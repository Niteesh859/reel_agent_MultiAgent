"""PRD §7 — infrastructure-failure guardrail: retry once, then notify + pause.

This layer handles infra/API failures only (LLM API down, search API errors,
schema-validation exhaustion). Content quality is never gated here — that is
the operator's job at the checkpoints.
"""

from __future__ import annotations

import asyncio
from typing import Any, Awaitable, Callable


class SkipReel(Exception):
    """Operator chose to abandon this reel at a checkpoint."""


class StageFailure(Exception):
    """A stage failed twice; the pipeline run must pause and notify."""

    def __init__(self, stage: str, original: Exception):
        super().__init__(f"stage '{stage}' failed after one retry: {original}")
        self.stage = stage
        self.original = original


async def with_retry(
    fn: Callable[..., Awaitable[Any]],
    *args: Any,
    stage: str,
    retry_delay_sec: float = 2.0,
    **kwargs: Any,
) -> Any:
    """Run a stage callable; on failure retry once, then raise StageFailure."""
    try:
        return await fn(*args, **kwargs)
    except (SkipReel, asyncio.CancelledError):
        raise
    except Exception as first_err:  # noqa: BLE001 — infra errors are heterogeneous
        print(
            f"[guardrail] stage '{stage}' hit {type(first_err).__name__}: "
            f"{first_err} — retrying once…",
            flush=True,
        )
        await asyncio.sleep(retry_delay_sec)
        try:
            return await fn(*args, **kwargs)
        except (SkipReel, asyncio.CancelledError):
            raise
        except Exception as second_err:  # noqa: BLE001
            raise StageFailure(stage, second_err) from first_err
