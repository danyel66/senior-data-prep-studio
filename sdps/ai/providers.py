"""LLM providers. Both stream, support cancellation, and return schema-constrained JSON text.

Claude: official `anthropic` SDK, streaming, structured outputs (`output_config.format`), adaptive
thinking with summarized display for progress, prompt caching on the stable system prompt, and the
server-side refusal fallback (`fallbacks="default"`) on models that support it.

Ollama: local HTTP API (`/api/chat`) with the same JSON schema passed as `format`.
"""
from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass

from .config import FALLBACK_MODELS, AIConfig, estimate_cost, get_api_key

ProgressFn = Callable[[str], None]


class AIError(Exception):
    """A user-presentable provider failure. `settings_hint` suggests opening AI Settings."""

    def __init__(self, message: str, *, settings_hint: bool = False, retryable: bool = False):
        super().__init__(message)
        self.settings_hint = settings_hint
        self.retryable = retryable


class AICancelled(Exception):
    pass


@dataclass
class Completion:
    text: str
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    cost_usd: float | None = None
    fallback_used: bool = False


class Provider:
    name = "provider"

    def complete(self, system: str, user: str, schema: dict, progress: ProgressFn,
                 cancel: threading.Event) -> Completion:
        raise NotImplementedError

    def interrupt(self) -> None:
        """Abort an in-flight request from another thread."""


# ─────────────────────────────────────────── Claude ───────────────────────────────────────────
class ClaudeProvider(Provider):
    name = "claude"

    def __init__(self, config: AIConfig, api_key: str | None = None, base_url: str | None = None):
        self.config = config
        self._api_key = api_key if api_key is not None else get_api_key()
        self._base_url = base_url
        self._stream = None
        self._lock = threading.Lock()

    def _client(self):
        try:
            import anthropic
        except ImportError as e:
            raise AIError("The 'anthropic' package is not installed. Run: pip install -r requirements.txt") from e
        kwargs = {}
        if self._api_key:
            kwargs["api_key"] = self._api_key       # otherwise env var / `ant auth login` profile
        if self._base_url:
            kwargs["base_url"] = self._base_url
        try:
            return anthropic.Anthropic(**kwargs)
        except anthropic.AnthropicError as e:
            raise AIError("No Anthropic API key is configured. Add one in Challenge → AI Settings…",
                          settings_hint=True) from e

    def complete(self, system, user, schema, progress, cancel) -> Completion:
        import anthropic
        client = self._client()
        model = self.config.claude_model
        params = dict(
            model=model,
            max_tokens=64000,
            system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": user}],
            thinking={"type": "adaptive", "display": "summarized"},
            output_config={"effort": self.config.claude_effort,
                           "format": {"type": "json_schema", "schema": schema}},
        )
        if model in FALLBACK_MODELS:
            params.update(betas=["server-side-fallback-2026-07-01"], fallbacks="default")
        progress("Contacting Claude…")
        text_chars = 0
        try:
            with client.beta.messages.stream(**params) as stream:
                with self._lock:
                    self._stream = stream
                if cancel.is_set():
                    raise AICancelled()
                for event in stream:
                    if cancel.is_set():
                        raise AICancelled()
                    if event.type == "content_block_start" and event.content_block.type == "thinking":
                        progress("Designing the challenge…")
                    elif event.type == "content_block_delta":
                        if event.delta.type == "thinking_delta" and event.delta.thinking.strip():
                            line = event.delta.thinking.strip().splitlines()[-1][:140]
                            progress(f"Thinking: {line}")
                        elif event.delta.type == "text_delta":
                            text_chars += len(event.delta.text)
                            progress(f"Writing challenge… {text_chars:,} characters")
                final = stream.get_final_message()
        except AICancelled:
            raise
        except anthropic.AuthenticationError as e:
            raise AIError("Anthropic rejected the API key. Check it in Challenge → AI Settings…",
                          settings_hint=True) from e
        except anthropic.PermissionDeniedError as e:
            raise AIError(f"This API key can't use {model}: {e.message}", settings_hint=True) from e
        except anthropic.NotFoundError as e:
            raise AIError(f"Model '{model}' is not available to this API key.", settings_hint=True) from e
        except anthropic.RateLimitError as e:
            raise AIError("Rate limited by the Anthropic API. Wait a minute and try again.", retryable=True) from e
        except anthropic.BadRequestError as e:
            raise AIError(f"The Anthropic API rejected the request: {e.message}") from e
        except anthropic.APIStatusError as e:
            if e.status_code >= 500:
                raise AIError(f"The Anthropic API is having problems ({e.status_code}). Try again shortly.",
                              retryable=True) from e
            raise AIError(f"Anthropic API error {e.status_code}: {e.message}") from e
        except anthropic.APIConnectionError as e:
            if cancel.is_set():
                raise AICancelled() from e
            raise AIError("Couldn't reach the Anthropic API. Check your internet connection.", retryable=True) from e
        except anthropic.AnthropicError as e:
            if cancel.is_set():
                raise AICancelled() from e
            raise AIError(str(e)) from e
        finally:
            with self._lock:
                self._stream = None

        if final.stop_reason == "refusal":
            raise AIError("Claude declined to generate this challenge. Try a different topic or difficulty.")
        if final.stop_reason == "max_tokens":
            raise AIError("The response was cut off (max_tokens). Try a lower difficulty or effort.", retryable=True)
        text = "".join(b.text for b in final.content if b.type == "text")
        u = final.usage
        fallback_used = any(getattr(it, "type", "") == "fallback_message" for it in (getattr(u, "iterations", None) or []))
        served_by = final.model or model
        inp, out = u.input_tokens or 0, u.output_tokens or 0
        cr, cw = u.cache_read_input_tokens or 0, u.cache_creation_input_tokens or 0
        return Completion(text, served_by, inp, out, cr, cw, estimate_cost(served_by, inp, out, cr, cw), fallback_used)

    def interrupt(self) -> None:
        with self._lock:
            stream = self._stream
        if stream is not None:
            try:
                stream.close()
            except Exception:
                pass


