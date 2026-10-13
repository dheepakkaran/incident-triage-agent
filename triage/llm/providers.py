"""Provider chain: Groq -> OpenRouter -> local Ollama, with retry, fallback, cache and usage records.

Every LLM call in the agent goes through `LLMClient.invoke`, so there is one place that
handles rate limits, timeouts, caching and token/latency accounting.
"""

import os
import time
from dataclasses import dataclass
from typing import Any

import httpx
from langchain_core.messages import AIMessage, BaseMessage

from triage.config import Settings, get_settings
from triage.llm.cache import DiskCache, cache_key

MAX_RETRIES = 2  # per provider, only for rate limits, timeouts and 5xx
MAX_WAIT_S = 65.0  # long enough to outlast a per-minute token window (used by the last provider in the chain)
FALLBACK_WAIT_S = 20.0  # when another provider is next, do not sit out a long wait: move on


class LLMError(RuntimeError):
    """Raised when every provider in the chain failed."""


@dataclass
class ModelHandle:
    provider: str
    model: str
    chat: Any  # a LangChain chat model, or ScriptedChatModel in tests


def _ollama_has_model(host: str, model: str) -> bool:
    try:
        tags = httpx.get(f"{host}/api/tags", timeout=2).json()
    except (httpx.HTTPError, ValueError):
        return False
    names = {m["name"] for m in tags.get("models", [])}
    return model in names or f"{model}:latest" in names


def build_chat_model(provider: str, settings: Settings) -> ModelHandle | None:
    """Build one provider's chat model, or None when it is not usable (no key / not running)."""
    if provider == "groq":
        if not settings.groq_api_key:
            return None
        from langchain_groq import ChatGroq

        extra = {}
        effort = os.getenv("GROQ_REASONING_EFFORT", "low").strip()
        if effort and ("gpt-oss" in settings.groq_model or "qwen" in settings.groq_model):
            extra["reasoning_effort"] = effort  # fewer hidden reasoning tokens on the free tier
        chat = ChatGroq(
            model=settings.groq_model,
            api_key=settings.groq_api_key,
            temperature=0,
            max_retries=0,  # we retry ourselves, so we can fall back to the next provider
            timeout=settings.llm_timeout_s,
            **extra,
        )
        return ModelHandle("groq", settings.groq_model, chat)

    if provider == "openrouter":
        if not settings.openrouter_api_key:
            return None
        from langchain_openai import ChatOpenAI

        chat = ChatOpenAI(
            model=settings.openrouter_model,
            api_key=settings.openrouter_api_key,
            base_url="https://openrouter.ai/api/v1",
            temperature=0,
            max_retries=0,
            timeout=settings.llm_timeout_s,
        )
        return ModelHandle("openrouter", settings.openrouter_model, chat)

    if provider == "google":
        if not settings.google_api_key:
            return None
        from langchain_google_genai import ChatGoogleGenerativeAI

        extra = {}
        effort = os.getenv("GOOGLE_REASONING_EFFORT", "minimal").strip()
        if effort:
            extra["reasoning_effort"] = effort  # Gemma 4 "thinks" a lot by default: 37 s vs 1.5 s per review call
        chat = ChatGoogleGenerativeAI(
            model=settings.google_model,  # Gemma 4, open weights, served by Google AI Studio
            google_api_key=settings.google_api_key,
            temperature=0,
            max_retries=0,
            timeout=settings.llm_timeout_s,
            **extra,
        )
        return ModelHandle("google", settings.google_model, chat)

    if provider == "ollama":
        if not _ollama_has_model(settings.ollama_host, settings.ollama_model):
            return None
        from langchain_ollama import ChatOllama

        chat = ChatOllama(
            model=settings.ollama_model,
            base_url=settings.ollama_host,
            temperature=0,
            num_ctx=16384,  # the default 4k window is too small for 8 tool outputs
            reasoning=False,  # skip "thinking" output on qwen3-style models; it is slow locally
        )
        return ModelHandle("ollama", settings.ollama_model, chat)

    raise ValueError(f"unknown provider '{provider}'")


def _is_retryable(err: Exception) -> bool:
    status = (
        getattr(err, "status_code", None)
        or getattr(getattr(err, "response", None), "status_code", None)
        or getattr(err, "code", None)  # Google's client puts the HTTP status here
    )
    if status in (429, 500, 502, 503, 504):
        return True
    name = type(err).__name__.lower()
    return "timeout" in name or "ratelimit" in name or "connection" in name


def _wait_seconds(err: Exception, attempt: int, cap: float = MAX_WAIT_S) -> float | None:
    """Honour the provider's retry-after header when it sends one, else exponential backoff.
    Returns None when the wait would be longer than `cap` (better to try the next provider)."""
    headers = getattr(getattr(err, "response", None), "headers", None) or {}
    retry_after = headers.get("retry-after")
    try:
        wait = float(retry_after) if retry_after else 2.0 * (2**attempt)
    except ValueError:
        wait = 2.0 * (2**attempt)
    if wait > cap:
        return None
    return wait + 0.5


