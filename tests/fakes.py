"""In-memory fakes for the Kubernetes client used by the tests.

These mimic only the slice of the ``kubernetes`` client surface that
``NamespaceService`` and ``NamespaceCache`` actually call, with realistic
``ApiException`` behaviour (404 / 409) so the production code paths are
exercised without a real cluster.
"""

from __future__ import annotations

from typing import Dict, List, Optional, Tuple

from kubernetes.client.exceptions import ApiException


# --------------------------------------------------------------------- objects
class _Meta:
    def __init__(self, name, labels=None, annotations=None):
        self.name = name
        self.labels = labels or {}
        self.annotations = annotations or {}


class _Status:
    def __init__(self, phase="Active"):
        self.phase = phase


class _Namespace:
    def __init__(self, name, labels=None, annotations=None, phase="Active"):
        self.metadata = _Meta(name, labels, annotations)
        self.status = _Status(phase)


class _ListResult:
    def __init__(self, items):
        self.items = items


# ------------------------------------------------------------------ CoreV1 API
class FakeCoreV1:
    def __init__(self) -> None:
        self.namespaces: Dict[str, _Namespace] = {}
        self.quotas: Dict[Tuple[str, str], dict] = {}

    # namespaces ---------------------------------------------------------------
    def create_namespace(self, body: dict) -> _Namespace:
        name = body["metadata"]["name"]
        if name in self.namespaces:
            raise ApiException(status=409, reason="AlreadyExists")
        ns = _Namespace(name, labels=body["metadata"].get("labels", {}))
        self.namespaces[name] = ns
        return ns

    def read_namespace(self, name: str) -> _Namespace:
        if name not in self.namespaces:
            raise ApiException(status=404, reason="NotFound")
        return self.namespaces[name]

    def patch_namespace(self, name: str, body: dict) -> _Namespace:
        ns = self.namespaces.get(name)
        if ns is None:
            raise ApiException(status=404, reason="NotFound")
        ns.metadata.annotations.update(body.get("metadata", {}).get("annotations", {}))
        return ns

    def delete_namespace(self, name: str) -> None:
        if name not in self.namespaces:
            raise ApiException(status=404, reason="NotFound")
        del self.namespaces[name]

    def list_namespace(self, label_selector: Optional[str] = None) -> _ListResult:
        items = list(self.namespaces.values())
        if label_selector:
            key, _, value = label_selector.partition("=")
            items = [n for n in items if n.metadata.labels.get(key) == value]
        return _ListResult(items)

    # resource quotas ----------------------------------------------------------
    def read_namespaced_resource_quota(self, name: str, namespace: str) -> dict:
        quota = self.quotas.get((namespace, name))
        if quota is None:
            raise ApiException(status=404, reason="NotFound")
        return quota

    def create_namespaced_resource_quota(self, namespace: str, body: dict) -> dict:
        self.quotas[(namespace, body["metadata"]["name"])] = body
        return body

    def patch_namespaced_resource_quota(self, name: str, namespace: str, body: dict) -> dict:
        existing = self.quotas.get((namespace, name))
        if existing is None:
            raise ApiException(status=404, reason="NotFound")
        # Merge-patch semantics: only supplied "hard" keys change.
        existing.setdefault("spec", {}).setdefault("hard", {}).update(
            body.get("spec", {}).get("hard", {})
        )
        return existing


# ----------------------------------------------------------------- Dynamic API
class _APIResource:
    def __init__(self, kind, group_version, namespaced=True, verbs=("list", "delete"), name=None):
        self.kind = kind
        self.group_version = group_version
        self.namespaced = namespaced
        self.verbs = list(verbs)
        self.name = name or f"{kind.lower()}s"


class _DynItem:
    def __init__(self, name):
        self.metadata = _Meta(name)


class _DynList:
    def __init__(self, items):
        self.items = items


class _FakeObj:
    """A full stored object (e.g. an EgressIP), accessed via .to_dict()."""

    def __init__(self, data):
        self._data = data

    def to_dict(self):
        return self._data


class _FakeObjList:
    def __init__(self, items):
        self.items = items

    def to_dict(self):
        return {"items": [i.to_dict() for i in self.items]}


def _matches(labels, selector):
    for part in selector.split(","):
        key, _, value = part.partition("=")
        if labels.get(key) != value:
            return False
    return True


class _ResourceHandle:
    def __init__(self, dyn, api_version, kind):
        self._dyn = dyn
        self._api_version = api_version
        self._kind = kind

    def get(self, namespace=None, name=None, label_selector=None):
        # Single object by name (full-object store).
        if name is not None:
            obj = self._dyn.objects.get((self._api_version, self._kind, name))
            if obj is None:
                raise ApiException(status=404, reason="NotFound")
            return _FakeObj(obj)
        # List by label selector (full-object store).
        if label_selector is not None:
            items = [
                _FakeObj(obj)
                for (av, kind, _), obj in self._dyn.objects.items()
                if av == self._api_version and kind == self._kind
                and _matches((obj.get("metadata") or {}).get("labels", {}) or {}, label_selector)
            ]
            return _FakeObjList(items)
        # Name-only list used by force-delete.
        names = self._dyn.items.get((self._api_version, self._kind), [])
        return _DynList([_DynItem(n) for n in names])

    def create(self, body=None):
        name = body["metadata"]["name"]
        key = (self._api_version, self._kind, name)
        if key in self._dyn.objects:
            raise ApiException(status=409, reason="AlreadyExists")
        self._dyn.objects[key] = body
        return _FakeObj(body)

    def delete(self, name=None, namespace=None):
        key = (self._api_version, self._kind, name)
        if key in self._dyn.objects:
            del self._dyn.objects[key]
            self._dyn.deleted.append((self._kind, name))
            return
        names = self._dyn.items.get((self._api_version, self._kind), [])
        if name in names:
            names.remove(name)
            self._dyn.deleted.append((self._kind, name))
            return
        raise ApiException(status=404, reason="NotFound")


class _FakeResources:
    def __init__(self, dyn):
        self._dyn = dyn

    def search(self):
        return self._dyn.api_resources

    def get(self, api_version=None, kind=None):
        return _ResourceHandle(self._dyn, api_version, kind)


class FakeDynamic:
    def __init__(self) -> None:
        self.api_resources: List[_APIResource] = []
        self.items: Dict[Tuple[str, str], List[str]] = {}
        self.objects: Dict[Tuple[str, str, str], dict] = {}
        self.deleted: List[Tuple[str, str]] = []
        self.resources = _FakeResources(self)

    def add_resource(self, kind, api_version, names, **kw):
        self.api_resources.append(_APIResource(kind, api_version, **kw))
        self.items[(api_version, kind)] = list(names)


# ------------------------------------------------------------------- top client
class FakeK8sClient:
    def __init__(self, in_cluster=True, kubeconfig_path=None) -> None:
        self.core_v1 = FakeCoreV1()
        self.dynamic = FakeDynamic()