# ─────────────────────────────────────────── Ollama ───────────────────────────────────────────
class OllamaProvider(Provider):
    name = "ollama"

    def __init__(self, config: AIConfig):
        self.config = config
        self._response = None
        self._lock = threading.Lock()

    @property
    def base(self) -> str:
        return self.config.ollama_url.rstrip("/")

    def list_models(self, timeout: float = 3.0) -> list[str]:
        try:
            with urllib.request.urlopen(f"{self.base}/api/tags", timeout=timeout) as r:
                data = json.load(r)
        except (urllib.error.URLError, OSError, ValueError) as e:
            raise AIError(f"Ollama isn't reachable at {self.base}. Install it from ollama.com and make sure "
                          "it is running.", settings_hint=True) from e
        return sorted(m.get("name", "") for m in data.get("models", []) if m.get("name"))

    def complete(self, system, user, schema, progress, cancel) -> Completion:
        model = self.config.ollama_model
        if not model:
            raise AIError("No Ollama model selected. Choose one in Challenge → AI Settings…", settings_hint=True)
        body = json.dumps({
            "model": model, "stream": True, "format": schema,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "options": {"temperature": 0.6, "num_ctx": int(self.config.ollama_num_ctx)},
        }).encode()
        req = urllib.request.Request(f"{self.base}/api/chat", data=body, headers={"Content-Type": "application/json"})
        progress(f"Loading {model} in Ollama… (the first run can take a while)")
        parts: list[str] = []
        last: dict = {}
        chars, t0 = 0, time.monotonic()
        try:
            resp = urllib.request.urlopen(req, timeout=600)
            with self._lock:
                self._response = resp
            with resp:
                for raw in resp:
                    if cancel.is_set():
                        raise AICancelled()
                    if not raw.strip():
                        continue
                    msg = json.loads(raw)
                    if msg.get("error"):
                        raise AIError(f"Ollama error: {msg['error']}")
                    piece = msg.get("message", {}).get("content", "")
                    if piece:
                        parts.append(piece)
                        chars += len(piece)
                        progress(f"Writing challenge with {model}… {chars:,} characters "
                                 f"({time.monotonic() - t0:.0f}s)")
                    if msg.get("done"):
                        last = msg
                        break
        except AICancelled:
            raise
        except urllib.error.HTTPError as e:
            detail = ""
            try:
                detail = json.loads(e.read()).get("error", "")
            except Exception:
                pass
            if e.code == 404:
                raise AIError(f"Ollama model '{model}' is not installed. Run: ollama pull {model}",
                              settings_hint=True) from e
            raise AIError(f"Ollama returned HTTP {e.code}: {detail or e.reason}") from e
        except (urllib.error.URLError, ConnectionError, OSError) as e:
            if cancel.is_set():
                raise AICancelled() from e
            raise AIError(f"Ollama isn't reachable at {self.base}. Is it running?", settings_hint=True,
                          retryable=True) from e
        except ValueError as e:
            raise AIError(f"Unexpected response from Ollama: {e}") from e
        finally:
            with self._lock:
                self._response = None
        if last.get("done_reason") == "length":
            raise AIError("Ollama stopped before finishing (context or length limit). Try a larger model or "
                          "increase the context size in AI Settings.", retryable=True)
        return Completion("".join(parts), model, last.get("prompt_eval_count", 0), last.get("eval_count", 0),
                          cost_usd=0.0)

    def interrupt(self) -> None:
        with self._lock:
            resp = self._response
        if resp is not None:
            try:
                resp.close()
            except Exception:
                pass


def create_provider(config: AIConfig) -> Provider:
    return OllamaProvider(config) if config.provider == "ollama" else ClaudeProvider(config)
