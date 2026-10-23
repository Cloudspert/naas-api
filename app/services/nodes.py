"""Node operations (read-only): fetch and shape cluster nodes.

Nodes are cluster-scoped core objects, so this uses CoreV1 (no dynamic client,
no namespace). The manager only reads and builds NodeDetail objects; the list
and get endpoints are served from NodeCache.
"""

import logging

from app.models import NodeDetail

logger = logging.getLogger(__name__)

_ROLE_PREFIX = "node-role.kubernetes.io/"


class NodeManager:
    def __init__(self, k8s):
        self.k8s = k8s

    def fetch_all(self):
        """Live list of all nodes as NodeDetail objects (used by NodeCache)."""
        result = self.k8s.core_v1.list_node()
        return [self._detail(node) for node in result.items]

    @staticmethod
    def _detail(node) -> NodeDetail:
        meta = node.metadata
        labels = meta.labels or {}
        status = getattr(node, "status", None)
        spec = getattr(node, "spec", None)

        addresses, conditions, capacity, allocatable, node_info = {}, [], {}, {}, None
        if status is not None:
            addresses = {a.type: a.address for a in (status.addresses or [])}
            conditions = status.conditions or []
            capacity = status.capacity or {}
            allocatable = status.allocatable or {}
            node_info = getattr(status, "node_info", None)

        ready = None
        for cond in conditions:
            if cond.type == "Ready":
                ready = cond.status == "True"
                break

        roles = sorted(
            key[len(_ROLE_PREFIX):]
            for key in labels
            if key.startswith(_ROLE_PREFIX) and key[len(_ROLE_PREFIX):]
        )

        return NodeDetail(
            name=meta.name,
            ready=ready,
            roles=roles,
            internal_ip=addresses.get("InternalIP"),
            labels=labels,
            unschedulable=bool(getattr(spec, "unschedulable", False)) if spec else False,
            addresses=addresses,
            annotations=meta.annotations or {},
            capacity=capacity,
            allocatable=allocatable,
            kubelet_version=getattr(node_info, "kubelet_version", None) if node_info else None,
            os_image=getattr(node_info, "os_image", None) if node_info else None,
            architecture=getattr(node_info, "architecture", None) if node_info else None,
        )
