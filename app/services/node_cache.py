"""In-memory cache of cluster nodes, refreshed on a background thread.

Mirrors NamespaceCache. Stores the full NodeDetail per node so both the list
and get endpoints are served from here; list filtering is done in-memory.
"""

import logging
import threading
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


class NodeCache:
    def __init__(self, node_manager, refresh_interval):
        self.node_manager = node_manager
        self.refresh_interval = max(1, refresh_interval)

        self._lock = threading.Lock()
        self._by_name = {}
        self._cached_at = None
        self._stop = threading.Event()
        self._thread = None

    def start(self):
        self.refresh()  # warm synchronously
        self._thread = threading.Thread(target=self._loop, name="node-cache", daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)

    def _loop(self):
        while not self._stop.wait(self.refresh_interval):
            try:
                self.refresh()
            except Exception as exc:  # never let the thread die
                logger.error("node cache refresh failed: %s", exc)

    def refresh(self):
        by_name = {detail.name: detail for detail in self.node_manager.fetch_all()}
        with self._lock:
            self._by_name = by_name
            self._cached_at = datetime.now(timezone.utc)

    def list(self, labels=None):
        with self._lock:
            details = list(self._by_name.values())
        if labels:
            details = [d for d in details if _matches(d.labels, labels)]
        return details

    def get(self, name):
        with self._lock:
            return self._by_name.get(name)

    def ready(self) -> bool:
        with self._lock:
            return self._cached_at is not None


def _matches(node_labels, wanted) -> bool:
    return all(node_labels.get(key) == value for key, value in wanted.items())
