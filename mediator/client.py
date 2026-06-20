"""Client for any OpenAI-compatible chat endpoint.

Works for both LM Studio (local) and cloud providers (OpenAI, OpenRouter, Groq,
Together, etc.) because they all implement ``/v1/chat/completions`` and, in most
cases, ``/v1/models``. Only the ``base_url`` and ``api_key`` differ.
"""

from __future__ import annotations

import json
import time
from typing import Any, Iterator

import httpx

from .config import Config, ProviderConfig

# Transient upstream errors (429 throttling, 502/503/504 overload) are common on free
# tiers; do a few short retries so a single hiccup doesn't fail an entire debate.
_MAX_TRANSIENT_RETRIES = 3
_RETRY_BACKOFFS = (3.0, 8.0, 18.0)
_RETRY_AFTER_CAP = 30.0  # don't sleep longer than this even if the server suggests it
_RETRYABLE_STATUS = {429, 502, 503, 504}


class LLMError(RuntimeError):
    """Raised for any problem talking to an LLM provider."""


# Backwards-compatible alias (Phase 1 referred to LMStudioError).
LMStudioError = LLMError


class LLMClient:
    def __init__(self, base_url: str, api_key: str = "", timeout_seconds: float = 120.0,
                 label: str = "provider") -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key or "not-needed"
        self.timeout_seconds = timeout_seconds
        self.label = label  # human-friendly name for error messages

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.api_key}"}

    def list_models(self) -> list[str]:
        url = f"{self.base_url}/models"
        try:
            resp = httpx.get(url, headers=self._headers(), timeout=self.timeout_seconds)
            resp.raise_for_status()
        except httpx.ConnectError as exc:
            raise LLMError(
                f"Could not connect to {self.label} at {self.base_url}. "
                "If this is local, is LM Studio's server running? "
                "(LM Studio -> Developer -> Start Server)"
            ) from exc
        except httpx.HTTPStatusError as exc:
            raise LLMError(
                f"{self.label} returned HTTP {exc.response.status_code} for {url}."
            ) from exc
        except httpx.HTTPError as exc:
            raise LLMError(f"Request to {url} failed: {exc}") from exc

        data = resp.json()
        return [m.get("id", "") for m in data.get("data", []) if m.get("id")]

    def chat(self, messages: list[dict[str, str]], model: str = "",
             temperature: float = 0.5) -> str:
        if not model:
            available = self.list_models()
            if not available:
                raise LLMError(
                    f"No model available from {self.label}. "
                    "Load a model in LM Studio, or set a model name in config.toml."
                )
            model = available[0]

        url = f"{self.base_url}/chat/completions"
        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
        }
        last_err: Exception | None = None
        for attempt in range(_MAX_TRANSIENT_RETRIES + 1):
            try:
                resp = httpx.post(url, json=payload, headers=self._headers(),
                                  timeout=self.timeout_seconds)
                # Transient upstream issue — wait and retry, capped.
                if resp.status_code in _RETRYABLE_STATUS and attempt < _MAX_TRANSIENT_RETRIES:
                    delay = _RETRY_BACKOFFS[attempt]
                    try:
                        delay = min(float(resp.headers.get("retry-after", delay)),
                                    _RETRY_AFTER_CAP)
                    except (TypeError, ValueError):
                        pass
                    time.sleep(delay)
                    continue
                resp.raise_for_status()
                break
            except httpx.ConnectError as exc:
                raise LLMError(
                    f"Could not connect to {self.label} at {self.base_url}. "
                    "Is the server running / the base_url correct?"
                ) from exc
            except httpx.TimeoutException as exc:
                raise LLMError(
                    f"{self.label} did not respond within {self.timeout_seconds}s."
                ) from exc
            except httpx.HTTPStatusError as exc:
                detail = exc.response.text
                if exc.response.status_code in (401, 403):
                    detail = "Authentication failed — check the API key for this provider."
                raise LLMError(
                    f"{self.label} returned HTTP {exc.response.status_code}: {detail}"
                ) from exc
            except httpx.HTTPError as exc:
                last_err = exc
                raise LLMError(f"Request to {url} failed: {exc}") from exc
        else:  # exhausted retries; resp is the last transient failure
            raise LLMError(
                f"{self.label} returned HTTP {resp.status_code} after "
                f"{_MAX_TRANSIENT_RETRIES} retries: {resp.text[:300]}"
            )

        data = resp.json()
        try:
            return data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMError(f"Unexpected response shape from {self.label}: {data}") from exc

    def chat_stream(self, messages: list[dict[str, str]], model: str = "",
                    temperature: float = 0.5) -> Iterator[str]:
        """Yield content deltas as the model produces them (token-level streaming).

        Uses the OpenAI-compatible ``stream: true`` SSE protocol, which LM Studio and
        the major cloud providers all support.
        """
        if not model:
            available = self.list_models()
            if not available:
                raise LLMError(
                    f"No model available from {self.label}. "
                    "Load a model in LM Studio, or set a model name in config.toml."
                )
            model = available[0]

        url = f"{self.base_url}/chat/completions"
        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "stream": True,
        }
        try:
            with httpx.stream("POST", url, json=payload, headers=self._headers(),
                              timeout=self.timeout_seconds) as resp:
                if resp.status_code >= 400:
                    body = resp.read().decode("utf-8", "replace")
                    if resp.status_code in (401, 403):
                        body = "Authentication failed — check the API key for this provider."
                    raise LLMError(f"{self.label} returned HTTP {resp.status_code}: {body}")
                for line in resp.iter_lines():
                    if not line:
                        continue
                    if line.startswith("data:"):
                        line = line[5:].strip()
                    if line == "[DONE]":
                        break
                    try:
                        obj = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    try:
                        delta = obj["choices"][0].get("delta", {}).get("content")
                    except (KeyError, IndexError, TypeError):
                        delta = None
                    if delta:
                        yield delta
        except httpx.ConnectError as exc:
            raise LLMError(
                f"Could not connect to {self.label} at {self.base_url}. "
                "Is the server running / the base_url correct?"
            ) from exc
        except httpx.TimeoutException as exc:
            raise LLMError(
                f"{self.label} did not respond within {self.timeout_seconds}s."
            ) from exc
        except httpx.HTTPError as exc:
            raise LLMError(f"Streaming request to {url} failed: {exc}") from exc


def make_client(provider: ProviderConfig, timeout_seconds: float, label: str) -> LLMClient:
    return LLMClient(
        base_url=provider.base_url,
        api_key=provider.resolved_key(),
        timeout_seconds=timeout_seconds,
        label=label,
    )


def client_for_role(config: Config, role: str) -> tuple[LLMClient, str]:
    """Build the client + model name for a given agent role."""
    provider = config.provider_for(role)
    agent = config.agent(role)
    label = f"{role}:{agent.provider}"
    return make_client(provider, config.timeout_seconds, label), agent.model
