# NaaS API — Developer Guide

A line-by-line tour of the codebase: every module, class, and function, how a
request flows end to end, and how to extend each layer. For *calling* the API
see [USER.md](USER.md).

---

## 1. Tech choices

- **FastAPI** — request validation via Pydantic, automatic OpenAPI/Swagger, and
  dependency injection (used for the auth guard). Chosen over Flask for the
  built-in validation and docs.
- **kubernetes** Python client — typed `CoreV1Api` for namespaces/quotas plus a
  `DynamicClient` for enumerating arbitrary (CRD) resources on force-delete.
- **Jinja2 + PyYAML** — managed manifests are rendered from templates so their
  shape is editable without code changes.
- **pydantic-settings** — 12-factor env-var configuration.

---

## 2. Directory map

```
app/
  main.py              FastAPI app, lifespan, logging, OpenAPI/Swagger wiring
  core/
    config.py          env-driven Settings (cached singleton)
    errors.py          ApiError base (status_code -> HTTP status)
  auth/
    base.py            Principal, AuthError, BASIC_CHALLENGE
    basic.py           BasicAuth
    __init__.py        build_auth() + AUTH_MODULES (re-exports base)
  k8s/
    client.py          K8sClient: SA/kubeconfig loader, CoreV1 + DynamicClient
    render.py          render_manifest() (Jinja2 -> dict)
  services/
    namespaces.py      NamespaceManager: create / update-quota / mark / force-delete
    egressips.py       EgressIPManager: create / delete / get / list (dynamic client)
    cache.py           NamespaceCache: labelled-namespace cache + refresher thread
  models/
    common.py          NAME_RE, MessageResponse
    namespaces.py      namespace request/response models
    egressips.py       EgressIP request/response models
    __init__.py        re-exports all models
  api/
    deps.py            get_manager / get_cache / require_auth / run
    namespaces.py      namespace routes
    egressips.py       EgressIP routes
    __init__.py        api_router aggregating both route modules
  templates/*.j2       Namespace + ResourceQuota + EgressIP manifests
tests/                 unit + functional tests (see §11)
helm/naas-api/         chart (see §12)
Dockerfile             pip-only image (see §13)
```

Layered grouping: `core` (config/errors) ← `k8s` / `models` ← `services` /
`auth` ← `api` ← `main`. Packages re-export their public names, so callers use
tidy paths (`from app.models import …`, `from app.auth import build_auth`).

---

## 3. Configuration — `app/core/` (`config.py`, `errors.py`)

`Settings(BaseSettings)` reads `APP_`-prefixed env vars (and an optional
`.env`). Fields:

| Field | Env var | Default | Notes |
|-------|---------|---------|-------|
| `app_name` | `APP_APP_NAME` | `naas-api` | |
| `log_level` | `APP_LOG_LEVEL` | `INFO` | |
| `in_cluster` | `APP_IN_CLUSTER` | `True` | `False` + `kubeconfig_path` for local dev |
| `kubeconfig_path` | `APP_KUBECONFIG_PATH` | `None` | |
| `namespace_label_selector` | `APP_NAMESPACE_LABEL_SELECTOR` | `managed-by=naas-api` | filter for the list endpoint |
| `cache_refresh_interval_seconds` | `APP_CACHE_REFRESH_INTERVAL_SECONDS` | `30` | background refresh period |
| `deletion_annotation_key` | `APP_DELETION_ANNOTATION_KEY` | `naas-api/marked-for-deletion-at` | written on soft delete |
| `template_dir` | `APP_TEMPLATE_DIR` | `app/templates` | |
| `managed_label` | `APP_MANAGED_LABEL` | `managed-by=naas-api` | stamped on created resources |
| `egressip_api_version` | `APP_EGRESSIP_API_VERSION` | `k8s.ovn.org/v1` | EgressIP CRD group/version |
| `egressip_kind` | `APP_EGRESSIP_KIND` | `EgressIP` | EgressIP CRD kind |
| `auth_module` | `APP_AUTH_MODULE` | `basic` | selects the active auth module |
| `basic_auth_users` | `APP_BASIC_AUTH_USERS` | `{}` | JSON map `{"user":"pass"}` |

Key code details:
- `parse_users` (a `field_validator(mode="before")`) lets `basic_auth_users`
  arrive as a **JSON string** from the env var and parses it into a dict.
- `managed_label_key` / `managed_label_value` split `managed_label` on `=` so
  templates can place the label as `key: "value"`.
- `template_dir` defaults to `app/templates` via `Path(__file__).resolve().parents[1]`
  (config lives in `app/core/`, templates one level up).