def _text_only(reply: AIMessage) -> AIMessage:
    """Some models (e.g. Gemma 4) return a list of blocks, including their "thinking". Keep the text only,
    so every provider's reply parses the same way downstream."""
    if isinstance(reply.content, list):
        parts = [b if isinstance(b, str) else b.get("text", "") for b in reply.content if isinstance(b, str) or b.get("type") == "text"]
        reply.content = "".join(parts)
    return reply


def _usage(reply: AIMessage) -> dict:
    meta = reply.usage_metadata or {}
    return {
        "input_tokens": int(meta.get("input_tokens", 0)),
        "output_tokens": int(meta.get("output_tokens", 0)),
    }


class LLMClient:
    def __init__(self, handles: list[ModelHandle], cache: DiskCache | None = None, sleep=time.sleep):
        if not handles:
            raise LLMError(
                "No LLM provider is available. Set GROQ_API_KEY or OPENROUTER_API_KEY in .env, "
                "or start Ollama and pull the model in OLLAMA_MODEL."
            )
        self.handles = handles
        self.cache = cache
        self._sleep = sleep

    @classmethod
    def from_settings(cls, settings: Settings | None = None, only: str | None = None, role: str | None = None) -> "LLMClient":
        """`only` pins one provider; `role` ('investigator' / 'reviewer') uses that agent's own chain."""
        settings = settings or get_settings()
        by_role = {"investigator": settings.investigator_chain, "reviewer": settings.reviewer_chain}
        chain = [only] if only else list(by_role.get(role, settings.provider_chain))
        handles = [h for h in (build_chat_model(p, settings) for p in chain) if h is not None]
        cache = DiskCache(settings.cache_dir / "llm") if settings.llm_cache_enabled else None
        return cls(handles, cache)

    @property
    def primary(self) -> ModelHandle:
        return self.handles[0]

    def invoke(self, messages: list[BaseMessage], tools: list[dict] | None = None, purpose: str = "") -> tuple[AIMessage, dict]:
        """Return (reply, record). `record` has provider, model, tokens, latency, cached, attempts."""
        # Walk the chain in order. Each provider's own cached reply is used before calling it (so eval
        # reruns cost nothing), but a fallback's old cached answer never wins over the primary model.
        # Retry only errors that may go away (rate limit, timeout, 5xx).
        errors = []
        for position, handle in enumerate(self.handles):
            is_last = position == len(self.handles) - 1
            if self.cache is not None:
                hit = self.cache.get(cache_key(handle.provider, handle.model, messages, tools))
                if hit is not None:
                    reply, usage = hit
                    # Report the latency of the original call, so cached reruns give honest numbers.
                    return reply, self._record(purpose, handle, usage, usage.get("latency_s", 0.0), cached=True, attempts=0, reply=reply)
            model = handle.chat.bind_tools(tools) if tools else handle.chat
            for attempt in range(MAX_RETRIES + 1):
                start = time.perf_counter()
                try:
                    reply = model.invoke(messages)
                except Exception as err:  # provider SDKs raise many different error types
                    errors.append(f"{handle.provider}: {type(err).__name__}: {str(err)[:200]}")
                    if _is_retryable(err) and attempt < MAX_RETRIES:
                        wait = _wait_seconds(err, attempt, MAX_WAIT_S if is_last else FALLBACK_WAIT_S)
                        if wait is not None:
                            self._sleep(wait)
                            continue
                    break  # give up on this provider, fall back to the next one
                latency = time.perf_counter() - start
                reply = _text_only(reply)
                usage = {**_usage(reply), "latency_s": round(latency, 3)}
                if self.cache is not None:
                    self.cache.put(cache_key(handle.provider, handle.model, messages, tools), reply, usage)
                return reply, self._record(purpose, handle, usage, latency, cached=False, attempts=attempt + 1, reply=reply)
        raise LLMError("all providers failed: " + " | ".join(errors))

    @staticmethod
    def _record(purpose: str, handle: ModelHandle, usage: dict, latency: float, cached: bool, attempts: int, reply: AIMessage) -> dict:
        """One LLM call for the trace. The reply is kept so a run can be replayed without any API."""
        return {
            "purpose": purpose,
            "provider": handle.provider,
            "model": handle.model,
            "input_tokens": usage.get("input_tokens", 0),
            "output_tokens": usage.get("output_tokens", 0),
            "latency_s": round(latency, 3),
            "cached": cached,
            "attempts": attempts,
            "reply": {
                "content": reply.content if isinstance(reply.content, str) else str(reply.content),
                "tool_calls": [{"name": c["name"], "args": c["args"], "id": c["id"]} for c in reply.tool_calls],
            },
        }
