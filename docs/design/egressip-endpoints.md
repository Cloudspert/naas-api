# Design: EgressIP endpoints

**Status:** Implemented (create / delete / get / list) — see `app/egressips.py`,
`app/egress_api.py`, `app/templates/egressip.yaml.j2`. Decision on the open
question below: `namespace_labels` **are** mirrored onto metadata. JSON fields
use snake_case (`egress_ips`, `namespace_labels`, `pod_labels`) to match the
rest of the API.
**Author:** platform-team
**Related:** [../DEVELOPER.md](../DEVELOPER.md), [../USER.md](../USER.md)

---

## 1. Context & goal

Add API endpoints to **create / delete / get** OpenShift `EgressIP` resources.
Each EgressIP selects the namespaces and pods it applies to via labels, and
those label inputs must be settable through the API at create time. `GET` lists
EgressIPs filtered by labels.

**The API does not model "ecosystem" itself.** "Ecosystem" is just one label the
caller chooses to use (e.g. `ecosystem=payments`). The API treats it like any
other label — the caller owns the key and value. This keeps the grouping scheme
fully in the user's hands and out of the API's config.

**Goals**
- CRUD-lite (`create`, `delete`, `get`/`list`) for EgressIP via the API.
- Set namespace-selector and pod-selector labels on create.
- List filtered by an arbitrary label selector (a caller may filter by their own
  `ecosystem` label, or anything else).