- `get_settings()` is `@lru_cache`d → a process-wide singleton. **Tests call
  `get_settings.cache_clear()`** after changing env so they observe fresh
  values.

`errors.py` defines `ApiError(message, status_code)` — the shared base for
`NamespaceError` and `EgressIPError`, mapped to an HTTP status by the router
`run()` helper (§8).

---

## 4. Models — `app/models/`

Pydantic models define the public contract **and** do input validation. Split by
concern and re-exported from `models/__init__.py` (so callers do
`from app.models import …`):

- **`common.py`** — `NAME_RE` (the RFC 1123 label regex) and `MessageResponse`.
- **`namespaces.py`**
  - `ResourceLimits` — `memory`, `cpu`, `storage`, each `Optional[str]`;
    `is_empty()` is true when all are `None` (this is what lets a caller patch a
    single dimension).
  - `CreateNamespaceRequest` — `name` + `limits`; `valid_name` enforces `NAME_RE`
    and the 63-char cap; violations raise `ValidationError` → HTTP **422**.
  - `UpdateQuotaRequest` — `limits` required; `not_empty` rejects an all-`None`
    `ResourceLimits` so a no-op patch is a 422, not a silent success.
  - Responses: `NamespaceSummary`, `NamespaceListResponse`, `NamespaceStatusResponse`.
- **`egressips.py`**
  - `CreateEgressIPRequest` — `valid_ips` validates each IP with `ipaddress`;
    `non_empty_namespace_labels` enforces the safety pin (§6.3).
  - Responses: `EgressIPSummary`, `EgressIPListResponse`.

Explicit response models keep the OpenAPI schema accurate.

---

## 5. Kubernetes access & services — `app/k8s/` + `app/services/`

### 5.1 `k8s/client.py` — `K8sClient`

Constructor loads credentials:
- `in_cluster=True` → `config.load_incluster_config()` (the pod's mounted
  **service account** token — this is how the API authenticates *to* the
  cluster).
- otherwise → `config.load_kube_config(kubeconfig_path)` for local dev.

Then it builds:
- `self.core_v1 = CoreV1Api(api_client)` — namespaces, resource quotas.
- `self.dynamic = DynamicClient(api_client)` — discovery + arbitrary resources
  (CRDs during force-delete, and the cluster-scoped `EgressIP`).

### 5.2 `services/namespaces.py` — `NamespaceManager`

Mutates the cluster for namespaces. Constructed with the `K8sClient`, the
managed-label key/value, and the deletion annotation key; it calls
`render_manifest` for the objects it creates. `NamespaceError` (an
`app.core.errors.ApiError`) is the typed, reportable failure the router maps to
an HTTP status. `QUOTA_NAME` is the fixed name of the managed `ResourceQuota`.

Methods:

- **`create_namespace(name, limits)`** — renders `namespace.yaml.j2`, creates the
  namespace (a `409` becomes `NamespaceError(..., 409)`), then `apply_quota`.
- **`update_quota(name, limits)`** — `require_namespace` then `apply_quota`.
- **`mark_for_deletion(name)`** — *soft delete*. Patches the namespace with an
  annotation `deletion_annotation_key = <UTC ISO timestamp>`. Nothing is removed.
- **`force_delete(name)`** — `delete_all_resources` then `delete_namespace`
  (a `404` on the final delete is tolerated — already gone).
- **`namespace_exists(name)`** — `read_namespace`; returns `True`, or `False`
  on a real `404` (any other error → `NamespaceError`). Unlike
  `require_namespace`, absence is not an error here.
- **`apply_quota(namespace, limits)`** — returns `{}` if `limits` empty.
  Otherwise renders `resourcequota.yaml.j2` with only the supplied dimensions,
  then `read_quota`: **404 → create**, else **merge-patch**. Because the manifest
  only contains the supplied `hard` keys, the merge changes just those and leaves
  the rest — this is what makes "update only memory" work.
- **`delete_all_resources(namespace)`** — for each `(api_version, kind)` from
  `namespaced_kinds()`, list and delete each object, collecting `"Kind/name"`.
  Per-item failures are logged and skipped (best-effort).
- **`namespaced_kinds()`** — walks `dynamic.resources.search()` and keeps kinds
  that are **namespaced**, support both `list` and `delete`, and aren't
  subresources (no `/` in the name). De-duplicated by `(api_version, kind)` — the
  discovery step that makes force-delete cover CRDs, not just core types.
