# NaaS API — Namespace as a Service

A FastAPI service that runs **on OpenShift** and uses its **service account** to
manage namespaces: create them with resource quotas, list the managed ones,
update quotas, and delete (soft-mark or force).

> **Why FastAPI over Flask?** Built-in request validation (Pydantic), automatic
> OpenAPI/Swagger docs at `/docs`, and clean dependency injection — which is what
> makes the pluggable auth layer below tidy.

## Documentation

- **[docs/USER.md](docs/USER.md)** — endpoints, inputs, examples, status codes.
- **[docs/DEVELOPER.md](docs/DEVELOPER.md)** — full code walkthrough, every
  module/class/function and the request flow.
- **[docs/design/quota-operator.md](docs/design/quota-operator.md)** — design
  (not yet implemented) for a `ManagedNamespace` operator that lets rollouts
  surge without exceeding the normal-run quota.
- **[docs/design/egressip-endpoints.md](docs/design/egressip-endpoints.md)** —
  design (not yet implemented) for per-ecosystem EgressIP create/delete/get
  endpoints.
- **Swagger UI** at `/docs` — the **Authorize** button is wired to the *active*
  auth module (HTTP Basic by default), so "Try it out" calls are authenticated.

## Endpoints

| Method   | Path                                  | Description                                                                 |
|----------|---------------------------------------|-----------------------------------------------------------------------------|
| `POST`   | `/api/v1/namespaces`                  | Create a namespace with quotas (`memory`, `cpu`, `storage` — any subset).         |
| `GET`    | `/api/v1/namespaces`                  | List namespaces carrying the configured label (served from cache).          |
| `GET`    | `/api/v1/namespaces/{name}/status`    | Report whether a namespace exists.                                          |
| `PATCH`  | `/api/v1/namespaces/{name}/quota`     | Update quotas; only supplied dimensions change.                             |
| `DELETE` | `/api/v1/namespaces/{name}`           | Mark for deletion (annotation). `?force=true` deletes all resources + ns.   |
| `POST`   | `/api/v1/egressips`                   | Create an EgressIP (label-selected namespaces/pods, fixed egress IPs).      |
| `GET`    | `/api/v1/egressips`                   | List EgressIPs, optional `?labels=k=v` filter.                             |
| `GET`    | `/api/v1/egressips/{name}`            | Get a single EgressIP.                                                      |
| `DELETE` | `/api/v1/egressips/{name}`            | Delete an EgressIP.                                                         |
| `GET`    | `/healthz`, `/readyz`                 | Probes.                                                                      |
| `GET`    | `/docs`                               | Swagger UI.                                                                  |

All `/api/v1` endpoints require authentication (HTTP Basic by default).

### Examples

```bash
# Create
curl -u admin:changeme -X POST http://localhost:8080/api/v1/namespaces \
  -H 'Content-Type: application/json' \
  -d '{"name":"team-a","limits":{"memory":"8Gi","cpu":"4","storage":"50Gi"}}'

# List (from cache)
curl -u admin:changeme http://localhost:8080/api/v1/namespaces

# Update only memory
curl -u admin:changeme -X PATCH http://localhost:8080/api/v1/namespaces/team-a/quota \
  -H 'Content-Type: application/json' -d '{"limits":{"memory":"16Gi"}}'

# Soft delete (mark with annotation)
curl -u admin:changeme -X DELETE http://localhost:8080/api/v1/namespaces/team-a

# Force delete (remove all core + CRD resources, then the namespace)
curl -u admin:changeme -X DELETE "http://localhost:8080/api/v1/namespaces/team-a?force=true"
```

## Configuration

All via `APP_`-prefixed environment variables (see `app/config.py`). Key ones:

