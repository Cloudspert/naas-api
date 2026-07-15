# NaaS API — User Guide

**Namespace as a Service (NaaS)** lets you self-service OpenShift namespaces:
create them with resource quotas, list the ones you manage, change quotas, and
delete them (soft mark or force).

This guide covers **how to call the API**. For internals see
[DEVELOPER.md](DEVELOPER.md).

---

## Base URL & interactive docs

| Resource        | Path                |
|-----------------|---------------------|
| Namespaces      | `/api/v1/namespaces` |
| EgressIPs       | `/api/v1/egressips` |
| Swagger UI      | `/docs`             |
| ReDoc           | `/redoc`            |
| OpenAPI schema  | `/openapi.json`     |
| Liveness probe  | `/healthz` (public) |
| Readiness probe | `/readyz` (public)  |

In **Swagger UI** (`/docs`) click **Authorize** and enter your credentials. The
authorize control automatically matches the authentication module the server is
running (HTTP Basic by default), then every "Try it out" call is authenticated.

---

## Authentication

All `/api/v1` endpoints require authentication. The default module is **HTTP
Basic**. Send an `Authorization: Basic <base64(user:password)>` header:

```bash
curl -u admin:yourpassword https://<host>/api/v1/namespaces
```

Missing/invalid credentials return **401 Unauthorized** with a
`WWW-Authenticate` header.

---

## Resource quantities

Quota values are **Kubernetes quantity strings**:

| Dimension | Field     | Examples                | Maps to (ResourceQuota)                  |
|-----------|-----------|-------------------------|------------------------------------------|
| Memory    | `memory`  | `512Mi`, `4Gi`, `16Gi`  | `requests.memory` + `limits.memory`      |
| CPU       | `cpu`     | `500m`, `2`, `4`        | `requests.cpu` + `limits.cpu`            |
| Storage   | `storage` | `10Gi`, `50Gi`, `1Ti`   | `requests.storage`                       |

Every dimension is **optional** — send only the ones you want to set.

> **Bare numbers for memory/storage default to `Gi`.** Sending `memory: "8"`
> (or `8`) is treated as `8Gi`; `storage: 50` becomes `50Gi`. Values that already
> carry a unit (`8Gi`, `512Mi`, …) are left as-is. **CPU is never rewritten** — a
> bare number there means whole cores.

---

## Endpoints

### 1. Create a namespace — `POST /api/v1/namespaces`

Creates a namespace and applies a `ResourceQuota`.

**Request body**

| Field            | Type   | Required | Description                                              |
|------------------|--------|----------|----------------------------------------------------------|
| `name`           | string | yes      | Namespace name. RFC 1123 label: lowercase alphanumeric and `-`, ≤ 63 chars. |
| `limits.memory`  | string | no       | Memory quota.                                            |
| `limits.cpu`     | string | no       | CPU quota.                                               |
| `limits.storage` | string | no       | Storage request quota.                                   |
| `labels`         | object | no       | Extra labels for the namespace — **also inherited by its ResourceQuota**. |

```bash
curl -u admin:pw -X POST https://<host>/api/v1/namespaces \
  -H 'Content-Type: application/json' \
  -d '{"name":"team-a",
       "limits":{"memory":"8Gi","cpu":"4","storage":"50Gi"},
       "labels":{"env":"prod","team":"payments"}}'
```

> **Label key prefix.** If the server is configured with a label key prefix
> (`APP_LABEL_KEY_PREFIX`, e.g. `company.example.io`), your label keys are
> namespaced automatically: sending `"env": "prod"` stores
> `company.example.io/env: prod`. Keys that already contain a `/` are left as-is
> (Kubernetes allows only one prefix per key), and the server's own
> `managed-by` label is never prefixed. With no prefix configured, your keys are
> used verbatim.

**`201 Created`**

```json
{
  "message": "namespace created",
  "namespace": "team-a",
  "details": {
    "namespace": "team-a",
    "quota": {
      "requests.memory": "8Gi", "limits.memory": "8Gi",
      "requests.cpu": "4", "limits.cpu": "4",
      "requests.storage": "50Gi"
    }
  }
}
```

Errors: `409` if the namespace already exists, `422` for an invalid name/body.

---

### 2. List managed namespaces — `GET /api/v1/namespaces`

Returns namespaces carrying the server-configured management label
(`managed-by=naas-api` by default). Served from an in-memory **cache** that
refreshes periodically, so results may lag reality by up to the configured
refresh interval.

```bash
curl -u admin:pw https://<host>/api/v1/namespaces
```

**`200 OK`**

```json
{
  "items": [
    {
      "name": "team-a",
      "status": "Active",
      "labels": {"managed-by": "naas-api"},
      "marked_for_deletion_at": null
    }
  ],
  "count": 1,
  "cached_at": "2026-06-30T12:00:00+00:00"
}
```

`marked_for_deletion_at` is set when a namespace was soft-deleted (see below).
`cached_at` tells you how fresh the snapshot is.

---

### 3. Namespace status (existence) — `GET /api/v1/namespaces/{name}/status`

Checks the cluster **live** (not the cache) for the namespace. Returns `200`
in both cases — read the `exists` boolean; it is not an error when the namespace
is absent.

```bash
curl -u admin:pw https://<host>/api/v1/namespaces/team-a/status
```

**`200 OK`**

```json
{ "namespace": "team-a", "exists": true }
```

---

### 4. Update quotas — `PATCH /api/v1/namespaces/{name}/quota`

Updates the namespace's quota. **Only the dimensions you send change**; the
others are left intact. At least one dimension is required.

**Request body**

| Field            | Type   | Required          | Description       |
|------------------|--------|-------------------|-------------------|
| `limits.memory`  | string | at least one of   | New memory quota. |
| `limits.cpu`     | string | the three         | New CPU quota.    |
| `limits.storage` | string |                   | New storage quota.|

