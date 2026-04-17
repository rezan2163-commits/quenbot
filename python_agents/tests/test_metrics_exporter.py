"""MetricsExporter tests — render format + source wiring."""
from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if HERE not in sys.path:
    sys.path.insert(0, HERE)


def test_render_with_sources():
    from metrics_exporter import MetricsExporter, _reset_metrics_exporter_for_tests
    _reset_metrics_exporter_for_tests()
    exp = MetricsExporter(port=0)
    exp.register("fast_brain", lambda: {
        "fast_brain_predictions_total": 123,
        "fast_brain_enabled": True,
        "fast_brain_name": "x",   # non-numeric, skipped
    })
    exp.register("router", lambda: {"decision_router_routed_total": 7})
    body = exp._render()
    assert "quenbot_exporter_up 1" in body
    assert "quenbot_fast_brain_predictions_total 123" in body
    assert "quenbot_fast_brain_enabled 1" in body
    assert "quenbot_decision_router_routed_total 7" in body
    # non-numeric skipped
    assert "fast_brain_name" not in body


def test_source_error_does_not_raise():
    from metrics_exporter import MetricsExporter
    exp = MetricsExporter(port=0)
    def bad():
        raise RuntimeError("boom")
    exp.register("broken", bad)
    body = exp._render()
    assert "# error in broken" in body
    assert exp._scrape_errors >= 1


def test_metrics_self_reporting():
    from metrics_exporter import MetricsExporter
    exp = MetricsExporter(port=9999)
    m = exp.metrics()
    assert m["metrics_exporter_port"] == 9999
    assert m["metrics_exporter_scrape_total"] == 0


def test_sanitize_names():
    from metrics_exporter import _sanitize_name
    assert _sanitize_name("fast_brain.predictions/total") == "fast_brain_predictions_total"
    assert _sanitize_name("ok") == "ok"
