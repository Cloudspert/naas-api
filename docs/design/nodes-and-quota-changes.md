# Design: node endpoints + delta quota updates + PVC quota

**Status:** Implemented — nodes (`services/nodes.py` + `node_cache.py`,
`api/nodes.py`), delta quota updates (`QuotaDelta`, `NamespaceManager.update_quota`),
and optional `pvc_count` + 1-core CPU floor. Decisions taken: delta in-place on
`PATCH .../quota`; CPU floor is a silent raise to 1; zero stays `"0"`.
**Author:** platform-team
**Date:** 2026-10-23
**Related:** [../DEVELOPER.md](../DEVELOPER.md), [../USER.md](../USER.md)

Four changes, grouped because the last three all touch the quota path:

1. **List nodes** — `GET /api/v1/nodes`.
2. **Get node** — `GET /api/v1/nodes/{name}` (labels, IPs, …).
3. **Quota update is now a delta** — the update input is *how much to add*
   (±), not the new absolute value.
4. **Optional PVC-count quota** — a new `pvc_count` dimension, only added when
   sent.

---

## 1. Nodes — list & get

Nodes are **cluster-scoped core** objects (`CoreV1Api.list_node` /
`read_node`), so this reuses the existing `K8sClient.core_v1` — no dynamic
client, no namespace.

### 1.1 `GET /api/v1/nodes` — list

Optional `?labels=k=v,k2=v2` filter (maps to the API server's
`label_selector`, same UX as `/egressips`). Auth required.

```json
{
  "items": [
    {
      "name": "worker-1",
      "ready": true,
      "roles": ["worker"],
      "internal_ip": "10.0.0.11",
      "labels": {"node-role.kubernetes.io/worker": "", "topology.kubernetes.io/zone": "eu-west-1a"}
    }
  ],
  "count": 1
}
```

`NodeSummary` is intentionally light (name, ready, roles, internal IP, labels)
so the list stays small on large clusters.

- **`ready`** — the `Ready` condition's status (`True` → `true`).
- **`roles`** — derived from `node-role.kubernetes.io/<role>` labels (the
  convention; there is no dedicated field).
- **`internal_ip`** — the `InternalIP` entry from `status.addresses`.

### 1.2 `GET /api/v1/nodes/{name}` — get

`200` with a fuller `NodeDetail`, `404` if absent.

```json
{
  "name": "worker-1",
  "ready": true,
  "roles": ["worker"],
  "unschedulable": false,
  "addresses": {"InternalIP": "10.0.0.11", "Hostname": "worker-1"},
  "labels": { "...": "..." },
  "annotations": { "...": "..." },
  "capacity": {"cpu": "8", "memory": "32Gi", "pods": "110"},
  "allocatable": {"cpu": "7800m", "memory": "30Gi", "pods": "110"},
  "kubelet_version": "v1.30.4",
  "os_image": "Red Hat Enterprise Linux CoreOS",
  "architecture": "amd64"
}
```

- **`addresses`** — the whole `status.addresses` collapsed to a `{type: address}`
  map (covers InternalIP, ExternalIP, Hostname — the "IP and so forth").
- **`capacity` / `allocatable`** — from `status.capacity` / `status.allocatable`.
- **`unschedulable`** — `spec.unschedulable` (cordon state).

### 1.3 Caching

Both node endpoints are **served from an in-memory cache** (`NodeCache`),
mirroring `NamespaceCache`:

- A background daemon thread refreshes the full node list every
  `APP_CACHE_REFRESH_INTERVAL_SECONDS` — the **existing global cache interval**
  (default `30`) that already drives `NamespaceCache`. No new node-specific
  setting; one knob tunes all caches.
- The cache stores the full node **detail** keyed by name. `GET /nodes` derives
  the light `NodeSummary`s from it; `GET /nodes/{name}` returns the cached
  `NodeDetail` (`404` when the name is not in the cache).
- `start()` warms it synchronously on lifespan startup (one `list_node`), then
  the thread takes over; `stop()` on shutdown. Same self-healing loop as the
  namespace cache (exceptions logged, the thread never dies).
- `/readyz` reports `warming` until **both** caches have populated once.
- Trade-off: a node cordoned / added / removed is visible only after the next
  refresh (≤ interval). Acceptable for this read-only view; lower the interval
  if fresher data is required.

### 1.4 Code & RBAC

- `app/services/nodes.py` — `NodeManager(k8s)` with `list(labels=None)` and
  `get(name)`; raises `NodeError` (an `ApiError`) → `404`/`500`. Read-only.
- `app/models/nodes.py` — `NodeSummary`, `NodeDetail`, `NodeListResponse`.
- `app/api/nodes.py` — the two routes (+ `get_node_manager` dep); included in
  `api_router`. Wire `node_manager` onto `app.state` in `main.lifespan`.
- Helm `ClusterRole`: add
  ```yaml
  - apiGroups: [""]
    resources: ["nodes"]
    verbs: ["get", "list", "watch"]
  ```

---

## 2. Quota update becomes a delta

### 2.1 The change

