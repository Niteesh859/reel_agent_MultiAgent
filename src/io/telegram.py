"""Telegram checkpoint channel (PRD §6) — full parity with the TUI.

Zero-argument decisions are inline buttons (Approve / Show full / Skip); typed
messages carry edits and regenerate-notes using the exact same command syntax
as the TUI. Replying to a per-scene message (sent by "show full") edits that
scene directly. Auto-enabled when TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID are set.

Resilience: `verify()` is called once at startup (bad token/chat falls back to
TUI-only instead of failing mid-run); sends retry once on transport errors; the
long-poll loop rides out network blips. Telegram-API-level errors (bad token,
polling conflict) still raise so the checkpoint layer can drop the channel
visibly rather than retry forever.
"""

from __future__ import annotations

import asyncio
import re

import httpx

from src.io.checkpoint import ACTION_BAR, CheckpointAction, CheckpointView, parse_action

_MAX_MSG = 3900  # Telegram hard limit is 4096
_FIELD_PREFIX = re.compile(
    r"^(narration|text|visual|emphasis|duration)\s*[:=]", re.IGNORECASE
)
_QUICK_TIMEOUT = httpx.Timeout(8.0, connect=5.0)


class TelegramChannel:
    name = "telegram"

    def __init__(self, token: str, chat_id: str, poll_timeout_sec: int = 25):
        self.chat_id = str(chat_id)
        self.poll_timeout = poll_timeout_sec
        self._http = httpx.AsyncClient(
            base_url=f"https://api.telegram.org/bot{token}",
            timeout=httpx.Timeout(15.0, connect=10.0),
        )
        self._poll_http_timeout = httpx.Timeout(poll_timeout_sec + 15.0, connect=10.0)
        self._offset: int | None = None
        self._scene_messages: dict[int, int] = {}  # message_id -> scene_id

    async def _api(
        self, method: str, *, http_timeout: httpx.Timeout | None = None, **params
    ) -> dict:
        kwargs: dict = {"json": params}
        if http_timeout is not None:
            kwargs["timeout"] = http_timeout
        response = await self._http.post(f"/{method}", **kwargs)
        payload = response.json()
        if not payload.get("ok"):
            raise RuntimeError(f"telegram {method} failed: {payload.get('description')}")
        return payload["result"]

    async def verify(self) -> str:
        """Startup probe: confirm the token AND that the bot can reach the chat
        (the latter fails until the operator has pressed Start on the bot)."""
        me = await self._api("getMe", http_timeout=_QUICK_TIMEOUT)
        await self._api(
            "sendMessage",
            http_timeout=_QUICK_TIMEOUT,
            chat_id=self.chat_id,
            text="🤖 reels-agent checkpoint channel connected",
        )
        return me.get("username", "bot")

    async def _post_chunk(self, **params) -> dict:
        try:
            return await self._api("sendMessage", **params)
        except httpx.HTTPError:  # transient transport error — one retry
            await asyncio.sleep(2.0)
            return await self._api("sendMessage", **params)

    def _chunks(self, text: str) -> list[str]:
        if len(text) <= _MAX_MSG:
            return [text]
        chunks, current = [], ""
        for line in text.split("\n"):
            if len(current) + len(line) + 1 > _MAX_MSG:
                chunks.append(current)
                current = line
            else:
                current = f"{current}\n{line}" if current else line
        if current:
            chunks.append(current)
        return chunks

    async def _send(self, text: str, reply_markup: dict | None = None) -> int:
        message_id = 0
        chunks = self._chunks(text)
        for i, chunk in enumerate(chunks):
            result = await self._post_chunk(
                chat_id=self.chat_id,
                text=chunk,
                disable_web_page_preview=True,
                **({"reply_markup": reply_markup} if reply_markup and i == len(chunks) - 1 else {}),
            )
            message_id = result["message_id"]
        return message_id

    async def present_summary(self, view: CheckpointView) -> None:
        text = "\n".join(
            [
                f"◇ CHECKPOINT · {view.stage}",
                *view.header,
                "",
                *view.summary,
                "",
                f"Type edits or a note — {ACTION_BAR}",
            ]
        )
        keyboard = {
            "inline_keyboard": [
                [
                    {"text": "✅ Approve", "callback_data": "approve"},
                    {"text": "📄 Show full", "callback_data": "full"},
                ],
                [{"text": "🛑 Skip reel", "callback_data": "skip"}],
            ]
        }
        await self._send(text, reply_markup=keyboard)

    async def present_full(self, view: CheckpointView) -> None:
        await self._send("\n".join(view.full))
        if view.scene_lines:
            self._scene_messages.clear()
            await self._send("↩️ Reply to any scene message below to edit it directly:")
            for scene_id, line in view.scene_lines.items():
                message_id = await self._send(line)
                self._scene_messages[message_id] = scene_id

    async def notify(self, text: str) -> None:
        await self._send(text)

    def _action_from_message(self, message: dict) -> CheckpointAction | None:
        if str(message.get("chat", {}).get("id")) != self.chat_id:
            return None
        text = (message.get("text") or "").strip()
        if not text:
            return None
        reply = message.get("reply_to_message") or {}
        scene_id = self._scene_messages.get(reply.get("message_id", -1))
        if scene_id is not None:
            if _FIELD_PREFIX.match(text):
                return CheckpointAction("edit", f"edit scene {scene_id} {text}")
            return CheckpointAction("edit", f"edit scene {scene_id} narration: {text}")
        return parse_action(text)

    async def next_action(self) -> CheckpointAction:
        while True:
            try:
                updates = await self._api(
                    "getUpdates",
                    http_timeout=self._poll_http_timeout,
                    timeout=self.poll_timeout,
                    allowed_updates=["message", "callback_query"],
                    **({"offset": self._offset} if self._offset is not None else {}),
                )
            except httpx.HTTPError:  # network blip — keep the channel alive
                await asyncio.sleep(3.0)
                continue
            for update in updates:
                self._offset = update["update_id"] + 1
                if callback := update.get("callback_query"):
                    try:
                        await self._api(
                            "answerCallbackQuery", callback_query_id=callback["id"]
                        )
                    except (RuntimeError, httpx.HTTPError):
                        pass  # cosmetic ack; the action itself still counts
                    if str(callback.get("message", {}).get("chat", {}).get("id")) != self.chat_id:
                        continue
                    data = callback.get("data")
                    if data == "approve":
                        return CheckpointAction("approve")
                    if data == "full":
                        return CheckpointAction("show_full")
                    if data == "skip":
                        return CheckpointAction("skip")
                    continue
                if message := update.get("message"):
                    action = self._action_from_message(message)
                    if action is not None:
                        return action

    async def aclose(self) -> None:
        await self._http.aclose()
