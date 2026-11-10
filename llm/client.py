"""OpenAI-compatible streaming LLM client with failover.

Works with any /chat/completions endpoint (Sarvam, Gemini's OpenAI endpoint,
a self-hosted vLLM). Provides:
- streaming text and tool-call deltas
- first-token timeout: emits a `stall` event (caller plays a cached filler once),
  then fails over to the next endpoint
- circuit breakers per endpoint on error rate and latency
- token usage for the cost ledger
"""

import asyncio
import json
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Dict, List, Optional

import httpx

from config.settings import get_settings
from observability import metrics as m
from observability.tracing import llm_span

logger = logging.getLogger(__name__)


class LLMUnavailableError(RuntimeError):
    """Every endpoint failed or timed out."""


@dataclass
class LLMEndpoint:
    name: str
    base_url: str
    api_key: str
    model: str
    extra_body: Dict[str, Any] = field(default_factory=dict)
    provider: str = "sarvam"


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: str
    extra_content: Optional[Dict[str, Any]] = None  # Gemini's signature, which must be sent back with the tool result

    def parsed_arguments(self) -> Dict[str, Any]:
        try:
            value = json.loads(self.arguments or "{}")
            return value if isinstance(value, dict) else {}
        except json.JSONDecodeError:
            return {}


@dataclass
class Usage:
    input_tokens: int = 0
    cached_tokens: int = 0
    output_tokens: int = 0

    def add(self, other: "Usage") -> None:
        self.input_tokens += other.input_tokens
        self.cached_tokens += other.cached_tokens
        self.output_tokens += other.output_tokens


@dataclass
class LLMEvent:
    type: str  # "text" | "tool_calls" | "stall" | "done"
    text: str = ""
    tool_calls: List[ToolCall] = field(default_factory=list)
    usage: Optional[Usage] = None
    ttft_ms: Optional[float] = None
    endpoint: Optional[str] = None


class CircuitBreaker:
    """Opens after `threshold` failures inside `window` seconds, for `cooldown` seconds."""

    def __init__(self, threshold: int = 3, window: float = 60.0, cooldown: float = 30.0):
        self.threshold, self.window, self.cooldown = threshold, window, cooldown
        self._failures: List[float] = []
        self._opened_at: Optional[float] = None

    @property
    def is_open(self) -> bool:
        if self._opened_at is None:
            return False
        if time.monotonic() - self._opened_at > self.cooldown:
            self._opened_at = None  # half-open: allow a trial request
            self._failures.clear()
            return False
        return True

    def record_failure(self) -> None:
        now = time.monotonic()
        self._failures = [t for t in self._failures if now - t < self.window] + [now]
        if len(self._failures) >= self.threshold:
            self._opened_at = now

    def record_success(self) -> None:
        self._failures.clear()
        self._opened_at = None


_BREAKERS: Dict[str, CircuitBreaker] = {}


def _breaker(name: str) -> CircuitBreaker:
    return _BREAKERS.setdefault(name, CircuitBreaker())


def _headers(ep: LLMEndpoint) -> Dict[str, str]:
    headers = {"Authorization": f"Bearer {ep.api_key}", "Content-Type": "application/json"}
    if ep.provider == "sarvam":
        headers["api-subscription-key"] = ep.api_key
    return headers


def is_reasoning_model(model: str) -> bool:
    return bool(re.match(r"(o\d|gpt-5)", model.lower()))


def default_reasoning_effort(model: str) -> str:
    """The lowest effort each family supports, so the first token isn't delayed."""
    name = model.lower()
    if re.match(r"gpt-5\.\d", name):
        return "none"
    return "minimal" if name.startswith("gpt-5") else "low"


def build_endpoint(provider: str, name: str) -> LLMEndpoint:
    """The one place a provider name becomes a URL, key and model. All providers share the same client."""
    s = get_settings()
    if provider == "openai":
        if not s.OPENAI_API_KEY:
            raise ValueError("LLM provider is openai but OPENAI_API_KEY is not set")
        return LLMEndpoint(name, s.OPENAI_BASE_URL, s.OPENAI_API_KEY, s.OPENAI_MODEL, {}, provider)
    if provider == "selfhosted":
        if not (s.SELFHOSTED_LLM_BASE_URL and s.SELFHOSTED_LLM_MODEL):
            raise ValueError("LLM provider is selfhosted but SELFHOSTED_LLM_BASE_URL or SELFHOSTED_LLM_MODEL is not set")
        return LLMEndpoint(name, s.SELFHOSTED_LLM_BASE_URL, s.SELFHOSTED_LLM_API_KEY or "none", s.SELFHOSTED_LLM_MODEL,
                           {}, provider)
    return LLMEndpoint(name, s.REALTIME_LLM_BASE_URL, s.REALTIME_LLM_API_KEY, s.REALTIME_LLM_MODEL,
                       s.realtime_llm_extra_body, "sarvam")


