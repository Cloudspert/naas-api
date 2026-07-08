"""EgressIP operations: create, delete, get, list.

EgressIP (k8s.ovn.org/v1) is a cluster-scoped CRD, so everything goes through
the dynamic client with no namespace argument.
"""

import logging

from kubernetes.client.exceptions import ApiException

from app.core.errors import ApiError
from app.k8s.render import render_manifest

logger = logging.getLogger(__name__)


class EgressIPError(ApiError):
    pass


class EgressIPManager:
    def __init__(self, k8s, api_version, kind, managed_label_key, managed_label_value):
        self.k8s = k8s
        self.api_version = api_version
        self.kind = kind
        self.managed_label_key = managed_label_key
        self.managed_label_value = managed_label_value

    def _resource(self):
        return self.k8s.dynamic.resources.get(api_version=self.api_version, kind=self.kind)

    def create(self, name, egress_ips, namespace_labels, pod_labels=None, labels=None):
        manifest = render_manifest(
            "egressip.yaml.j2",
            api_version=self.api_version,
            kind=self.kind,
            name=name,
            egress_ips=egress_ips,
            namespace_labels=namespace_labels,
            pod_labels=pod_labels,
            metadata_labels=self._metadata_labels(namespace_labels, labels),
        )
        try:
            self._resource().create(body=manifest)
        except ApiException as exc:
            if exc.status == 409:
                raise EgressIPError(f"egressip '{name}' already exists", 409)
            raise EgressIPError(f"failed to create egressip: {exc.reason}", exc.status or 500)

        logger.info(
            "event=egressip_created name=%s ips=%s namespace_labels=%s",
            name, egress_ips, namespace_labels,
        )
        return self._summary(manifest)

    def delete(self, name):
        try:
            self._resource().delete(name=name)
        except ApiException as exc:
            if exc.status == 404:
                raise EgressIPError(f"egressip '{name}' not found", 404)
            raise EgressIPError(f"failed to delete egressip: {exc.reason}", exc.status or 500)

        logger.info("event=egressip_deleted name=%s", name)
        return {"name": name}

    def get(self, name):
        try:
            obj = self._resource().get(name=name)
        except ApiException as exc:
            if exc.status == 404:
                raise EgressIPError(f"egressip '{name}' not found", 404)
            raise EgressIPError(f"failed to read egressip: {exc.reason}", exc.status or 500)
        return self._summary(obj.to_dict())

    def list(self, labels=None):
        selector = self._label_selector(labels)
        try:
            result = self._resource().get(label_selector=selector)
        except ApiException as exc:
            raise EgressIPError(f"failed to list egressips: {exc.reason}", exc.status or 500)
        return [self._summary(item.to_dict()) for item in result.items]

    # --- helpers -------------------------------------------------------------

    def _metadata_labels(self, namespace_labels, labels):
        # managed-by + mirrored namespace selector labels + any extra index labels.
        result = {self.managed_label_key: self.managed_label_value}
        result.update(namespace_labels or {})
        result.update(labels or {})
        return result

    def _label_selector(self, labels):
        parts = [f"{self.managed_label_key}={self.managed_label_value}"]
        for key, value in (labels or {}).items():
            parts.append(f"{key}={value}")
        return ",".join(parts)

    @staticmethod
    def _summary(obj):
        meta = obj.get("metadata", {}) or {}
        spec = obj.get("spec", {}) or {}
        status = obj.get("status", {}) or {}
        return {
            "name": meta.get("name"),
            "egress_ips": spec.get("egressIPs", []),
            "namespace_selector": (spec.get("namespaceSelector", {}) or {}).get("matchLabels", {}),
            "pod_selector": (spec.get("podSelector", {}) or {}).get("matchLabels", {}),
            "status": status.get("items", []),
            "labels": meta.get("labels", {}),
        }
