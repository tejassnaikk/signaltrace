"""Tests for evaluation/run_agent.py (not the agent loop itself)."""
from __future__ import annotations

import pytest


def test_build_out_path_no_limit():
    from evaluation.run_agent import _build_out_path
    p = _build_out_path("/tmp/results", "dev", "abc123", None)
    assert p.endswith("agent_dev_abc123.jsonl")
    assert "_debug" not in p


def test_build_out_path_with_limit():
    from evaluation.run_agent import _build_out_path
    p = _build_out_path("/tmp/results", "dev", "abc123", 3)
    assert p.endswith("agent_dev_abc123_debug.jsonl")
    assert "_debug" in p


def test_limit_run_writes_to_debug_file_only(monkeypatch, tmp_path):
    """--limit N writes to *_debug.jsonl; the main results file is never created."""
    import evaluation.run_agent as mod

    # Prevent any Ollama calls
    monkeypatch.setattr(mod, "_verify_pinned_digest", lambda: None)
    monkeypatch.setattr(mod, "RESULTS_DIR", str(tmp_path))

    # Return a trivial ok result without calling Ollama
    fake_result = {
        "answer": {"pattern": "none", "claims": [], "primary": [], "narrative": ""},
        "trace": [],
        "tool_call_counts": {},
        "status": "ok",
        "n_rejected": 0,
        "rejected": [],
        "error": None,
    }
    monkeypatch.setattr(mod, "run_agent", lambda *a, **kw: fake_result)
    monkeypatch.setattr(mod, "OllamaClient", lambda cfg: object())

    mod.main(["--split", "dev", "--limit", "1", "--runs", "1"])

    names = [p.name for p in tmp_path.iterdir()]
    debug_files = [n for n in names if "_debug" in n and n.endswith(".jsonl")]
    main_files = [n for n in names if n.endswith(".jsonl") and "_debug" not in n]

    assert len(debug_files) == 1, f"Expected 1 debug file, got {debug_files}"
    assert main_files == [], f"Expected no main results file, got {main_files}"


# ------------------------------------------------------------------ #
# Circuit breaker tests                                               #
# ------------------------------------------------------------------ #

_FAIL_RESULT = {
    "answer": None,
    "trace": [],
    "tool_call_counts": {},
    "status": "failed_error",
    "n_rejected": 0,
    "rejected": [],
    "error": "simulated error for circuit breaker test",
    "error_context": None,
}

_OK_RESULT = {
    "answer": {"pattern": "none", "claims": [], "primary": [], "narrative": ""},
    "trace": [],
    "tool_call_counts": {},
    "status": "ok",
    "n_rejected": 0,
    "rejected": [],
    "error": None,
    "error_context": None,
}


def test_l_circuit_breaker_stops_on_two_consecutive_failed_error(monkeypatch, tmp_path):
    """2 consecutive failed_error runs trigger sys.exit(1)."""
    import evaluation.run_agent as mod

    monkeypatch.setattr(mod, "_verify_pinned_digest", lambda: None)
    monkeypatch.setattr(mod, "RESULTS_DIR", str(tmp_path))
    monkeypatch.setattr(mod, "OllamaClient", lambda cfg: object())
    monkeypatch.setattr(mod, "run_agent", lambda *a, **kw: _FAIL_RESULT)

    with pytest.raises(SystemExit) as exc_info:
        mod.main(["--split", "dev", "--limit", "1", "--runs", "2"])

    assert exc_info.value.code != 0


def test_l_circuit_breaker_one_error_then_ok_does_not_stop(monkeypatch, tmp_path):
    """1 failed_error followed by an ok run does not trigger the circuit breaker."""
    import evaluation.run_agent as mod

    monkeypatch.setattr(mod, "_verify_pinned_digest", lambda: None)
    monkeypatch.setattr(mod, "RESULTS_DIR", str(tmp_path))
    monkeypatch.setattr(mod, "OllamaClient", lambda cfg: object())

    call_count = [0]

    def _alternating(*a, **kw):
        call_count[0] += 1
        return _FAIL_RESULT if call_count[0] == 1 else _OK_RESULT

    monkeypatch.setattr(mod, "run_agent", _alternating)

    # Must complete without raising SystemExit
    mod.main(["--split", "dev", "--limit", "1", "--runs", "2"])

    assert call_count[0] == 2