| Variable                              | Default                                   | Purpose                                      |
|---------------------------------------|-------------------------------------------|----------------------------------------------|
| `APP_NAMESPACE_LABEL_SELECTOR`        | `managed-by=naas-api`                | Label filtering the list endpoint.           |
| `APP_CACHE_REFRESH_INTERVAL_SECONDS`  | `30`                                      | Background cache refresh period.             |
| `APP_DELETION_ANNOTATION_KEY`         | `naas-api/marked-for-deletion-at`    | Annotation written on soft-delete.           |
| `APP_MANAGED_LABEL`                   | `managed-by=naas-api`                | Label stamped on created resources.          |
| `APP_AUTH_MODULE`                     | `basic`                                   | Active auth module.                          |
| `APP_BASIC_AUTH_USERS`                | `{}`                                      | JSON map `{"user":"pass"}`.                  |
| `APP_IN_CLUSTER`                      | `true`                                    | Use SA creds; set `false` + `APP_KUBECONFIG_PATH` locally. |

## Architecture

```
app/
  main.py            composition root + lifespan (starts the cache)
  config.py          env-driven settings
  models/schemas.py  request/response validation
  auth/              pluggable auth: base ABC, basic module, registry
  k8s/
    client.py        SA / kubeconfig loader, CoreV1 + DynamicClient
    namespaces.py    create / update-quota / mark / force-delete
    cache.py         labelled-namespace cache + background refresher
  templating/        Jinja2 renderer + editable manifest templates
  routers/           HTTP endpoints
```

### Templating

Generated objects (Namespace, ResourceQuota) are rendered from Jinja2 templates
in `app/templating/templates/*.yaml.j2`. Change the shape of created resources
by editing those files — no Python changes needed.

### Pluggable auth

`app/auth` defines an `AuthModule` ABC. `basic` is implemented today; add a new
scheme by subclassing `AuthModule` and registering it in `app/auth/__init__.py`'s
`_REGISTRY`. The active one is selected with `APP_AUTH_MODULE`.

## Local development

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
export APP_IN_CLUSTER=false
export APP_KUBECONFIG_PATH=~/.kube/config
export APP_BASIC_AUTH_USERS='{"admin":"changeme"}'
uvicorn app.main:app --reload --port 8080
```

## Tests

Unit + functional tests run against an in-memory fake cluster — no real
OpenShift needed.

```bash
pip install -r requirements-dev.txt
pytest
```

Or with **Docker Compose** (no local Python needed — the project is bind-mounted,
so edits are picked up without a rebuild):

```bash
docker compose run --rm tests                 # run everything
docker compose run --rm tests pytest -k auth  # filter
docker compose run --rm tests pytest -x       # stop on first failure
docker compose build tests                    # rebuild after dependency changes
```

- **Unit:** schema validation, basic-auth module + registry, template rendering,
  cache filtering, `NamespaceManager` create/update/mark/force.
- **Functional:** full HTTP stack via `TestClient` (auth 401s, OpenAPI security
  advertisement, create/list/update/delete happy + error paths).

See [docs/DEVELOPER.md §11](docs/DEVELOPER.md) for the test layout.

## Build & deploy

```bash
# Build (pip-only image)
docker build -t quay.io/your-org/naas-api:0.1.0 .
docker push quay.io/your-org/naas-api:0.1.0

# Install with Helm (creates SA + RBAC + ConfigMap + Secret + Deployment + Route)
helm upgrade --install naas-api ./helm/naas-api \
  -n naas-api --create-namespace \
  --set image.repository=quay.io/your-org/naas-api \
  --set image.tag=0.1.0 \
  --set-json 'auth.basicUsers={"admin":"a-strong-password"}'
```

### Exposing the service

Pick **at most one** (both are disabled by default; enabling both fails the
render):

```bash
# OpenShift Route
helm upgrade ... --set route.enabled=true --set route.host=naas-api.apps.example.com

# Kubernetes Ingress (plain K8s)
helm upgrade ... \
  --set ingress.enabled=true \
  --set ingress.className=nginx \
  --set ingress.host=naas-api.example.com \
  --set-json 'ingress.tls=[{"secretName":"naas-api-tls","hosts":["naas-api.example.com"]}]'
```

With neither enabled, reach the API via `kubectl port-forward`.

> **Force-delete & RBAC:** complete force-deletion of CRD-backed objects requires
> broad delete rights. `rbac.allowWildcardForceDelete=true` (default) grants
> `get/list/delete` on `*/*`. Set it to `false` to restrict to a core resource
> set defined in `helm/.../templates/rbac.yaml`.
