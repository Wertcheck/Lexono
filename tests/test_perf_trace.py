"""Tests fuer app/observability/perf_trace.py (P0 Performance-Root-Cause-
Run, 13.09.) - Request-Correlation-Trace durch die bestehende Pipeline."""

from __future__ import annotations

import time

from app.observability.perf_trace import PerfTrace


def test_step_records_name_and_a_plausible_duration() -> None:
    trace = PerfTrace()
    with trace.step("beispiel_schritt"):
        time.sleep(0.01)

    assert len(trace.steps) == 1
    name, duration = trace.steps[0]
    assert name == "beispiel_schritt"
    assert duration >= 0.01


def test_multiple_steps_are_recorded_in_order() -> None:
    trace = PerfTrace()
    with trace.step("a"):
        pass
    with trace.step("b"):
        pass

    assert [name for name, _ in trace.steps] == ["a", "b"]


def test_trace_id_is_stable_across_steps_unless_explicitly_reused() -> None:
    trace = PerfTrace()
    first_id = trace.trace_id
    with trace.step("x"):
        pass
    assert trace.trace_id == first_id


def test_explicit_trace_id_is_kept() -> None:
    trace = PerfTrace(trace_id="fixed-id")
    assert trace.trace_id == "fixed-id"


def test_total_seconds_grows_with_elapsed_time() -> None:
    trace = PerfTrace()
    time.sleep(0.01)
    assert trace.total_seconds() >= 0.01
