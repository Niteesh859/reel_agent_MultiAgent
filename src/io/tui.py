"""Terminal review channel (PRD §6) — denser/faster review at the keyboard."""

from __future__ import annotations

import asyncio
import sys
import threading

from rich.console import Console
from rich.panel import Panel

from src.io.checkpoint import ACTION_BAR, CheckpointAction, CheckpointView, parse_action

_STDIN_EOF = "__stdin_eof__"


class TuiChannel:
    name = "tui"

    def __init__(self, console: Console | None = None):
        self.console = console or Console()
        self._queue: asyncio.Queue[str] = asyncio.Queue()
        self._reader_started = False

    def _start_reader(self) -> None:
        """One persistent stdin reader thread feeding an asyncio queue, so a
        pending prompt can be cancelled cleanly when Telegram wins the race."""
        if self._reader_started:
            return
        self._reader_started = True
        loop = asyncio.get_running_loop()

        def _read() -> None:
            for line in sys.stdin:
                loop.call_soon_threadsafe(self._queue.put_nowait, line.rstrip("\n"))
            loop.call_soon_threadsafe(self._queue.put_nowait, _STDIN_EOF)

        threading.Thread(target=_read, name="tui-stdin", daemon=True).start()

    async def present_summary(self, view: CheckpointView) -> None:
        header = "\n".join(view.header)
        body = "\n".join(view.summary)
        self.console.print()
        self.console.print(
            Panel(
                f"[bold cyan]{header}[/bold cyan]\n\n{body}",
                title=f"◇ CHECKPOINT · {view.stage}",
                subtitle=ACTION_BAR,
                border_style="cyan",
            )
        )

    async def present_full(self, view: CheckpointView) -> None:
        self.console.print(
            Panel(
                "\n".join(view.full),
                title=f"full detail · {view.stage}",
                border_style="blue",
            )
        )

    async def notify(self, text: str) -> None:
        self.console.print(f"[dim]›[/dim] {text}")

    async def next_action(self) -> CheckpointAction:
        self._start_reader()
        line = await self._queue.get()
        if line == _STDIN_EOF:
            await self.notify("stdin closed — skipping reel")
            return CheckpointAction("skip")
        return parse_action(line)

    async def aclose(self) -> None:  # daemon reader dies with the process
        return None
