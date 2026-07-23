"""OpenRouter (OpenAI-compatible) client + schema-validated JSON completions.

Every agent call goes through `complete_json`: the target Pydantic schema is
embedded in the system prompt, the response is parsed and validated, and on
JSON/schema failure the error is fed back for a corrective re-ask (bounded by
settings.llm.json_retries). LangSmith tracing is attached via `wrap_openai`,
which is a no-op unless LANGSMITH_TRACING=true.
"""

from __future__ import annotations

import json
import re
from typing import Callable, TypeVar

import httpx
from openai import AsyncOpenAI
from pydantic import BaseModel, ValidationError

try:  # tracing is optional at runtime; never let it block the pipeline
    from langsmith.wrappers import wrap_openai
except Exception:  # pragma: no cover
    wrap_openai = None  # type: ignore[assignment]

from src.settings import Settings, env

M = TypeVar("M", bound=BaseModel)

_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL)


class LLMOutputError(RuntimeError):
    """Model kept producing output that fails schema validation."""


def make_client(settings: Settings) -> AsyncOpenAI:
    client = AsyncOpenAI(
        base_url=settings.llm.base_url,
        api_key=env("OPENROUTER_API_KEY") or "missing-key",
        # Without an explicit timeout the SDK waits up to 600s per request (and
        # silently retries twice) — a wedged provider call would look like a
        # frozen pipeline. Cap it so hangs fail fast into the visible
        # with_retry → StageFailure path instead.
        timeout=httpx.Timeout(settings.llm.request_timeout_sec, connect=10.0),
        max_retries=1,
        default_headers={
            # OpenRouter attribution headers (optional but recommended)
            "HTTP-Referer": "https://localhost/reels-agent",
            "X-Title": "reels-agent",
        },
    )
    if wrap_openai is not None:
        client = wrap_openai(client)
    return client


def _extract_json(text: str) -> dict:
    candidate = text.strip()
    fence = _FENCE_RE.search(candidate)
    if fence:
        candidate = fence.group(1).strip()
    if not candidate.startswith("{"):
        start, end = candidate.find("{"), candidate.rfind("}")
        if start == -1 or end <= start:
            raise json.JSONDecodeError("no JSON object found", candidate, 0)
        candidate = candidate[start : end + 1]
    return json.loads(candidate)


async def complete_json(
    client: AsyncOpenAI,
    settings: Settings,
    *,
    model: str,
    schema: type[M],
    system: str,
    user: str,
    temperature: float = 0.7,
    post_validate: Callable[[M], None] | None = None,
) -> M:
    """One agent call → validated instance of `schema`.

    `post_validate` may raise ValueError for semantic checks the JSON schema
    can't express (e.g. fact_id / scene_id cross-references); its message is
    fed back to the model on the corrective retry.
    """
    schema_json = json.dumps(schema.model_json_schema(), separators=(",", ":"))
    system_full = (
        f"{system}\n\n"
        "Respond with a single JSON object and nothing else — no prose, no markdown "
        "fences. The JSON must validate against this JSON Schema:\n"
        f"{schema_json}"
    )
    messages: list[dict] = [
        {"role": "system", "content": system_full},
        {"role": "user", "content": user},
    ]

    kwargs: dict = {}
    if settings.llm.force_json_mode:
        kwargs["response_format"] = {"type": "json_object"}
        if settings.llm.require_json_capable_provider:
            kwargs["extra_body"] = {"provider": {"require_parameters": True}}

    last_err: Exception | None = None
    for _attempt in range(settings.llm.json_retries + 1):
        response = await client.chat.completions.create(
            model=model,
            messages=messages,
            max_tokens=settings.llm.max_output_tokens,
            temperature=temperature,
            **kwargs,
        )
        text = (response.choices[0].message.content or "").strip()
        try:
            parsed = schema.model_validate(_extract_json(text))
            if post_validate is not None:
                post_validate(parsed)
            return parsed
        except (json.JSONDecodeError, ValidationError, ValueError) as err:
            last_err = err
            messages.append({"role": "assistant", "content": text})
            messages.append(
                {
                    "role": "user",
                    "content": (
                        f"That response was invalid: {err}\n"
                        "Return ONLY the corrected JSON object, nothing else."
                    ),
                }
            )
    raise LLMOutputError(
        f"model '{model}' failed schema validation after "
        f"{settings.llm.json_retries + 1} attempts: {last_err}"
    )