- **`require_namespace(name)`** — `read_namespace`; raises
  `NamespaceError(..., 404)` if missing.

Each mutating op logs a stdout event (`event=namespace_created …`, etc.).

### 5.3 `services/cache.py` — `NamespaceCache`

Serves the list endpoint without hitting the API server per request.

- State: `_items`, `_cached_at`, guarded by a `Lock`.
- **`start()`** — does one synchronous `refresh()` (warm cache) then launches a
  **daemon thread** (`_loop`) that calls `refresh()` every `refresh_interval`
  seconds. Daemon so it never blocks shutdown.
- **`_loop()`** — `while not self._stop.wait(interval)`: the `Event.wait` doubles
  as the sleep and the stop signal. Exceptions are caught and logged so a
  transient API error never kills the thread.
- **`refresh()`** — `list_namespace(label_selector=...)`, maps each item to a
  `NamespaceSummary` (name, phase, labels, and `marked_for_deletion_at` read
  from the deletion annotation), then swaps `_items`/`_cached_at` under the lock.
- **`get()`** — returns a copy of the items + `cached_at` ISO string.
- **`stop()`** — sets the stop event and joins (≤ 5s). Called from lifespan
  shutdown.

> **Why single-worker?** The cache lives in process memory and has its own
> refresher thread. Run one Uvicorn worker per pod and scale with **replicas**
> (each replica keeps its own cache); see the Dockerfile `CMD`.

### 5.4 `services/egressips.py` — `EgressIPManager`

Manages OpenShift `EgressIP` (cluster-scoped OVN-Kubernetes CRD) via the
**dynamic client** (no namespace argument). Api-version/kind come from config
(`APP_EGRESSIP_API_VERSION`/`_KIND`). Methods: `create`, `delete`, `get`, `list`.

- The API models grouping purely as **labels** — no built-in "ecosystem". The
  caller's `namespace_labels` become `spec.namespaceSelector.matchLabels`, and
  are **mirrored onto `metadata.labels`** (with `managed-by` + optional extra
  `labels`) so `list` can filter by the same labels.
- **Safety pin:** `namespace_labels` is required and validated non-empty
  (`models/egressips.py`), because an empty namespaceSelector matches *every*
  namespace. IPs are validated with `ipaddress`.
- `list(labels)` builds a metadata label selector `managed-by=…[,k=v…]`.
- Errors raise `EgressIPError` (an `ApiError`), mapped to HTTP by `run()`.
- Design: [design/egressip-endpoints.md](design/egressip-endpoints.md).

---

## 6. Templating — `app/k8s/render.py` + `app/templates/`

- **`render.py` — `render_manifest(name, **ctx)`** renders a Jinja2 template and
  `yaml.safe_load`s it into a manifest dict. A module-level `Environment` uses
  `FileSystemLoader(settings.template_dir)`, `StrictUndefined` (a forgotten
  variable raises instead of emitting blanks), and block trimming.
- **`templates/namespace.yaml.j2`** — a `Namespace` with the managed label.
- **`templates/resourcequota.yaml.j2`** — a `ResourceQuota`; `{% if memory %}` /
  `{% if cpu %}` / `{% if storage %}` emit only the supplied dimensions.
- **`templates/egressip.yaml.j2`** — an `EgressIP` (metadata labels, `egressIPs`,
  `namespaceSelector`, optional `podSelector`).

> **Gotcha (already handled):** quota values are wrapped in quotes
> (`"{{ cpu }}"`) so a value like `4` is rendered as the YAML **string** `"4"`,
> not an int — Kubernetes quantities must be strings. There's a regression test
> for this (`test_quota_all_dimensions_are_strings`).

To change what gets created (add a `LimitRange`, default labels, etc.), edit the
templates — no Python change required.

---

## 7. Authentication — `app/auth/`

Pluggable: exactly one module is active, chosen by `APP_AUTH_MODULE`.

- **`base.py`** — shared primitives:
  - `Principal(username, module)` — the authenticated identity.
  - `AuthError(message, headers)` — raised on failure; `headers` carries e.g. the
    `WWW-Authenticate` challenge.
  - `BASIC_CHALLENGE` — the Basic `WWW-Authenticate` header constant.
- **`basic.py` — `BasicAuth`**
  - `__init__(users)` — `{username: password}` (from a mounted Secret in prod).
  - `authenticate(request)` — parses `Authorization: Basic <b64>`, splits
    `user:pass`, compares the password with `hmac.compare_digest`. Any problem →
    `AuthError` with the Basic challenge header; success → a `Principal`.
  - `openapi_scheme()` → `("basicAuth", {"type":"http","scheme":"basic", …})`.
