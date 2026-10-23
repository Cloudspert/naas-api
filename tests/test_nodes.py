"""Unit tests for NodeManager and NodeCache."""

from app.services.node_cache import NodeCache
from app.services.nodes import NodeManager
from tests.fakes import FakeK8sClient, _Node


def _seed(fake):
    fake.core_v1.nodes = {
        "worker-1": _Node(
            "worker-1",
            labels={"topology.kubernetes.io/zone": "eu-west-1a"},
            roles=["worker"],
            internal_ip="10.0.0.11",
            capacity={"cpu": "8", "memory": "32Gi", "pods": "110"},
            kubelet_version="v1.30.4",
        ),
        "cp-1": _Node("cp-1", roles=["control-plane"], internal_ip="10.0.0.5",
                      ready=False, unschedulable=True),
    }


def test_fetch_all_shapes_detail():
    fake = FakeK8sClient()
    _seed(fake)
    details = {d.name: d for d in NodeManager(fake).fetch_all()}

    w = details["worker-1"]
    assert w.ready is True
    assert w.roles == ["worker"]
    assert w.internal_ip == "10.0.0.11"
    assert w.addresses == {"InternalIP": "10.0.0.11", "Hostname": "worker-1"}
    assert w.capacity["memory"] == "32Gi"
    assert w.kubelet_version == "v1.30.4"
    assert w.unschedulable is False

    cp = details["cp-1"]
    assert cp.ready is False
    assert cp.roles == ["control-plane"]
    assert cp.unschedulable is True


def test_cache_list_and_get():
    fake = FakeK8sClient()
    _seed(fake)
    cache = NodeCache(NodeManager(fake), refresh_interval=3600)
    cache.refresh()  # no thread in tests

    assert {n.name for n in cache.list()} == {"worker-1", "cp-1"}
    assert cache.get("worker-1").internal_ip == "10.0.0.11"
    assert cache.get("ghost") is None
    assert cache.ready() is True


def test_cache_list_label_filter():
    fake = FakeK8sClient()
    _seed(fake)
    cache = NodeCache(NodeManager(fake), refresh_interval=3600)
    cache.refresh()

    workers = cache.list({"node-role.kubernetes.io/worker": ""})
    assert {n.name for n in workers} == {"worker-1"}
    assert cache.list({"topology.kubernetes.io/zone": "nope"}) == []


def test_cache_empty_before_refresh():
    cache = NodeCache(NodeManager(FakeK8sClient()), refresh_interval=3600)
    assert cache.ready() is False
    assert cache.list() == []
