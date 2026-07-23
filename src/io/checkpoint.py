"""Checkpoint orchestration (PRD §3/§6/§8).

Presents one checkpoint on every active channel with the same three-part shape
(header / summary / full-on-demand), then races the channels — the first
operator action wins. Four actions: approve, edit, regenerate-with-note,
skip-reel (plus show-full/help, which loop locally).
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from dataclasses import dataclass, field
from typing import Callable, Literal, Protocol

from src.guardrails.failures import SkipReel
from src.io.edits import EditOutcome
from src.settings import OUTPUT_DIR

ActionKind = Literal["approve", "skip", "regen", "edit", "show_full", "help", "noop"]

_REGEN_RE = re.compile(r"^regen(?:erate)?(?:\s+with\s+note)?\s*[:\-]?\s*(.*)$", re.IGNORECASE | re.DOTALL)

ACTION_BAR = (
    "actions: approve · edit <cmd> · regen: <note> · skip · full · help"
)


@dataclass
class CheckpointAction:
    kind: ActionKind
    payload: str | None = None


@dataclass
class CheckpointView:
    reel_id: str
    stage: str
    header: list[str]
    summary: list[str]
    full: list[str]
    scene_lines: dict[int, str] = field(default_factory=dict)


@dataclass
class CheckpointResult:
    kind: Literal["approve", "regen"]
    note: str | None = None


class Channel(Protocol):
    name: str

    async def present_summary(self, view: CheckpointView) -> None: ...
    async def present_full(self, view: CheckpointView) -> None: ...
    async def notify(self, text: str) -> None: ...
    async def next_action(self) -> CheckpointAction: ...
    async def aclose(self) -> None: ...


def parse_action(text: str) -> CheckpointAction:
    stripped = text.strip()
    if not stripped:
        return CheckpointAction("noop")
    low = stripped.lower()
    if low in {"approve", "a", "ok", "yes", "y", "lgtm"}:
        return CheckpointAction("approve")
    if low in {"skip", "skip reel", "abandon", "abort"}:
        return CheckpointAction("skip")
    if low in {"full", "show full", "f", "detail", "details"}:
        return CheckpointAction("show_full")
    if low in {"help", "h", "?", "commands"}:
        return CheckpointAction("help")
    if m := _REGEN_RE.match(stripped):
        note = m.group(1).strip()
        return CheckpointAction("regen", note or None)
    return CheckpointAction("edit", stripped)


def append_metric(reel_id: str, stage: str, action: str, note: str | None = None) -> None:
    """KPI trail (PRD §2): one JSONL row per checkpoint action → output/metrics.jsonl."""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    record = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "reel_id": reel_id,
        "stage": stage,
        "action": action,
        "note": note,
    }
    with (OUTPUT_DIR / "metrics.jsonl").open("a") as fh:
        fh.write(json.dumps(record) + "\n")


async def _notify_all(channels: list[Channel], text: str) -> None:
    await asyncio.gather(*(ch.notify(text) for ch in channels), return_exceptions=True)


async def run_checkpoint(
    view_fn: Callable[[], CheckpointView],
    channels: list[Channel],
    apply_edit: Callable[[str], EditOutcome],
    edit_help: str,
) -> CheckpointResult:
    """Blocks until the operator approves, regenerates, or skips. Edits are
    applied to the live artifact (via apply_edit) and the refreshed summary is
    re-presented on every channel so both stay in sync."""
    if not channels:
        raise RuntimeError("no checkpoint channels available")

    view = view_fn()
    active = list(channels)
    await asyncio.gather(
        *(ch.present_summary(view) for ch in active), return_exceptions=True
    )

    while True:
        tasks: dict[asyncio.Task, Channel] = {
            asyncio.create_task(ch.next_action()): ch for ch in active
        }
        try:
            done, pending = await asyncio.wait(
                tasks, return_when=asyncio.FIRST_COMPLETED
            )
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

        winner = done.pop()
        source = tasks[winner]
        try:
            action = winner.result()
        except asyncio.CancelledError:
            continue
        except Exception as err:  # noqa: BLE001 — drop the broken channel, keep going
            active = [ch for ch in active if ch is not source]
            if not active:
                raise RuntimeError(f"all checkpoint channels failed (last: {err})")
            await _notify_all(
                active, f"channel '{source.name}' errored ({err}); continuing without it"
            )
            continue

        view = view_fn()
        if action.kind == "noop":
            continue
        if action.kind == "help":
            await source.notify(f"{edit_help}\n\n{ACTION_BAR}")
            continue
        if action.kind == "show_full":
            await source.present_full(view)
            continue
        if action.kind == "edit":
            outcome = apply_edit(action.payload or "")
            append_metric(view.reel_id, view.stage, "edit", action.payload)
            if outcome.ok:
                view = view_fn()
                await _notify_all(active, f"✏️  {outcome.message}")
                await asyncio.gather(
                    *(ch.present_summary(view) for ch in active),
                    return_exceptions=True,
                )
            else:
                await source.notify(f"⚠️  {outcome.message}")
            continue
        if action.kind == "approve":
            append_metric(view.reel_id, view.stage, "approve")
            await _notify_all(active, f"✅ {view.stage} approved — advancing")
            return CheckpointResult("approve")
        if action.kind == "regen":
            append_metric(view.reel_id, view.stage, "regen", action.payload)
            await _notify_all(active, "🔁 regenerating stage with your note…")
            return CheckpointResult("regen", action.payload)
        if action.kind == "skip":
            append_metric(view.reel_id, view.stage, "skip")
            await _notify_all(active, "🛑 reel skipped")
            raise SkipReel(view.reel_id)