- **`__init__.py`** — `AUTH_MODULES` maps `"basic"` → a factory; `build_auth(settings)`
  returns the configured module (or raises `ValueError` for an unknown name). It
  re-exports `Principal` / `AuthError`.

There is **no ABC** — a module is any object exposing `authenticate(request) ->
Principal` and `openapi_scheme()`. **Adding one (e.g. bearer):** write the class,
register it in `AUTH_MODULES`, set `APP_AUTH_MODULE`. The `require_auth` guard and
Swagger pick it up automatically.

---

## 8. HTTP layer — `app/api/`

### Dependencies (`deps.py`)
- `get_manager` / `get_cache` pull the singletons off `request.app.state`
  (populated in lifespan). The EgressIP router defines `get_egress_manager`
  locally.
- **`require_auth`** — the guard. Calls `app.state.auth.authenticate`; on
  `AuthError` raises `HTTPException(401, detail, headers=exc.headers)`. A
  dependency on every protected route.
- **`run(func)`** — runs a manager call and converts any `ApiError`
  (`NamespaceError` / `EgressIPError`) → `HTTPException(status_code)`.

### Routers (`namespaces.py`, `egressips.py`, `__init__.py`)
`namespaces.py` (prefix `/api/v1/namespaces`) and `egressips.py`
(`/api/v1/egressips`); `__init__.py` builds `api_router` including both, and
`main` mounts that single router.

| Function | Method/path | Notes |
|----------|-------------|-------|
| `create_namespace` | `POST /namespaces` | `201`; body `CreateNamespaceRequest`. |
| `list_namespaces` | `GET /namespaces` | from the cache (no live call). |
| `namespace_status` | `GET /namespaces/{name}/status` | live existence; `{exists: bool}`. |
| `update_quota` | `PATCH /namespaces/{name}/quota` | body `UpdateQuotaRequest`. |
| `delete_namespace` | `DELETE /namespaces/{name}` | `force` → `force_delete` vs `mark_for_deletion`. |
| `create_egressip` | `POST /egressips` | `201`; body `CreateEgressIPRequest`. |
| `list_egressips` | `GET /egressips` | optional `?labels=k=v` metadata filter. |
| `get_egressip` | `GET /egressips/{name}` | single object or `404`. |
| `delete_egressip` | `DELETE /egressips/{name}` | `200` / `404`. |

Every route depends on `require_auth`, so identity is validated before any work.

---

## 9. Composition & Swagger — `app/main.py`

- **`lifespan`** (async context manager) runs on startup/shutdown:
  - `configure_logging` (to stdout), build `K8sClient`.
  - put `settings`, `k8s`, `manager` (`NamespaceManager`), `cache`
    (`NamespaceCache`), `egress_manager` (`EgressIPManager`), and `auth`
    (`build_auth(settings)`) on `app.state`.
  - `cache.start()` (warm + thread). On shutdown, `cache.stop()`.
  - **Tests** monkeypatch `app.main.K8sClient` before entering the TestClient so
    the lifespan wires the **fake** cluster.
- **`create_app()`** builds the `FastAPI` app, registers `/healthz` and
  `/readyz` (readyz reports `warming` until the cache has populated once),
  includes `api_router`, then installs the custom OpenAPI via `build_openapi(app)`.
- **`build_openapi(app)`** — overrides `app.openapi` so the generated schema
  **advertises the active auth module** (FastAPI can't infer a scheme from our
  custom `require_auth` dependency):
  1. build the base schema with `get_openapi(...)`,
  2. ask `build_auth(get_settings()).openapi_scheme()` for the `(name, object)`
     and inject it under `components.securitySchemes`,
  3. add a `security: [{name: []}]` requirement to every operation whose path
     starts with `/api/` (so health endpoints stay unsecured in the docs).

  Result: the Swagger **Authorize** button appears and matches whichever module
  is enabled — Basic today, Bearer/OAuth later with no change here.

---

## 10. Request flow (end to end)

`POST /api/v1/namespaces`:

1. Uvicorn → FastAPI route `create_namespace`.
2. Body parsed/validated into `CreateNamespaceRequest` (bad name → 422).
3. `require_auth` → `app.state.auth.authenticate` (bad creds → 401).
4. `get_manager` provides the `NamespaceManager` from `app.state`.
5. `create_namespace` renders `namespace.yaml.j2` → `core_v1.create_namespace`.
6. `apply_quota` renders `resourcequota.yaml.j2` → create/merge-patch.
7. `NamespaceError` (e.g. 409) → `HTTPException` (via `run`); success →
   `MessageResponse` serialized to JSON `201`.

