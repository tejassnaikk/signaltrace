"""Tests for evaluation/run_agent.py (not the agent loop itself)."""
from __future__ import annotations


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