def realtime_endpoints() -> List[LLMEndpoint]:
    s = get_settings()
    endpoints = [build_endpoint(s.LLM_PROVIDER, "primary")]
    if s.LLM_FALLBACK_PROVIDER and s.LLM_FALLBACK_PROVIDER != s.LLM_PROVIDER:
        endpoints.append(build_endpoint(s.LLM_FALLBACK_PROVIDER, "fallback"))
    elif s.REALTIME_LLM_FALLBACK_BASE_URL and s.REALTIME_LLM_FALLBACK_MODEL:
        endpoints.append(LLMEndpoint("fallback", s.REALTIME_LLM_FALLBACK_BASE_URL, s.REALTIME_LLM_FALLBACK_API_KEY,
                                     s.REALTIME_LLM_FALLBACK_MODEL, s.fallback_llm_extra_body))
    return endpoints


def _usage_from(payload: Dict[str, Any]) -> Optional[Usage]:
    u = payload.get("usage")
    if not u:
        return None
    details = u.get("prompt_tokens_details") or {}
    return Usage(
        input_tokens=int(u.get("prompt_tokens") or 0),
        cached_tokens=int(details.get("cached_tokens") or 0),
        output_tokens=int(u.get("completion_tokens") or 0),
    )


class LLMClient:
    """Streams chat completions across an ordered list of endpoints."""

    INTER_CHUNK_TIMEOUT = 8.0

    def __init__(
        self,
        endpoints: List[LLMEndpoint],
        *,
        ttft_timeout: Optional[float] = None,
        hard_timeout: Optional[float] = None,
        http_client: Optional[httpx.AsyncClient] = None,
    ):
        if not endpoints:
            raise ValueError("LLMClient needs at least one endpoint")
        s = get_settings()
        self.endpoints = endpoints
        self.ttft_timeout = ttft_timeout if ttft_timeout is not None else s.REALTIME_LLM_TIMEOUT_SECONDS
        self.hard_timeout = hard_timeout if hard_timeout is not None else s.REALTIME_LLM_HARD_TIMEOUT_SECONDS
        self._http = http_client or httpx.AsyncClient(timeout=httpx.Timeout(30.0, connect=5.0))

    @classmethod
    def realtime(cls, http_client: Optional[httpx.AsyncClient] = None) -> "LLMClient":
        return cls(realtime_endpoints(), http_client=http_client)

    @classmethod
    def offline(cls) -> "LLMClient":
        s = get_settings()
        if s.LLM_PROVIDER == "sarvam":
            endpoint = LLMEndpoint("offline", s.OFFLINE_LLM_BASE_URL, s.OFFLINE_LLM_API_KEY or s.REALTIME_LLM_API_KEY,
                                   s.OFFLINE_LLM_MODEL, {})
        else:
            endpoint = build_endpoint(s.LLM_PROVIDER, "offline")
        return cls([endpoint], ttft_timeout=60.0, hard_timeout=180.0,
                   http_client=httpx.AsyncClient(timeout=httpx.Timeout(180.0, connect=10.0)))

    async def aclose(self) -> None:
        await self._http.aclose()

    # ------------------------------------------------------------------
    def _body(self, ep: LLMEndpoint, messages, tools, temperature, max_tokens, stream, extra) -> Dict[str, Any]:
        body: Dict[str, Any] = {
            "model": ep.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": stream,
        }
        if stream:
            body["stream_options"] = {"include_usage": True}
        if tools:
            body["tools"] = tools
            body["tool_choice"] = "auto"
        body.update(ep.extra_body)
        body.update(extra or {})
        self._apply_provider_rules(ep, body)
        return body

    @staticmethod
    def _apply_provider_rules(ep: LLMEndpoint, body: Dict[str, Any]) -> None:
        """Differences between OpenAI-compatible servers. Sarvam requests are left exactly as they were."""
        if ep.provider == "sarvam":
            return
        effort = get_settings().LLM_REASONING_EFFORT
        body.pop("reasoning_effort", None)
        if ep.provider == "selfhosted":
            if effort:
                body["reasoning_effort"] = effort
            return
        tokens = body.pop("max_tokens")
        if is_reasoning_model(body["model"]):
            effort = effort or default_reasoning_effort(body["model"])
            body["reasoning_effort"] = effort
            body.pop("temperature", None)  # not accepted while reasoning is on
            if effort != "none":
                tokens += 400  # this limit also counts hidden reasoning, which could otherwise leave no reply
        body["max_completion_tokens"] = tokens

    async def _stream_endpoint(self, ep: LLMEndpoint, body: Dict[str, Any]) -> AsyncIterator[LLMEvent]:
        """Yields text / tool_calls / done events from one endpoint."""
        url = ep.base_url.rstrip("/") + "/chat/completions"
        pending: Dict[int, Dict[str, str]] = {}
        usage: Optional[Usage] = None
        async with self._http.stream("POST", url, headers=_headers(ep), json=body) as resp:
            if resp.status_code != 200:
                detail = (await resp.aread()).decode(errors="replace")[:300]
                raise httpx.HTTPStatusError(f"{resp.status_code}: {detail}", request=resp.request, response=resp)
            async for line in resp.aiter_lines():
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    break
                payload = json.loads(data)
                usage = _usage_from(payload) or usage
                for choice in payload.get("choices") or []:
                    delta = choice.get("delta") or {}
                    if delta.get("content"):
                        yield LLMEvent(type="text", text=delta["content"], endpoint=ep.name)
                    for tc in delta.get("tool_calls") or []:
                        slot = pending.setdefault(int(tc.get("index", 0)), {"id": "", "name": "", "arguments": "", "extra_content": None})
                        if tc.get("id"):
                            slot["id"] = tc["id"]
                        if tc.get("extra_content"):
                            slot["extra_content"] = tc["extra_content"]
                        fn = tc.get("function") or {}
                        if fn.get("name"):
                            slot["name"] += fn["name"]
                        if fn.get("arguments"):
                            slot["arguments"] += fn["arguments"]
        if pending:
            calls = [ToolCall(id=v["id"] or f"call_{i}", name=v["name"], arguments=v["arguments"],
                              extra_content=v["extra_content"])
                     for i, v in sorted(pending.items()) if v["name"]]
            if calls:
                yield LLMEvent(type="tool_calls", tool_calls=calls, endpoint=ep.name)
        yield LLMEvent(type="done", usage=usage or Usage(), endpoint=ep.name)

    async def stream_chat(
        self,
        messages: List[Dict[str, Any]],
        *,
        tools: Optional[List[Dict[str, Any]]] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        extra_body: Optional[Dict[str, Any]] = None,
        purpose: str = "reply",
    ) -> AsyncIterator[LLMEvent]:
        """Streams one completion, failing over between endpoints.

        Emits at most one `stall` event when the first endpoint misses the
        first-token deadline; the caller plays a cached filler line.
        """
        s = get_settings()
        temperature = s.REALTIME_LLM_TEMPERATURE if temperature is None else temperature
        max_tokens = max_tokens or s.REALTIME_LLM_MAX_TOKENS
        candidates = [ep for ep in self.endpoints if not _breaker(ep.name).is_open] or self.endpoints[-1:]
        deadline = time.monotonic() + self.hard_timeout
        stalled = False
        last_error: Optional[BaseException] = None

        for idx, ep in enumerate(candidates):
            is_last = idx == len(candidates) - 1
            body = self._body(ep, messages, tools, temperature, max_tokens, True, extra_body)
            start = time.monotonic()
            # The stream is pumped into a queue so a timed-out wait never kills the stream itself
            queue: asyncio.Queue = asyncio.Queue()
            pump = asyncio.create_task(self._pump(self._stream_endpoint(ep, body), queue))
            got_first = False
            with llm_span(purpose, ep.model, messages) as span:
                try:
                    while True:
                        remaining = deadline - time.monotonic()
                        if got_first:
                            budget = self.INTER_CHUNK_TIMEOUT
                        elif is_last and stalled:
                            budget = remaining  # filler already played; wait out the hard deadline
                        else:
                            budget = min(self.ttft_timeout, remaining)
                        budget = max(0.05, budget)
                        try:
                            item = await asyncio.wait_for(queue.get(), timeout=budget)
                        except asyncio.TimeoutError:
                            if got_first:
                                raise
                            m.record_vendor_error("llm", "ttft_timeout")
                            if not stalled:
                                stalled = True
                                yield LLMEvent(type="stall", endpoint=ep.name)
                            if not is_last or time.monotonic() >= deadline:
                                raise
                            continue
                        if item is None:
                            break
                        if isinstance(item, BaseException):
                            raise item
                        if not got_first:
                            got_first = True
                            ttft_ms = (time.monotonic() - start) * 1000
                            m.observe_llm_ttft(ttft_ms / 1000, ep.name, purpose)
                            item.ttft_ms = ttft_ms
                            span.set_attribute("gen_ai.ttft_ms", ttft_ms)
                        if item.type == "done" and item.usage:
                            span.set_attribute("gen_ai.usage.input_tokens", item.usage.input_tokens)
                            span.set_attribute("gen_ai.usage.output_tokens", item.usage.output_tokens)
                        yield item
                    _breaker(ep.name).record_success()
                    m.record_vendor_call("llm", ok=True)
                    return
                except (asyncio.TimeoutError, httpx.HTTPError, json.JSONDecodeError) as e:
                    last_error = e
                    _breaker(ep.name).record_failure()
                    m.record_vendor_call("llm", ok=False)
                    span.set_attribute("error", repr(e)[:200])
                    logger.warning(f"LLM endpoint '{ep.name}' failed ({purpose}): {e!r}")
                    if got_first:
                        # Text already reached the caller; do not splice a second model mid-reply
                        raise LLMUnavailableError(str(e)) from e
                finally:
                    pump.cancel()
        raise LLMUnavailableError(f"All LLM endpoints failed: {last_error!r}")

    @staticmethod
    async def _pump(gen: AsyncIterator[LLMEvent], queue: asyncio.Queue) -> None:
        try:
            async for event in gen:
                await queue.put(event)
            await queue.put(None)
        except asyncio.CancelledError:
            raise
        except BaseException as e:  # surfaced to the consumer
            await queue.put(e)

    async def complete(
        self,
        messages: List[Dict[str, Any]],
        *,
        temperature: float = 0.0,
        max_tokens: int = 400,
        json_mode: bool = False,
        extra_body: Optional[Dict[str, Any]] = None,
        purpose: str = "completion",
    ) -> tuple:
        """Non-streaming completion. Returns (text, Usage)."""
        last_error: Optional[BaseException] = None
        for ep in [e for e in self.endpoints if not _breaker(e.name).is_open] or self.endpoints[-1:]:
            body = self._body(ep, messages, None, temperature, max_tokens, False, extra_body)
            if json_mode:
                body["response_format"] = {"type": "json_object"}
            url = ep.base_url.rstrip("/") + "/chat/completions"
            with llm_span(purpose, ep.model, messages) as span:
                try:
                    resp = await asyncio.wait_for(
                        self._http.post(url, headers=_headers(ep), json=body), timeout=self.hard_timeout
                    )
                    if resp.status_code == 400 and json_mode:
                        body.pop("response_format", None)  # endpoint does not support JSON mode
                        resp = await self._http.post(url, headers=_headers(ep), json=body)
                    resp.raise_for_status()
                    payload = resp.json()
                    text = (payload["choices"][0]["message"].get("content") or "").strip()
                    usage = _usage_from(payload) or Usage()
                    span.set_attribute("gen_ai.usage.output_tokens", usage.output_tokens)
                    _breaker(ep.name).record_success()
                    m.record_vendor_call("llm", ok=True)
                    return text, usage
                except (asyncio.TimeoutError, httpx.HTTPError, KeyError, IndexError, ValueError) as e:
                    last_error = e
                    _breaker(ep.name).record_failure()
                    m.record_vendor_call("llm", ok=False)
                    logger.warning(f"LLM completion via '{ep.name}' failed ({purpose}): {e!r}")
        raise LLMUnavailableError(f"All LLM endpoints failed: {last_error!r}")


def parse_json_object(text: str) -> Dict[str, Any]:
    """Extracts the first JSON object from model text (tolerates code fences)."""
    if not text:
        return {}
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        return {}
    try:
        value = json.loads(text[start:end + 1])
        return value if isinstance(value, dict) else {}
    except json.JSONDecodeError:
        return {}
