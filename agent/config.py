"""Frozen M6 agent configuration (PLAN.md Section 4.1).

Pinned once, at the start of M6, after a smoke test on dev dates - not
re-pinned casually. `config()` returns this as a dict, for writing into
every run's metadata (PLAN.md 4.1: "the exact model tag and its Ollama
digest are pinned at the start of M6... written into every run's
metadata").

Ollama itself is NOT started or stopped by this project - it runs
outside this session's control, already configured with the server
env settings recorded below as metadata only (this module never sets
or reads them).
"""
from __future__ import annotations

MODEL_TAG = "qwen3:8b"
MODEL_DIGEST = "500a1f067a9f782620b40bee6f7b0c89e17ae61f686b92c24933e4ca4b2b8b41"
OLLAMA_VERSION = "0.35.1"

OPTIONS = {
    "temperature": 0,
    "seed": 20261001,
    "num_ctx": 16384,
    "num_predict": 2048,
}

# Qwen3 is a hybrid reasoning model - whether to enable its thinking mode
# is a real design choice, deliberately NOT made here. Decided later, on
# dev dates only (never held-out), PLAN.md 4.1. False for the M6 kickoff
# smoke test.
THINK = False

# Metadata only - describes the already-running server this project
# depends on but does not manage.
SERVER_ENV = {
    "OLLAMA_FLASH_ATTENTION": "1",
    "OLLAMA_KV_CACHE_TYPE": "q8_0",
    "OLLAMA_LOAD_TIMEOUT": "15m",
    "OLLAMA_KEEP_ALIVE": "2h",
    "models_storage": "external USB SSD ~38 MB/s",
}


def config() -> dict:
    """The frozen config, as a plain dict, for every run's metadata."""
    return {
        "model_tag": MODEL_TAG,
        "model_digest": MODEL_DIGEST,
        "ollama_version": OLLAMA_VERSION,
        "options": dict(OPTIONS),
        "think": THINK,
        "server_env": dict(SERVER_ENV),
    }


def _current_server_digest(tag: str) -> str | None:
    """GET /api/tags (via the ollama package, not curl), return the
    installed digest for `tag`, or None if the tag isn't present."""
    import ollama  # lazy — only needed when Ollama is running
    for m in ollama.list().models:
        if m.model == tag:
            return m.digest
    return None


def _verify_pinned_digest() -> None:
    try:
        current = _current_server_digest(MODEL_TAG)
    except Exception as e:
        raise RuntimeError(
            f"agent/config.py: could not reach Ollama to verify the pinned "
            f"digest for {MODEL_TAG!r} - {e}"
        ) from e
    if current is None:
        raise RuntimeError(
            f"agent/config.py: {MODEL_TAG!r} is not present on the Ollama "
            f"server (GET /api/tags found no matching model name)."
        )
    if current != MODEL_DIGEST:
        raise RuntimeError(
            f"agent/config.py: pinned digest mismatch for {MODEL_TAG!r} - "
            f"server has {current}, config pins {MODEL_DIGEST}. The model "
            f"was re-pulled or changed since the pin; refusing to proceed "
            f"with a different model than the one M6 was frozen against."
        )
