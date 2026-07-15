"""Namespace operations: create, update quota, delete, existence check."""

import logging
from datetime import datetime, timezone

from kubernetes.client.exceptions import ApiException

from app.core.errors import ApiError
from app.k8s.render import render_manifest

logger = logging.getLogger(__name__)

QUOTA_NAME = "naas-api-quota"


class NamespaceError(ApiError):
    pass


class NamespaceManager:
    def __init__(self, k8s, managed_label_key, managed_label_value, deletion_annotation_key,
                 key_prefix=None):
        self.k8s = k8s
        self.managed_label_key = managed_label_key
        self.managed_label_value = managed_label_value
        self.deletion_annotation_key = deletion_annotation_key
        self.key_prefix = key_prefix

    def create_namespace(self, name, limits, labels=None, annotations=None):
        extra_labels = self.prefixed_keys(labels)
        extra_annotations = self.prefixed_keys(annotations)
        if self.deletion_annotation_key in extra_annotations:
            raise NamespaceError(
                f"annotation '{self.deletion_annotation_key}' is reserved", 422
            )

        manifest = render_manifest(
            "namespace.yaml.j2",
            name=name,
            managed_label_key=self.managed_label_key,
            managed_label_value=self.managed_label_value,
            extra_labels=extra_labels or None,
            extra_annotations=extra_annotations or None,
        )
        try:
            self.k8s.core_v1.create_namespace(body=manifest)
        except ApiException as exc:
            if exc.status == 409:
                raise NamespaceError(f"namespace '{name}' already exists", 409)
            raise NamespaceError(f"failed to create namespace: {exc.reason}", exc.status or 500)

        # The quota inherits the namespace's labels (already prefixed).
        # Annotations stay namespace-level only.
        quota = self.apply_quota(name, limits, labels=extra_labels)
        logger.info(
            "event=namespace_created namespace=%s quota=%s labels=%s annotations=%s",
            name, quota, extra_labels, extra_annotations,
        )
        return {
            "namespace": name,
            "quota": quota,
            "labels": extra_labels,
            "annotations": extra_annotations,
        }

    def update_quota(self, name, limits):
        self.require_namespace(name)
        quota = self.apply_quota(name, limits)
        logger.info("event=quota_updated namespace=%s quota=%s", name, quota)
        return {"namespace": name, "quota": quota}

    def mark_for_deletion(self, name):
        self.require_namespace(name)
        timestamp = datetime.now(timezone.utc).isoformat()
        patch = {"metadata": {"annotations": {self.deletion_annotation_key: timestamp}}}
        try:
            self.k8s.core_v1.patch_namespace(name=name, body=patch)
        except ApiException as exc:
            raise NamespaceError(f"failed to mark namespace: {exc.reason}", exc.status or 500)

        logger.info(
            "event=namespace_marked_for_deletion namespace=%s marked_for_deletion_at=%s",
            name,
            timestamp,
        )
        return {"namespace": name, "marked_for_deletion_at": timestamp}

    def force_delete(self, name):
        self.require_namespace(name)
        deleted = self.delete_all_resources(name)
        try:
            self.k8s.core_v1.delete_namespace(name=name)
        except ApiException as exc:
            if exc.status != 404:
                raise NamespaceError(f"failed to delete namespace: {exc.reason}", exc.status or 500)

        logger.info(
            "event=namespace_deleted namespace=%s deleted_resource_count=%d", name, len(deleted)
        )
        return {"namespace": name, "deleted_resources": deleted}

    def namespace_exists(self, name):
        try:
            self.k8s.core_v1.read_namespace(name=name)
            return True
        except ApiException as exc:
            if exc.status == 404:
                return False
            raise NamespaceError(f"failed to read namespace: {exc.reason}", exc.status or 500)

    # --- helpers -------------------------------------------------------------

    def prefixed_keys(self, entries):
        """Apply the configured key prefix to caller-supplied labels/annotations.

        {"env": "prod"} -> {"company.example.io/env": "prod"} when a prefix is
        configured. Keys that already carry a prefix (contain "/") are left
        alone — Kubernetes allows only one prefix per key.
        """
        if not entries:
            return {}
        if not self.key_prefix:
            return dict(entries)
        return {
            key if "/" in key else f"{self.key_prefix}/{key}": value
            for key, value in entries.items()
        }

    def apply_quota(self, namespace, limits, labels=None):
        """Create or merge-patch the quota. Only the supplied dimensions change."""
        if limits.is_empty():
            return {}

        manifest = render_manifest(
            "resourcequota.yaml.j2",
            quota_name=QUOTA_NAME,
            namespace=namespace,
            managed_label_key=self.managed_label_key,
            managed_label_value=self.managed_label_value,
            extra_labels=labels or None,
            memory=limits.memory,
            cpu=limits.cpu,
            storage=limits.storage,
        )
        try:
            if self.read_quota(namespace) is None:
                self.k8s.core_v1.create_namespaced_resource_quota(namespace=namespace, body=manifest)
            else:
                # Merge patch: only the rendered "hard" keys are updated.
                self.k8s.core_v1.patch_namespaced_resource_quota(
                    name=QUOTA_NAME, namespace=namespace, body=manifest
                )
        except ApiException as exc:
            raise NamespaceError(f"failed to apply quota: {exc.reason}", exc.status or 500)

        return manifest.get("spec", {}).get("hard", {})

    def read_quota(self, namespace):
        try:
            return self.k8s.core_v1.read_namespaced_resource_quota(
                name=QUOTA_NAME, namespace=namespace
            )
        except ApiException as exc:
            if exc.status == 404:
                return None
            raise

    def delete_all_resources(self, namespace):
        """Delete every namespaced resource (core + CRD) in the namespace."""
        deleted = []
        for api_version, kind in self.namespaced_kinds():
            try:
                resource = self.k8s.dynamic.resources.get(api_version=api_version, kind=kind)
                items = resource.get(namespace=namespace)
            except Exception as exc:  # best-effort enumeration
                logger.debug("skipping %s: %s", kind, exc)
                continue

            for item in getattr(items, "items", []):
                item_name = item.metadata.name
                try:
                    resource.delete(name=item_name, namespace=namespace)
                    deleted.append(f"{kind}/{item_name}")
                except Exception as exc:
                    logger.warning("failed to delete %s/%s in %s: %s", kind, item_name, namespace, exc)
        return deleted

    def namespaced_kinds(self):
        """(api_version, kind) for every namespaced, listable+deletable resource."""
        kinds = []
        seen = set()
        for api_resource in self.k8s.dynamic.resources.search():
            try:
                namespaced = getattr(api_resource, "namespaced", False)
                verbs = getattr(api_resource, "verbs", []) or []
                kind = getattr(api_resource, "kind", None)
                api_version = getattr(api_resource, "group_version", None)
                name = getattr(api_resource, "name", "")
                # Skip cluster-scoped, subresources (e.g. pods/status), and
                # anything we can't both list and delete.
                if not namespaced or not kind or "/" in name:
                    continue
                if "list" not in verbs or "delete" not in verbs:
                    continue
                if (api_version, kind) in seen:
                    continue
                seen.add((api_version, kind))
                kinds.append((api_version, kind))
            except Exception:
                continue
        return kinds

    def require_namespace(self, name):
        try:
            self.k8s.core_v1.read_namespace(name=name)
        except ApiException as exc:
            if exc.status == 404:
                raise NamespaceError(f"namespace '{name}' not found", 404)
            raise NamespaceError(f"failed to read namespace: {exc.reason}", exc.status or 500)
