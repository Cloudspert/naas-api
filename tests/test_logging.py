"""Tests for lifecycle event logging to stdout."""

import logging
import sys

from app.main import configure_logging
from app.services.namespaces import NamespaceManager
from app.models import ResourceLimits
from tests.fakes import FakeK8sClient, _Namespace

ANNOTATION = "naas-api/marked-for-deletion-at"


def _manager(fake):
    return NamespaceManager(
        k8s=fake,
        managed_label_key="managed-by",
        managed_label_value="naas-api",
        deletion_annotation_key=ANNOTATION,
    )


def test_configure_logging_targets_stdout():
    configure_logging("INFO")
    root = logging.getLogger()
    stdout_handlers = [
        h for h in root.handlers if getattr(h, "_naas_stdout", False) and h.stream is sys.stdout
    ]
    assert stdout_handlers


def test_configure_logging_is_idempotent():
    configure_logging("INFO")
    configure_logging("INFO")
    root = logging.getLogger()
    assert len([h for h in root.handlers if getattr(h, "_naas_stdout", False)]) == 1


def test_create_logs_event(caplog):
    fake = FakeK8sClient()
    with caplog.at_level(logging.INFO, logger="app.namespaces"):
        _manager(fake).create_namespace("team-a", ResourceLimits(memory="8Gi"))
    assert "event=namespace_created namespace=team-a" in caplog.text


def test_update_quota_logs_event(caplog):
    fake = FakeK8sClient()
    manager = _manager(fake)
    manager.create_namespace("team-a", ResourceLimits(memory="8Gi"))
    with caplog.at_level(logging.INFO, logger="app.namespaces"):
        manager.update_quota("team-a", ResourceLimits(memory="16Gi"))
    assert "event=quota_updated namespace=team-a" in caplog.text


def test_mark_for_deletion_logs_event(caplog):
    fake = FakeK8sClient()
    fake.core_v1.namespaces["team-a"] = _Namespace("team-a")
    with caplog.at_level(logging.INFO, logger="app.namespaces"):
        _manager(fake).mark_for_deletion("team-a")
    assert "event=namespace_marked_for_deletion namespace=team-a" in caplog.text


def test_force_delete_logs_event(caplog):
    fake = FakeK8sClient()
    fake.core_v1.namespaces["team-a"] = _Namespace("team-a")
    fake.dynamic.add_resource("ConfigMap", "v1", ["cm1"])
    with caplog.at_level(logging.INFO, logger="app.namespaces"):
        _manager(fake).force_delete("team-a")
    assert "event=namespace_deleted namespace=team-a" in caplog.text


def test_events_reach_stdout(capsys):
    configure_logging("INFO")
    fake = FakeK8sClient()
    _manager(fake).create_namespace("team-a", ResourceLimits(memory="8Gi"))
    for handler in logging.getLogger().handlers:
        handler.flush()
    assert "event=namespace_created namespace=team-a" in capsys.readouterr().out
