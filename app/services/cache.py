"""In-memory cache of labelled namespaces, refreshed on a background thread."""

import logging
import threading
from datetime import datetime, timezone

from app.models import NamespaceSummary

logger = logging.getLogger(__name__)


class NamespaceCache:
    def __init__(self, k8s, label_selector, refresh_interval, deletion_annotation_key):
        self.k8s = k8s
        self.label_selector = label_selector
        self.refresh_interval = max(1, refresh_interval)
        self.deletion_annotation_key = deletion_annotation_key

        self._lock = threading.Lock()
        self._items = []
        self._cached_at = None
        self._stop = threading.Event()
        self._thread = None

    def start(self):
        self.refresh()  # warm the cache synchronously
        self._thread = threading.Thread(target=self._loop, name="namespace-cache", daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)

    def _loop(self):
        # Event.wait doubles as the sleep and the stop signal.
        while not self._stop.wait(self.refresh_interval):
            try:
                self.refresh()
            except Exception as exc:  # never let the thread die
                logger.error("namespace cache refresh failed: %s", exc)

    def refresh(self):
        result = self.k8s.core_v1.list_namespace(label_selector=self.label_selector)
        items = []
        for ns in result.items:
            annotations = ns.metadata.annotations or {}
            items.append(
                NamespaceSummary(
                    name=ns.metadata.name,
                    status=ns.status.phase if ns.status else None,
                    labels=ns.metadata.labels or {},
                    marked_for_deletion_at=annotations.get(self.deletion_annotation_key),
                )
            )
        with self._lock:
            self._items = items
            self._cached_at = datetime.now(timezone.utc)

    def get(self):
        with self._lock:
            cached_at = self._cached_at.isoformat() if self._cached_at else None
            return list(self._items), cached_at
