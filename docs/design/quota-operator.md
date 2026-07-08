# Design: Quota Operator (`ManagedNamespace`)

**Status:** Draft / design-only (no implementation)
**Author:** platform-team
**Related:** [../DEVELOPER.md](../DEVELOPER.md), [../USER.md](../USER.md)

---

## 1. Problem

A `ResourceQuota` counts the requests/limits of **every non-terminal pod** in a
namespace. A Deployment rolling update with the default `maxSurge > 0` (and
OpenShift `DeploymentConfig` surges by default) creates **new pods before** the
old ones terminate. If steady-state usage already fills the quota, the surge
pods are rejected and the rollout stalls.

The hard constraint: **Kubernetes cannot distinguish a "rollout surge" pod from
a normal pod** — they share a pod template. So a plain `ResourceQuota` cannot
express *"cap steady-state at X, but allow temporary bursting to X+Δ only during
a genuine rollout."*

We want tenants to roll out successfully **without** being able to permanently
run above their normal-run quota.

## 2. Goals / Non-goals

**Goals**
- Keep the enforced steady-state cap at the "normal run" quota.
- Allow rollouts to proceed (optionally with zero availability dip).
- Prevent tenants from turning temporary surge headroom into permanent capacity.
- Provide a declarative, self-healing, GitOps-friendly platform API.
- Fold namespace lifecycle + quota + limits + surge policy under one object.

**Non-goals**
- Replacing the Kubernetes scheduler or ResourceQuota accounting.
- Progressive-delivery features (canary/blue-green) — that's Argo Rollouts.
- Cross-namespace quota borrowing / cohorts — that's Kueue.

## 3. Decision summary

Introduce a cluster-scoped CRD **`ManagedNamespace`** and an **operator** that
reconciles it into a Namespace + ResourceQuota + LimitRange, and (optionally)
lends quota for the duration of a genuine rollout.

Two structural principles drive the design:

1. **Single writer.** The operator is the *only* writer of the ResourceQuota.
   The API and users express intent; the operator converges. This removes the
   multi-writer race that a standalone surge controller would create.

2. **Surge is derived from live truth, not a ledger.**
   ```
   effectiveQuota = baseline(spec) + Σ surgeCost(deployments currently mid-rollout)
   ```
   The surge term is recomputed every reconcile from observed rollout state, so
   there is no persisted bump-ledger to corrupt — crash-safe by construction. A
   stuck rollout is bounded by a policy TTL, not by controller memory.

## 4. The CRD (`ManagedNamespace`)

Cluster-scoped — it must create a Namespace (cluster-scoped); a namespaced CR
would be a chicken/egg. `metadata.name == namespace name` (1:1 mapping).

```yaml
apiVersion: naas.example.io/v1alpha1
kind: ManagedNamespace            # short name: mns
metadata:
  name: team-a                    # == the namespace name
spec:
  quota:                          # the "normal run" baseline
    memory: 8Gi
    cpu: "4"
    storage: 50Gi
  limits:                         # optional LimitRange defaults
    defaultRequest: { cpu: 100m, memory: 128Mi }
  surgePolicy:
    mode: Dynamic                 # None | MaxSurgeZero | Dynamic
    maxSurgeFactor: "0.25"
    ttlSeconds: 900               # cap how long a rollout may borrow
    dimensions: [memory, cpu]     # storage usually excluded from surge
  deletionPolicy:
    mode: Soft                    # Soft (annotate) | Force (cascade)
status:
  phase: Ready                    # Pending|Provisioning|Ready|Degraded|Terminating
  observedGeneration: 7
  baselineQuota:  { memory: 8Gi,  cpu: "4", storage: 50Gi }
  effectiveQuota: { memory: 10Gi, cpu: "5", storage: 50Gi }   # baseline + live surge
  activeSurges:
    - deployment: web
      amount: { memory: 2Gi, cpu: "1" }
      expiresAt: "2026-07-02T14:30:00Z"
  conditions:
    - { type: Ready,        status: "True" }
    - { type: QuotaApplied, status: "True" }
    - { type: SurgeActive,  status: "True", reason: RolloutInProgress }
```

**Notes**
- `spec.quota` is the source of truth for the baseline; `status.effectiveQuota`
  / `activeSurges` are *derived* views for visibility — never authoritative.
