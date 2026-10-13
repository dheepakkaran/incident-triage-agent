"""Every setting comes from an environment variable (or `.env`) with a working default.

`get_settings()` reads the environment each time it is called, so tests can change a
setting with `monkeypatch.setenv` without reloading modules.
"""

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

# The six fault types injected in RCAEval RE2. Runbooks and diagnoses use these names.
FAULT_TYPES = ("cpu", "mem", "disk", "delay", "loss", "socket")


def _str(name: str, default: str) -> str:
    value = os.getenv(name, "").strip()
    return value or default


def _int(name: str, default: int) -> int:
    return int(_str(name, str(default)))


def _float(name: str, default: float) -> float:
    return float(_str(name, str(default)))


def _bool(name: str, default: bool) -> bool:
    return _str(name, str(default)).lower() in ("1", "true", "yes", "on")


def _path(name: str, default: Path) -> Path:
    return Path(_str(name, str(default)))


@dataclass(frozen=True)
class Settings:
    # LLM providers
    provider_chain: tuple[str, ...]
    groq_model: str
    openrouter_model: str
    ollama_model: str
    groq_api_key: str
    openrouter_api_key: str
    ollama_host: str
    llm_timeout_s: float
    llm_cache_enabled: bool

    # Agent limits
    max_tool_calls: int
    confidence_threshold: float
    reviewer_enabled: bool
    reviewer_max_tool_calls: int

    # Paths
    data_dir: Path
    raw_dir: Path
    db_path: Path
    labels_path: Path
    splits_path: Path
    runbooks_dir: Path
    chroma_dir: Path
    runs_dir: Path
    cache_dir: Path

    # Runbook retrieval
    embedding_model: str


def get_settings() -> Settings:
    data_dir = _path("DATA_DIR", ROOT / "data")
    cache_dir = _path("CACHE_DIR", ROOT / ".cache")
    chain = tuple(p.strip() for p in _str("LLM_PROVIDER_CHAIN", "groq,openrouter,ollama").split(",") if p.strip())
    return Settings(
        provider_chain=chain,
        groq_model=_str("GROQ_MODEL", "openai/gpt-oss-120b"),
        openrouter_model=_str("OPENROUTER_MODEL", "openai/gpt-oss-120b:free"),
        ollama_model=_str("OLLAMA_MODEL", "qwen3:8b"),
        groq_api_key=_str("GROQ_API_KEY", ""),
        openrouter_api_key=_str("OPENROUTER_API_KEY", ""),
        ollama_host=_str("OLLAMA_HOST", "http://localhost:11434"),
        llm_timeout_s=_float("LLM_TIMEOUT_S", 120.0),
        llm_cache_enabled=_bool("LLM_CACHE_ENABLED", True),
        max_tool_calls=_int("MAX_TOOL_CALLS", 8),
        confidence_threshold=_float("CONFIDENCE_THRESHOLD", 0.6),
        reviewer_enabled=_bool("REVIEWER_ENABLED", True),
        reviewer_max_tool_calls=_int("REVIEWER_MAX_TOOL_CALLS", 3),
        data_dir=data_dir,
        raw_dir=_path("RAW_DIR", data_dir / "raw"),
        db_path=_path("TELEMETRY_DB", data_dir / "telemetry.db"),
        # Only eval/ code may read labels. A test enforces this for triage/agent and triage/tools.
        labels_path=_path("LABELS_PATH", data_dir / "labels.json"),
        splits_path=_path("SPLITS_PATH", data_dir / "splits.json"),
        runbooks_dir=_path("RUNBOOKS_DIR", ROOT / "runbooks"),
        chroma_dir=_path("CHROMA_DIR", cache_dir / "chroma"),
        runs_dir=_path("RUNS_DIR", ROOT / "runs"),
        cache_dir=cache_dir,
        embedding_model=_str("EMBEDDING_MODEL", "BAAI/bge-small-en-v1.5"),
    )