The new namespace appears in `GET /api/v1/namespaces` after the next cache
refresh (≤ `cache_refresh_interval_seconds`).

---

## 11. Tests — `tests/`

Everything runs against the in-memory fake cluster — no real Kubernetes needed.

**Locally:**
```bash
pip install -r requirements-dev.txt
pytest
```

**With Docker Compose** (no local Python; the project is bind-mounted so host
edits are picked up without a rebuild — see `docker-compose.yml` + `Dockerfile.test`):
```bash
docker compose run --rm tests                 # run everything
docker compose run --rm tests pytest -k auth  # filter
docker compose run --rm tests pytest -x       # stop on first failure
docker compose build tests                    # rebuild after dependency changes
```

- **`fakes.py`** — `FakeK8sClient` with `FakeCoreV1` (in-memory namespaces +
  quotas, realistic `ApiException` 404/409) and `FakeDynamic` (a name-only store
  for force-delete enumeration **plus** a full-object store for EgressIP
  create/get/list-by-label/delete). Mirrors only the client surface the code calls.
- **`conftest.py`** — sets test env (`APP_BASIC_AUTH_USERS`, long refresh
  interval) **before** importing `app`, clears the settings cache, and provides:
  - `fake_k8s` — a fresh fake.
  - `client` — a `TestClient` whose lifespan is monkeypatched onto the fake;
    yields `(client, fake)`. `AUTH = ("admin", "s3cret")`.
- **Unit tests:** `test_schemas.py` (validation), `test_auth.py` (basic module,
  `build_auth`, openapi scheme), `test_templating.py` (rendering, string-typed
  quota, StrictUndefined), `test_cache.py` (label filtering, annotation
  surfacing — no thread), `test_namespace_service.py` (`NamespaceManager`
  create/update/mark/force against the fake), `test_logging.py` (stdout event
  logging).
- **EgressIP tests:** `test_egressips.py` (unit — `EgressIPManager`
  create/get/list/delete, 404/409, and schema validation: invalid IPs, empty
  `namespace_labels`, bad name, IPv6) and `test_egress_api.py` (functional —
  create/list-by-label/get/delete happy + error paths, auth 401).
- **Functional tests:** `test_api.py` drives the real HTTP stack — auth 401s,
  OpenAPI security advertisement, create/list/update/delete/status happy + error
  paths, and force-delete removing resources then the namespace.

86 tests total; functional tests use a long cache interval and call
`app.state.cache.refresh()` explicitly where they need fresh data.

---

## 12. Helm chart — `helm/naas-api/`

Renders: `ServiceAccount`, `ClusterRole` + `ClusterRoleBinding` (the SA's
cluster permissions), `ConfigMap` (the `APP_*` config), `Secret` (basic-auth
users as JSON), `Deployment`, `Service`, and — for external exposure — an
OpenShift `Route` **or** a Kubernetes `Ingress`.

- **Exposure:** `route.enabled` (OpenShift) and `ingress.enabled` (plain
  Kubernetes) are both `false` by default. Enable **at most one** — the
  `ingress.yaml` template `fail`s the render if both are on. The Ingress
  supports `className`, `annotations`, `host`, `path`/`pathType`, and `tls`.

- `values.yaml` → `config.*` map onto the `APP_*` env (ConfigMap);
  `auth.basicUsers` → the Secret (`--set-json 'auth.basicUsers={...}'`).
- `rbac.allowWildcardForceDelete` (default `true`) grants `get/list/delete` on
  `*/*` so force-delete can remove CRD objects; set `false` to restrict to a
  core resource set.
- The Deployment hashes the ConfigMap/Secret into pod annotations so config
  changes trigger a rollout.

---

## 13. Container — `Dockerfile`

`python:3.12-slim`, **pip only**: copy `requirements.txt`, `pip install`, copy
`app/`. OpenShift-friendly: files `chgrp 0` + `g=u`, runs as non-root
(`USER 1001`), `EXPOSE 8080`. `CMD` runs a **single** Uvicorn worker (the cache
thread must not be duplicated across workers — scale with replicas).

---

## 14. Local development

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
export APP_IN_CLUSTER=false APP_KUBECONFIG_PATH=~/.kube/config
export APP_BASIC_AUTH_USERS='{"admin":"changeme"}'
uvicorn app.main:app --reload --port 8080   # docs at http://localhost:8080/docs
pytest
```