**Non-goals**
- Any special handling of an "ecosystem" concept (it's just labels).
- Managing egress-assignable node labels or the OVN network config.
- IP address pool management / allocation (IPs are supplied by the caller).
- Guaranteeing an IP is free/routable — that's the cluster's job; we surface status.

## 2. Background: what an EgressIP is

`EgressIP` (`apiVersion: k8s.ovn.org/v1`, kind `EgressIP`) is a **cluster-scoped**
CRD from OVN-Kubernetes. It pins the **source IP (SNAT)** of egress traffic for
selected pods to one or more fixed IPs, so external systems see a stable IP.

```yaml
apiVersion: k8s.ovn.org/v1
kind: EgressIP
metadata:
  name: payments-web
spec:
  egressIPs: ["192.168.1.100", "192.168.1.101"]
  namespaceSelector:            # REQUIRED - which namespaces
    matchLabels: { ecosystem: payments }
  podSelector:                  # OPTIONAL - which pods (all if omitted)
    matchLabels: { app: web }
status:
  items:
    - node: worker-1
      egressIP: 192.168.1.100
```

Cluster prerequisites (assumed, not managed here): the EgressIP CRD is installed,
nodes are labeled `k8s.ovn.org/egress-assignable`, and the IPs are valid for the
node subnet.

> **⚠️ Safety pin:** an **empty** `namespaceSelector` (`matchLabels: {}`) matches
> **every namespace in the cluster**. Because the API no longer injects an
> ecosystem label, we instead **require `namespaceLabels` to be non-empty** on
> every create — see §5.

## 3. Label model — two planes

Two **distinct** label planes. Keeping them separate is the crux of the design:

| Plane | Where it lives | Purpose | API input |
|-------|----------------|---------|-----------|
| **Selector labels** | `spec.namespaceSelector`, `spec.podSelector` | which namespaces/pods the egress applies to | `namespaceLabels` (required), `podLabels` (optional) |
| **Index labels** | `metadata.labels` on the EgressIP object | how `GET` finds/filters EgressIPs | mirrored `namespaceLabels` + optional `labels` (+ auto `managed-by`) |

- `namespaceSelector.matchLabels` = the caller's `namespaceLabels` verbatim.
  (If the caller uses an `ecosystem` label, they simply include it here.)
- `podSelector.matchLabels` = `podLabels` (optional; omitted → all pods).
- `metadata.labels` = `managed-by=naas-api`, **plus a mirror of
  `namespaceLabels`**, plus any extra `labels` supplied.

**Why mirror `namespaceLabels` into `metadata`:** so `GET` can filter by the same
labels used for selection (including the caller's `ecosystem` label) **without
the API knowing what any of them mean**. The caller sets `ecosystem=payments`
once in `namespaceLabels`; it drives selection *and* becomes queryable in `GET`.
(Mirroring is a design choice — see open questions.)

## 4. API

Base path `/api/v1/egressips`. All endpoints require auth (same guard as the
namespace endpoints).

### 4.1 Create — `POST /api/v1/egressips`

```json
{
  "name": "payments-web",                    // required (or generated - see §5)
  "egressIps": ["192.168.1.100", "192.168.1.101"],
  "namespaceLabels": { "ecosystem": "payments", "tier": "frontend" }, // required, non-empty
  "podLabels": { "app": "web" },             // optional pod selector
  "labels": { "team": "payments" }           // optional extra metadata/index labels
}
```

Renders (via a Jinja2 template, consistent with the namespace/quota manifests):

```yaml
apiVersion: k8s.ovn.org/v1
kind: EgressIP
metadata:
  name: payments-web
  labels:                       # managed-by + mirrored namespaceLabels + extra labels
    managed-by: naas-api
    ecosystem: payments
    tier: frontend
    team: payments
spec:
  egressIPs: ["192.168.1.100", "192.168.1.101"]
  namespaceSelector:
    matchLabels: { ecosystem: payments, tier: frontend }
  podSelector:
    matchLabels: { app: web }
```

`201 Created`; `409` if the name already exists; `422` on validation failure.

### 4.2 Delete — `DELETE /api/v1/egressips/{name}`

Deletes the EgressIP. `200` on success, `404` if absent. Deleting changes pod
egress routing (SNAT) — logged as an event (§10).

### 4.3 Get / list — `GET /api/v1/egressips`

Query param: `labels` (optional, `k=v,k2=v2`). Builds a **metadata** label
selector `managed-by=naas-api[,<labels>]` and lists cluster-scoped EgressIPs.
Filtering "by ecosystem" is just filtering by the caller's ecosystem label:

```
GET /api/v1/egressips?labels=ecosystem=payments
GET /api/v1/egressips?labels=ecosystem=payments,tier=frontend
GET /api/v1/egressips                       # all managed EgressIPs
```

```json
{
  "items": [
    {
      "name": "payments-web",
      "egressIps": ["192.168.1.100", "192.168.1.101"],
      "namespaceSelector": { "ecosystem": "payments", "tier": "frontend" },
      "podSelector": { "app": "web" },
      "status": [ { "node": "worker-1", "egressIP": "192.168.1.100" } ],
      "labels": { "managed-by": "naas-api", "ecosystem": "payments", "tier": "frontend", "team": "payments" }
    }
  ],
  "count": 1
}
```

`status` echoes `.status.items` so callers can see whether the IPs were actually
assigned. Also expose `GET /api/v1/egressips/{name}` for a single object.

## 5. Validation

- `namespaceLabels` — **required, non-empty** (the safety pin from §2; guarantees
  a non-empty `namespaceSelector`), valid label keys/values.
- `egressIps` — required, ≥1, each a valid IP (`ipaddress` module; IPv4 and IPv6).
- `name` — required, valid RFC 1123 subdomain. (Alternative: optional with a
  generated name, e.g. a short hash of the selectors — see open questions.)
- `podLabels` / `labels` — optional maps of valid label key/values.

## 6. Configuration additions

| Setting | Env var | Default | Purpose |
|---------|---------|---------|---------|
| `egressip_api_version` | `APP_EGRESSIP_API_VERSION` | `k8s.ovn.org/v1` | CRD group/version (configurable per platform) |
| `egressip_kind` | `APP_EGRESSIP_KIND` | `EgressIP` | CRD kind |

Reuses the existing `managed_label` for the `managed-by` metadata label. **No
ecosystem-related config** — the API is unaware of the concept.

## 7. Code structure (flat, matching the current layout)

```
app/
  egressips.py       EgressIPManager: create / delete / get / list (dynamic client + render)
  schemas.py         + CreateEgressIPRequest, EgressIPSummary, EgressIPListResponse
  api.py             + egressip routes (or a sibling egress_api.py router)
  templates/
    egressip.yaml.j2 the EgressIP manifest
  config.py          + the settings in §6
```

- **`EgressIPManager`** mirrors `NamespaceManager`, but uses the **dynamic
  client** (EgressIP is a CRD, cluster-scoped):
  - create: `dynamic.resources.get(api_version, kind).create(body=manifest)`
  - delete: `.delete(name=...)` (no namespace — cluster-scoped)
  - list: `.get(label_selector=...)`
  - get: `.get(name=...)`
- An `EgressIPError(message, status_code)` (or a shared base with `NamespaceError`)
  translates `ApiException` 404/409 to HTTP status, same pattern as today.
- Manifest rendered from `egressip.yaml.j2` so its shape stays editable.

**Caching:** not needed initially — EgressIP lists are on-demand and low-volume.
If GET volume grows, add a labelled cache like the namespace one (out of scope now).

## 8. RBAC (Helm `ClusterRole`)

```yaml
- apiGroups: ["k8s.ovn.org"]
  resources: ["egressips"]
  verbs: ["get", "list", "watch", "create", "delete", "patch"]
```

Cluster-scoped, so no namespace restriction is possible. Add to
`helm/naas-api/templates/rbac.yaml`; note it in the security review.

## 9. Dependency: namespaces must carry the selector labels

An EgressIP only applies to namespaces that actually have the labels in
`namespaceSelector`. Labelling namespaces is **the caller's responsibility** —
consistent with "the user manages the key on labels." No API involvement is
required; document that namespaces must be labelled to match.
(Optionally, the existing `POST /api/v1/namespaces` already accepts labels via
templating and could set them, but that's the caller's choice, not something
this feature enforces.)

## 10. Observability

Emit stdout events like the namespace ops:
`event=egressip_created name=… ips=… namespaceLabels=…`,
`event=egressip_deleted name=…`. Creation/deletion changes real egress routing,
so these events are the audit trail.

## 11. Testing

- Extend `tests/fakes.py` `FakeDynamic` to support cluster-scoped `egressips`:
  create (409 on dup), get by name (404), list by label selector, delete.
- Unit: `EgressIPManager` create/delete/get/list + IP/name validation +
  non-empty `namespaceLabels` guard.
- Functional: `POST` → object created with correct selectors + mirrored metadata
  labels; `GET ?labels=` filters correctly; `DELETE` 200/404; auth 401.

## 12. Security & operational notes

- **Non-empty `namespaceLabels` guard** (§2/§5) is the most important safety
  property — it prevents an EgressIP that matches every namespace.
- Two EgressIPs assigning the **same** IP, or overlapping pod selectors, can
  conflict — detection is out of scope; document and log.
- EgressIP changes are **traffic-affecting** (external firewalls often allowlist
  these IPs). Treat create/delete as privileged, audited operations.
- IP-family and pool correctness aren't fully checkable here — surface
  `.status` so operators can confirm assignment.

## 13. Open questions

- **Mirror `namespaceLabels` into `metadata`** (so `GET` can filter by the same
  labels, including the caller's ecosystem label) — chosen here; confirm. The
  alternative is to only stamp the explicit `labels` field and have callers
  repeat their ecosystem label in both `namespaceLabels` and `labels`.
- **Name** — required vs. generated (hash of selectors) to allow many EgressIPs
  without name clashes.
- **IPv6 / dual-stack** — validate/allow both? (default: allow any valid IP.)
- **api_version pinning** — fixed default vs. discovery, for non-OVN platforms.

## 14. Phased plan

1. **Read-only** `GET`/list (safe, no cluster mutation) + config + RBAC (get/list).
2. **Create + Delete** + template + validation + events + full RBAC.
3. **Hardening** — status surfacing, conflict warnings.