- `deletionPolicy` folds the existing soft/force delete semantics into the CR.
- `surgePolicy.mode`:
  - `None` — plain quota; rollouts may stall (today's behavior).
  - `MaxSurgeZero` — rely on the Deployment mutator to force `maxSurge: 0`
    (no borrowing needed; brief availability dip; needs ≥2 replicas for
    zero-downtime).
  - `Dynamic` — lend quota during rollout + earmark webhook.
- `additionalPrinterColumns`: `kubectl get mns` → `PHASE / BASELINE / EFFECTIVE /
  SURGE / AGE`.
- Ship `v1alpha1`; plan a conversion webhook to `v1beta1`; do not ship `v1` early.

## 5. Architecture

One manager process hosts several controllers and the admission webhooks:

```
                       ┌───────────────────────── operator (manager) ─────────────────────────┐
 FastAPI ── writes ──▶ │  ManagedNamespace controller                                          │
 (thin CR client)      │     watch mns ─▶ reconcile ─▶ Namespace + ResourceQuota + LimitRange   │
                       │                                   ▲ (sole writer)                       │
 user rollout ───────▶ │  Surge controller                 │                                     │
 (Deployment edit)     │     watch Deploy/RS in managed ns ─┘  effective = baseline + live surge  │
                       │                                                                         │
                       │  Admission webhooks (same manager):                                     │
                       │     • defaulting/validating  ManagedNamespace                           │
                       │     • mutating   Deployment  (maxSurge per policy)                      │
                       │     • validating Pod         (earmark surge headroom — Dynamic mode)    │
                       │     • conversion             (when >1 CRD version)                      │
                       └──────────────────────────────────────────────────────────────────────────┘
```

### 5.1 ManagedNamespace controller (level-triggered, idempotent)

Each reconcile computes desired state from `spec` + observed truth and applies
it — never edge-triggered, always convergent:

1. Fetch CR. If `deletionTimestamp` is set → run **finalizer** teardown honoring
   `deletionPolicy` (Soft = annotate & leave; Force = cascade delete resources,
   then remove the namespace), then drop the finalizer. Otherwise ensure the
   finalizer is present.
2. Ensure the Namespace exists and carries `managed-by=naas-api` + propagated
   labels.
3. `baseline = spec.quota`; `surge = live surge total` (from the surge
   component); `effective = baseline + surge`.
4. **Server-side apply** the ResourceQuota to `effective` and the LimitRange to
   spec. This yields **free drift correction** — a user hand-editing the quota is
   reverted next reconcile, because the operator is the sole writer.
5. Update `status` (conditions, phase, effectiveQuota, activeSurges,
   observedGeneration); requeue on resync / watch events.

### 5.2 Surge controller (Dynamic mode)

- Watches Deployments/DeploymentConfigs/ReplicaSets **filtered to managed
  namespaces** (never the whole cluster).
- Detects an in-flight rollout: `generation > status.observedGeneration`,
  `updatedReplicas < replicas`, or >1 ReplicaSet with `replicas > 0`.
- Computes surge cost = pod-template requests/limits × surge count, capped by
  `surgePolicy` and `ttlSeconds`.
- Does **not** write the quota itself — it triggers a reconcile of the owning
  `mns` and feeds the single-writer.
- On completion (only the new RS has replicas; `updated == replicas ==
  available`) the term drops; the next reconcile restores baseline — old pods are
  gone by then, so usage ≤ baseline.

### 5.3 The abuse window and the earmark webhook

While the quota is elevated, a tenant could fill the headroom with *unrelated*
steady-state pods and make it semi-permanent (lowering `hard` below current use
does not evict). Closed by a **validating Pod webhook** that, while surge is
active, only admits pods carrying the rolling Deployment's **new
pod-template-hash** — physically reserving the headroom for that rollout.

(Rejected alternative: a second `scopeSelector`-scoped quota + a mutating pod
label. Elegant, but pod labels outlive the rollout window, so the completion
transition — winning pods must shed the label to start counting under the
baseline quota before the surge quota is deleted — is racy. Prefer bump +
earmark.)

## 6. Integration with the existing FastAPI

The API stops mutating cluster objects directly and becomes a **CR client**:

| Endpoint        | Today                       | Under the operator                              |
|-----------------|-----------------------------|-------------------------------------------------|
| create          | create Namespace + Quota    | create `ManagedNamespace` CR                    |
| update quota    | patch ResourceQuota         | patch `mns.spec.quota`                          |
| soft delete     | annotate namespace          | `mns.spec.deletionPolicy=Soft` (or delete w/ soft finalizer) |
| force delete    | cascade delete              | delete CR → finalizer force-cleans              |
| status (exists) | read namespace              | read CR `.status` / conditions                  |
| list            | cache of namespaces         | watch / list CRs                                |

Benefits: requests become fast and retry-free (write intent, return);
convergence and self-healing move to the operator. **RBAC must forbid the API
SA from writing ResourceQuotas** — otherwise the multi-writer problem returns.

## 7. Migration / brownfield adoption

Namespaces created by today's imperative API must be **adopted**, not recreated:

1. One-time backfill job generates a `ManagedNamespace` CR per existing managed
   namespace (reading current quota into `spec.quota`).
2. Label/annotate existing Namespace + ResourceQuota so the operator adopts them
   (matching names → server-side apply updates in place, no recreate).
3. Cutover: revoke the API SA's direct quota-write RBAC. From here, the operator
   is the only writer.

This migration is the most common failure point for such projects — plan and
rehearse it.

## 8. RBAC / security

The operator SA is broad and privileged — **requires a security review**:

- `namespaces`: get/list/watch/create/patch/update/delete
- `resourcequotas`, `limitranges`: get/list/create/patch/update/delete
- `deployments`, `replicasets`, `deploymentconfigs`, `pods`: get/list/watch
- CRD group `managednamespaces` + `/status` + finalizers: full
- `events`: create; `leases`: for leader election
- existing force-delete wildcard (`*/*` get/list/delete), if retained

## 9. Operational concerns

- **Webhook availability / `failurePolicy`.** The Deployment mutator and Pod
  earmark webhook sit in the request path. Scope with `namespaceSelector`; pick
  Fail vs Ignore per webhook deliberately (a down earmark webhook must not wedge
  all pod creation cluster-wide).
- **Certs & rotation:** cert-manager (or a rotation sidecar).
- **Leader election** if the manager runs >1 replica.
- **Ownership/GC caveat:** cross-scope ownerReferences (namespaced child ↔
  cluster-scoped owner) are finicky — rely on **finalizers** for ordered
  teardown, not GC alone. Beware namespaces stuck in `Terminating` on their own
  resources.
- **HPA interaction:** replicas changing mid-rollout perturb the surge-cost math
  and reopen the abuse window — decide a policy (freeze/relax) explicitly.
- **Scale:** shared informers, rate-limited workqueue, label-scoped watches;
  never watch all Deployments/Pods cluster-wide from the surge controller.
- **Drift correction is a feature:** since the operator is the sole writer,
  reconcile intentionally overwrites manual edits to the managed quota.

## 10. Packaging / framework

- **operator-sdk / kubebuilder (Go)** — best for typed CRDs, webhooks,
  conversion, and OLM bundling. Recommended for the real build.
- **kopf (Python)** — stays in the team's stack for an alpha; weaker CRD codegen,
  conversion, and OLM support.
- **OpenShift distribution:** native path is **OLM + a ClusterServiceVersion**
  (OperatorHub, managed upgrades, generated RBAC). A Helm chart is fine for
  earlier phases.

## 11. Testing strategy

- **Unit:** reconcile is a pure function of (spec, observed) → desired; test the
  quota math, surge-cost calc, and state transitions directly.
- **Integration:** `envtest` / `kind` — provision a CR, assert Namespace + Quota
  converge; drive a rollout, assert effective quota rises then restores.
- **Chaos:** kill the manager mid-rollout and assert quota self-heals to baseline
  (validates the "no ledger" property).
- **Webhook:** admission review unit tests + e2e that surge headroom is only
  consumable by the rolling Deployment's new-hash pods.

## 12. Phased rollout (recommended sequencing)

C is a lot at once. Ship value incrementally; each step stands alone:

1. **Phase 0 — Component A.** Mutating webhook (or Kyverno policy) forcing
   `maxSurge: 0` in managed namespaces. Immediate relief, near-zero risk.
2. **Phase 1 — CRD + provisioning controller.** `ManagedNamespace` +
   reconcile of Namespace/Quota/LimitRange; API writes CRs; drift correction;
   backfill/adoption of existing namespaces.
3. **Phase 2 — Dynamic surge.** Surge sub-controller + earmark Pod webhook —
   **only if** true zero-dip surge under a hard cap proves to be a real
   requirement.

## 13. Alternatives considered

- **Component A only (`maxSurge: 0`).** Simplest; solves the cap-vs-rollout
  tension with a brief availability dip. If that dip is acceptable, C is overkill.
- **Standalone dynamic quota controller (Component B).** Same surge logic without
  the CRD — but reintroduces the multi-writer problem and lacks a declarative
  home. C subsumes it.
- **Buy instead of build:** Kyverno/Gatekeeper/VAP (= Phase 0), Argo Rollouts
  (owns surge within a budget), Kueue/ElasticQuota (cohort quota borrowing —
  conceptually the same "lend" idea, already battle-tested). Evaluate before
  committing to Phase 2.

## 14. Open questions

- Exact HPA-during-rollout policy (freeze vs. proportional relax).
- `failurePolicy` choice per webhook (availability vs. correctness).
- Whether `storage` is ever eligible for surge (default: no).
- Go (operator-sdk) vs Python (kopf) for the first real build.
- OLM bundle now, or Helm until Phase 2?
