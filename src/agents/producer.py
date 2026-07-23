"""Agent 4 — Producer. Phase 2 stub (PRD §12): visuals + TTS + music per scene."""

from __future__ import annotations

from src.schemas.outputs import ConvergedScript, ProducerManifest


async def produce(script: ConvergedScript) -> ProducerManifest:
    raise NotImplementedError(
        "Producer is a Phase 2 agent. Phase 1 ends at the exported, approved script."
    )
