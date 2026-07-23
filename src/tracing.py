"""LangSmith tracing shim.

`traceable` decorates every agent/stage function; it is a strict no-op unless
LANGSMITH_TRACING=true is set in the environment, and the import is guarded so
a missing/broken langsmith install can never take the pipeline down.
"""

from __future__ import annotations

try:
    from langsmith import traceable
except Exception:  # pragma: no cover

    def traceable(*dargs, **dkwargs):  # type: ignore[no-redef]
        if len(dargs) == 1 and callable(dargs[0]) and not dkwargs:
            return dargs[0]

        def _decorator(fn):
            return fn

        return _decorator


__all__ = ["traceable"]