Today `PATCH /api/v1/namespaces/{name}/quota` takes **absolute** values and
merge-patches them into `spec.hard`. New behavior: the body is a **delta added
to the current value** (negative to subtract).

```
current quota: memory=8Gi, cpu=4
PATCH {"limits": {"memory": "2Gi", "cpu": "1"}}
result:        memory=10Gi, cpu=5
```

- **Create is unchanged** — `POST /namespaces` still sets absolute values.
  Only the update endpoint becomes additive.
- Only the dimensions present in the request change (unchanged property).
- The response still returns the **new effective** `spec.hard`.

### 2.2 Base value, per dimension

Read the current quota (`read_quota`) and take the base from `spec.hard`
(the current *desired* values we previously set):

| Dimension   | Base read from        | Written back to                         |
|-------------|-----------------------|-----------------------------------------|
| `memory`    | `requests.memory`     | `requests.memory` + `limits.memory`     |
| `cpu`       | `requests.cpu`        | `requests.cpu` + `limits.cpu`           |
| `storage`   | `requests.storage`    | `requests.storage`                      |
| `pvc_count` | `persistentvolumeclaims` | `persistentvolumeclaims`             |

A dimension missing from the current quota (or no quota object at all) has
**base 0** → the delta becomes its new value. So an update can introduce a
dimension that create didn't set.

### 2.3 Integer arithmetic (no quantity parsing needed)

Inputs are guaranteed to be **plain integers** — the caller validates this, so
we never receive `M`/`Mi` suffixes or fractional values. That removes the need
for Kubernetes quantity parsing entirely: every dimension is stored and added as
an integer in a fixed unit.

| Dimension   | Stored as     | Base read from           | Delta math                          |
|-------------|---------------|--------------------------|-------------------------------------|
| `memory`    | `<n>Gi`       | `requests.memory`        | strip `Gi` → int + delta → `<sum>Gi`|
| `storage`   | `<n>Gi`       | `requests.storage`       | same as memory                      |
| `cpu`       | `<n>` (cores) | `requests.cpu`           | int + delta, then floor at 1 (§2.6) |
| `pvc_count` | `<n>`         | `persistentvolumeclaims` | int + delta                         |

- memory & storage are **always integer `Gi`** — the existing bare-number → `Gi`
  rule already guarantees this on input, so the only unit in play is `Gi`.
  Reading the base just strips the `Gi` suffix.
- cpu is **integer cores** (no millicores), floored at 1 (§2.6).
- **No decimals, no `Mi`, no `parse_quantity`, no serialization helper** — it's a
  few lines of integer add in `apply_quota`.
- The bare-number → `Gi` rule is extended to accept a leading `-` for deltas
  (`"-2"` → `-2Gi`); cpu/pvc deltas are signed integers.

### 2.4 Negative / zero results

- Result **< 0** → `422` (`"memory would drop below zero"`). We never write a
  negative quota.
- Result **== 0** → the key is set to `"0"`. It is **not removed** — merge-patch
  cannot delete a key, and removal is out of scope for this endpoint.
- **CPU is the exception** to both rules above — it is floored at 1 core
  instead (§2.6).

### 2.6 CPU minimum (1-core floor)

Whenever a `cpu` value is **written** (create or the result of a delta) it is
**never less than 1 core**:

- **Create** — a `cpu` below `1` (e.g. `500m`, `0`) is raised to `1`.
- **Update** — if a delta would drop the result below `1`, it is set to `1`
  (not rejected, not `422`).

CPU is also **whole integer cores** (§2.3) — inputs are integers, so there are
no fractional cores to worry about; the 1-core floor is the only adjustment.

Applied after the arithmetic, in one place (`apply_quota`),
so there is a single enforcement point. The floor applies **only when cpu is
being set** — if `cpu` is omitted, no cpu quota key is added or touched.
(Alternative — reject sub-1 values with `422` — in open questions.)

### 2.5 Schema

A distinct delta model keeps the validators honest (deltas allow a leading `-`;
absolute create values do not):

- New `QuotaDelta` in `app/models/namespaces.py` — same fields as
  `ResourceLimits` plus `pvc_count`, but the memory/storage normalizer allows an
  optional sign, and at least one field must be set.
- `UpdateQuotaRequest.limits` becomes a `QuotaDelta`. (Field name stays
  `limits` to avoid a breaking rename; documented as a delta.)

> **Alternative considered:** reuse `ResourceLimits` for both and branch in the
> service. Rejected — "is this value absolute or a delta?" would depend on the
> call site, and negative values would silently be legal on create. A separate
> model makes the contract explicit.

---

## 3. Optional PVC-count quota

A new quota dimension capping the **number of PVCs** in the namespace →
`spec.hard.persistentvolumeclaims` (a count, not a size).

- `ResourceLimits` (create) and `QuotaDelta` (update) gain
  **`pvc_count: Optional[int]`**.
- **Only rendered when sent** — the `resourcequota.yaml.j2` template gets:
  ```jinja
  {% if pvc_count is not none %}
      persistentvolumeclaims: "{{ pvc_count }}"
  {% endif %}
  ```
  (`is not none`, not truthiness, so `pvc_count: 0` — "no PVCs allowed" — is
  still rendered.)
