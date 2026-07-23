"""Backbone (round 1) vs revision (round 2+) logic — PRD §10 loop/phase.py."""

from __future__ import annotations

from src.schemas.loop_messages import (
    Angle,
    Emphasis,
    Phase,
    StrategistCommand,
)


def phase_for(iteration: int) -> Phase:
    return "backbone" if iteration == 1 else "revision"


def make_revision_command(
    iteration: int,
    angle: Angle,
    content_architecture: str,
    emphasis: Emphasis,
) -> StrategistCommand:
    """Round 2+ command. `angle` and `content_architecture` ride along unchanged
    every round (the persistent spine); only the emphasis is new."""
    return StrategistCommand(
        iteration=iteration,
        phase="revision",
        content_architecture=content_architecture,
        angle=angle,
        emphasis=emphasis,
    )
