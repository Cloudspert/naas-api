"""Tests for the namespace cache (no background thread)."""

from app.services.cache import NamespaceCache
from tests.fakes import FakeK8sClient, _Namespace

SELECTOR = "managed-by=naas-api"
ANNOTATION = "naas-api/marked-for-deletion-at"


def _cache(fake):
    return NamespaceCache(
        k8s=fake,
        label_selector=SELECTOR,
        refresh_interval=3600,
        deletion_annotation_key=ANNOTATION,
    )


def test_refresh_only_returns_labelled_namespaces():
    fake = FakeK8sClient()
    fake.core_v1.namespaces = {
        "managed": _Namespace("managed", labels={"managed-by": "naas-api"}),
        "other": _Namespace("other", labels={"team": "x"}),
    }
    cache = _cache(fake)
    cache.refresh()

    items, cached_at = cache.get()
    assert {i.name for i in items} == {"managed"}
    assert cached_at is not None


def test_refresh_surfaces_deletion_annotation():
    fake = FakeK8sClient()
    fake.core_v1.namespaces = {
        "managed": _Namespace(
            "managed",
            labels={"managed-by": "naas-api"},
            annotations={ANNOTATION: "2026-06-30T00:00:00+00:00"},
        ),
    }
    cache = _cache(fake)
    cache.refresh()

    items, _ = cache.get()
    assert items[0].marked_for_deletion_at == "2026-06-30T00:00:00+00:00"


def test_get_before_refresh_is_empty():
    cache = _cache(FakeK8sClient())
    items, cached_at = cache.get()
    assert items == []
    assert cached_at is None