- It is a **plain integer**: no `Gi` suffix, and `apply_quota` must pass it to
  the template. `is_empty()` must also consider `pvc_count`.
- Delta arithmetic for it is plain integer addition (§2.3).

Example create:
```json
{"name":"team-a","limits":{"memory":"8Gi","cpu":"4","storage":"50Gi","pvc_count":10}}
```
→ `spec.hard` additionally carries `persistentvolumeclaims: "10"`.

---

## 4. Code structure (layered layout)

```
app/
  models/
    namespaces.py   + pvc_count on ResourceLimits; new QuotaDelta; UpdateQuotaRequest.limits -> QuotaDelta
    nodes.py        NEW: NodeSummary, NodeDetail, NodeListResponse
  services/
    namespaces.py   apply_quota renders pvc_count + 1-core floor; update_quota adds integer deltas inline (strip Gi / int math — no quantity helper)
    nodes.py        NEW: NodeManager (list/get), NodeError
    node_cache.py   NEW: NodeCache (background refresh, mirrors NamespaceCache; reuses the global interval)
  api/
    namespaces.py   update route now passes a delta
    nodes.py        NEW: list/get routes (served from NodeCache)
    __init__.py     include nodes router
  core/config.py    (no new setting — NodeCache reuses cache_refresh_interval_seconds / APP_CACHE_REFRESH_INTERVAL_SECONDS)
  templates/
    resourcequota.yaml.j2   + persistentvolumeclaims block
  main.py           wire NodeManager + NodeCache onto app.state; readyz checks both caches
core/errors.py      NodeError subclasses ApiError (like NamespaceError/EgressIPError)
```

---

## 5. Testing

- **Delta arithmetic** (in `apply_quota`) — `memory 8Gi + 2 → 10Gi`,
  `storage 50Gi + 10 → 60Gi`, `cpu 4 + 1 → 5`, `cpu 4 - 1 → 3`, `pvc 10 + 5 → 15`;
  delta onto a dimension the quota didn't have (base 0); cpu result below 1 →
  floored to `1`; memory/storage negative result → `422`; exact-zero → `"0"`.
  All integers, single `Gi` unit — no `Mi`/decimal cases to test.
- **`NodeManager` + `NodeCache`** — fake `core_v1.list_node` / `read_node`
  (extend `tests/fakes.py` with a node store + `_Node` carrying
  addresses/conditions/roles); `refresh()` populates the cache (no thread in
  tests); list, label filter, get summary/detail, `404` for an uncached name.
- **Quota delta** — create then PATCH `+2Gi` → `10Gi`; PATCH a dimension the
  quota didn't have (base 0); PATCH memory negative below zero → `422`; PATCH cpu
  down to below 1 → floored to `1`.
- **PVC quota** — create with `pvc_count`; `pvc_count: 0` renders `"0"`; omitted
  → key absent; delta add.
- **CPU floor** — create with `cpu: "500m"` → quota `cpu: "1"`.
- **Functional** — `/nodes` list/get/404/auth-401; delta PATCH over the HTTP
  stack.
- Run via `docker compose run --rm tests`.

---

## 6. Docs to update (when implemented)

- **USER.md** — new Nodes section; rewrite the Update-quota section to explain
  **delta** semantics (big behavior change — call it out clearly); add
  `pvc_count` to the quota fields.
- **DEVELOPER.md** — directory map (nodes service/model/router, `quantity.py`),
  §5 services (NodeManager, delta `update_quota`), §8 routes table.
- **README** — endpoint table rows for `/nodes`; link this doc.

---

## 7. Open questions

- **Delta is a behavior change on an existing endpoint.** Acceptable to change
  in place, or should delta live on a new path (e.g.
  `POST /{name}/quota:add`) and leave the absolute `PATCH`? (Design assumes
  in-place change.)
- **Zero removal** — leave at `"0"` (chosen) or support real removal via a
  JSON-patch / full re-PUT of `hard`?
- **Node list filtering** — label selector only (chosen), or also by role /
  ready state?
- **CPU floor** — silently raise sub-1 values to `1` (chosen) or reject with
  `422`? Silent floor is friendlier but hides caller intent in logs.
- **Node cache interval** — reuses the global `cache_refresh_interval_seconds`
  (chosen — one knob for all caches). Split into a per-cache setting only if
  nodes and namespaces need different cadences.

> Inputs are integers by contract (validated upstream), so earlier questions
> about quantity serialization and rounding no longer apply — everything is
> integer `Gi` / cores / counts.

---

## 8. Phased plan

1. **Nodes** (read-only, self-contained, no quota risk): `NodeManager` +
   `NodeCache` + models + routes + RBAC + readyz wiring.
2. **PVC quota + CPU floor** (additive, low risk): `pvc_count` on create +
   template; the 1-core floor in `apply_quota`.
3. **Delta updates** (behavior change): `QuotaDelta`, `update_quota` rework
   (inline integer add, cpu floor), `pvc_count` delta. No quantity helper —
   inputs are integers.
