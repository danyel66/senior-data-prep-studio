"""AI provider settings. Non-secret settings live in config.json; the Anthropic API key lives in the
OS credential store (macOS Keychain / Windows Credential Manager / Secret Service) via `keyring`."""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, fields
from pathlib import Path

from ..db.config import DatabaseConfig

KEYRING_SERVICE = "SeniorDataPrepStudio"
KEYRING_USER = "anthropic_api_key"

# model id -> (label, $ per 1M input, $ per 1M output, $ per 1M cache read)
CLAUDE_MODELS = {
    "claude-opus-5-5": ("Claude Opus 5.5 (best quality)", 4.00, 20.00, 0.20),
    "claude-sonnet-5-5": ("Claude Sonnet 5.5 (balanced)", 2.00, 10.00, 0.20),
    "claude-haiku-5-5": ("Claude Haiku 5.5 (cheapest)", 0.10, 0.50, 0.01),
}
DEFAULT_CLAUDE_MODEL = "claude-opus-5-5"
# Models that accept the server-side refusal fallback (`fallbacks: "default"`) on the Claude API.
FALLBACK_MODELS = {"claude-opus-5-5", "claude-sonnet-5-5"}
EFFORT_LEVELS = ["low", "medium", "high"]
RECOMMENDED_OLLAMA_MODELS = ["qwen2.5-coder:14b", "qwen2.5-coder:7b", "llama3.1:8b"]


@dataclass
class AIConfig:
    provider: str = "claude"                 # "claude" | "ollama"
    claude_model: str = DEFAULT_CLAUDE_MODEL
    claude_effort: str = "medium"
    ollama_url: str = "http://localhost:11434"
    ollama_model: str = ""
    ollama_num_ctx: int = 16384

    @property
    def model(self) -> str:
        return self.claude_model if self.provider == "claude" else (self.ollama_model or "?")

    def describe(self) -> str:
        if self.provider == "ollama":
            return f"Ollama · {self.ollama_model or 'no model selected'}"
        return CLAUDE_MODELS.get(self.claude_model, (self.claude_model,))[0].split(" (")[0]

    # ---- persistence (shares config.json with DatabaseConfig) ---------------------------------
    @staticmethod
    def _path() -> Path:
        return DatabaseConfig.path()

    @classmethod
    def load(cls) -> "AIConfig":
        try:
            raw = json.loads(cls._path().read_text(encoding="utf-8")).get("ai", {})
        except (OSError, ValueError):
            raw = {}
        known = {f.name for f in fields(cls)}
        cfg = cls(**{k: v for k, v in raw.items() if k in known})
        if cfg.provider not in ("claude", "ollama"):
            cfg.provider = "claude"
        if cfg.claude_effort not in EFFORT_LEVELS:
            cfg.claude_effort = "medium"
        return cfg

    def save(self) -> None:
        path = self._path()
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            doc = {}
        doc["ai"] = asdict(self)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(doc, indent=2), encoding="utf-8")
        os.replace(tmp, path)


# ---- API key storage -------------------------------------------------------------------------
_session_key: str | None = None      # used when no OS keyring backend is available


def _keyring():
    try:
        import keyring
        from keyring.backends.fail import Keyring as FailKeyring
        kr = keyring.get_keyring()
        return None if isinstance(kr, FailKeyring) else keyring
    except Exception:
        return None


def keyring_available() -> bool:
    return _keyring() is not None


def get_api_key() -> str | None:
    kr = _keyring()
    if kr is not None:
        try:
            if key := kr.get_password(KEYRING_SERVICE, KEYRING_USER):
                return key
        except Exception:
            pass
    return _session_key


def set_api_key(key: str | None) -> bool:
    """Store (or clear with None/'') the key. Returns True if saved to the OS keyring,
    False if it could only be kept for this session."""
    global _session_key
    key = (key or "").strip() or None
    kr = _keyring()
    if kr is not None:
        try:
            if key:
                kr.set_password(KEYRING_SERVICE, KEYRING_USER, key)
            else:
                try:
                    kr.delete_password(KEYRING_SERVICE, KEYRING_USER)
                except Exception:
                    pass
            _session_key = None
            return True
        except Exception:
            pass
    _session_key = key
    return False


def claude_credentials_hint() -> str | None:
    """Where Claude credentials will come from, or None if none are configured."""
    if get_api_key():
        return "saved API key"
    if os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"):
        return "environment variable"
    if (Path.home() / ".config" / "anthropic").is_dir():
        return "`ant auth login` profile"
    return None


def estimate_cost(model: str, input_tokens: int, output_tokens: int, cache_read: int = 0,
                  cache_write: int = 0) -> float | None:
    if model not in CLAUDE_MODELS:
        return None
    _, p_in, p_out, p_cache = CLAUDE_MODELS[model]
    return (input_tokens * p_in + cache_write * p_in * 1.25 + cache_read * p_cache + output_tokens * p_out) / 1e6