```bash
# Change ONLY memory; cpu/storage stay as they were.
curl -u admin:pw -X PATCH https://<host>/api/v1/namespaces/team-a/quota \
  -H 'Content-Type: application/json' \
  -d '{"limits":{"memory":"16Gi"}}'
```

**`200 OK`** — returns the applied quota. Errors: `404` if the namespace does
not exist, `422` if `limits` is empty.

---

### 5. Delete a namespace — `DELETE /api/v1/namespaces/{name}`

Two modes, controlled by the `force` query parameter (default `false`).

| `force`  | Behaviour                                                                                          |
|----------|----------------------------------------------------------------------------------------------------|
| `false`  | **Soft delete.** Adds an annotation `naas-api/marked-for-deletion-at=<UTC timestamp>`. Nothing is removed — the namespace is only *flagged*. |
| `true`   | **Force delete.** Enumerates and deletes every resource in the namespace (core **and** CRD-backed), then deletes the namespace itself. |

```bash
# Soft delete (mark only)
curl -u admin:pw -X DELETE https://<host>/api/v1/namespaces/team-a

# Force delete (irreversible)
curl -u admin:pw -X DELETE "https://<host>/api/v1/namespaces/team-a?force=true"
```

**Soft `200 OK`**

```json
{ "message": "namespace marked for deletion",
  "namespace": "team-a",
  "details": { "namespace": "team-a", "marked_for_deletion_at": "2026-06-30T12:00:00+00:00" } }
```

**Force `200 OK`**

```json
{ "message": "namespace force-deleted",
  "namespace": "team-a",
  "details": { "namespace": "team-a", "deleted_resources": ["ConfigMap/cm1", "Deployment/web"] } }
```

Errors: `404` if the namespace does not exist.

> ⚠️ **Force delete is irreversible** and removes everything in the namespace.
> Prefer a soft delete unless you are sure.

---

## EgressIP endpoints

Manage OpenShift `EgressIP` resources (cluster-scoped OVN-Kubernetes CRD that
pins the egress/source IP of selected pods). The API treats grouping purely as
**labels** — there is no built-in "ecosystem" concept; use whatever label key
you like (e.g. `ecosystem=payments`) in `namespace_labels`.

> ⚠️ EgressIP changes are **traffic-affecting** (external firewalls often
> allowlist these IPs). `namespace_labels` is **required and non-empty** — an
> empty namespace selector would match every namespace in the cluster.

### 6. Create an EgressIP — `POST /api/v1/egressips`

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `name` | string | yes | EgressIP name (RFC 1123 label). Cluster-unique. |
| `egress_ips` | string[] | yes | One or more valid IPs (IPv4/IPv6). |
| `namespace_labels` | object | yes (non-empty) | `namespaceSelector.matchLabels` — which namespaces. |
| `pod_labels` | object | no | `podSelector.matchLabels` — which pods (all if omitted). |
| `labels` | object | no | Extra metadata labels for later filtering. |

```bash
curl -u admin:pw -X POST https://<host>/api/v1/egressips \
  -H 'Content-Type: application/json' \
  -d '{
        "name": "payments-web",
        "egress_ips": ["192.168.1.100", "192.168.1.101"],
        "namespace_labels": {"ecosystem": "payments", "tier": "frontend"},
        "pod_labels": {"app": "web"},
        "labels": {"team": "payments"}
      }'
```

`201 Created` — returns the created EgressIP summary. The `namespace_labels` are
mirrored onto the object's metadata labels (alongside `managed-by=naas-api` and
any `labels`) so they are queryable in the list endpoint.

```json
{
  "name": "payments-web",
  "egress_ips": ["192.168.1.100", "192.168.1.101"],
  "namespace_selector": {"ecosystem": "payments", "tier": "frontend"},
  "pod_selector": {"app": "web"},
  "status": [],
  "labels": {"managed-by": "naas-api", "ecosystem": "payments", "tier": "frontend", "team": "payments"}
}
```

Errors: `409` if the name exists, `422` for invalid IPs / empty `namespace_labels` / bad name.

### 7. List EgressIPs — `GET /api/v1/egressips`

Optional `labels` query (`k=v,k2=v2`) filters on the object's metadata labels —
so "get by ecosystem" is just filtering on your ecosystem label.

```bash
curl -u admin:pw "https://<host>/api/v1/egressips?labels=ecosystem=payments"
curl -u admin:pw  https://<host>/api/v1/egressips            # all managed EgressIPs
```

```json
{ "items": [ { "name": "payments-web", "egress_ips": ["192.168.1.100"], "status": [...] } ], "count": 1 }
```

`status` echoes `.status.items` from the cluster (assigned node/IP), so you can
confirm the IPs were actually assigned.

### 8. Get an EgressIP — `GET /api/v1/egressips/{name}`

Returns a single EgressIP summary, or `404`.

### 9. Delete an EgressIP — `DELETE /api/v1/egressips/{name}`

```bash
curl -u admin:pw -X DELETE https://<host>/api/v1/egressips/payments-web
```

`200` on success, `404` if absent. Deleting changes pod egress routing.

---

## Status codes

| Code  | Meaning                                                |
|-------|--------------------------------------------------------|
| `200` | Success (list / get / update / delete).                |
| `201` | Namespace or EgressIP created.                         |
| `401` | Missing or invalid authentication.                     |
| `404` | Namespace / EgressIP not found.                        |
| `409` | Already exists (on create).                            |
| `422` | Validation error (bad name, empty quota / namespace_labels, invalid IP, malformed body). |
| `5xx` | Upstream Kubernetes API error.                         |

Error responses follow FastAPI's shape: `{"detail": "<message>"}`.
