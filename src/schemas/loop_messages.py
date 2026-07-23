"""PRD §5.2 — Script-loop internal contracts.

5.2a Strategist → Writer command (backbone | revision shapes)
5.2b Writer → Strategist execution (full script draft every round)
     Convergence decision (Strategist-internal)
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator

Phase = Literal["backbone", "revision"]


class Angle(BaseModel):
    """Locked after round 1; read-only context for the Writer all loop."""

    chosen_angle: str
    hook: str


class BackboneSection(BaseModel):
    section_id: str
    purpose: str
    note: str
    facts_to_use: list[str] = Field(default_factory=list)


class Backbone(BaseModel):
    """One-time whole-script blueprint, sent only on round 1."""

    target_length_sec: int = Field(ge=30, le=60)
    sections: list[BackboneSection] = Field(min_length=1)


class EmphasisPrimary(BaseModel):
    scene_ref: int
    change_type: str
    note: str
    facts_to_use: list[str] = Field(default_factory=list)


class EmphasisSecondary(BaseModel):
    scene_ref: int
    note: str


class Emphasis(BaseModel):
    """Revision-round focus: exactly one primary major change + optional lighter touches."""

    primary: EmphasisPrimary
    secondary: list[EmphasisSecondary] = Field(default_factory=list)


class StrategistCommand(BaseModel):
    """§5.2a — phase selects the shape: backbone block XOR emphasis block."""

    iteration: int = Field(ge=1)
    phase: Phase
    content_architecture: str
    angle: Angle
    backbone: Backbone | None = None
    emphasis: Emphasis | None = None

    @model_validator(mode="after")
    def _phase_shape(self) -> "StrategistCommand":
        if self.phase == "backbone":
            if self.backbone is None:
                raise ValueError("backbone phase requires the backbone block")
            if self.emphasis is not None:
                raise ValueError("backbone phase must not carry an emphasis block")
            if self.iteration != 1:
                raise ValueError("backbone phase only occurs on iteration 1")
        else:
            if self.emphasis is None:
                raise ValueError("revision phase requires the emphasis block")
            if self.backbone is not None:
                raise ValueError("revision phase must not carry a backbone block")
            if self.iteration < 2:
                raise ValueError("revision phase starts at iteration 2")
        return self


class SceneDraft(BaseModel):
    scene_id: int
    section_id: str
    on_screen_text: str
    narration: str
    visual_keyword: str
    audio_emphasis: str
    approx_duration_sec: float = Field(gt=0)


class ScriptDraft(BaseModel):
    title: str
    target_length_sec: int
    status: Literal["complete"] = "complete"
    scenes: list[SceneDraft] = Field(min_length=1)

    def scene_ids(self) -> set[int]:
        return {s.scene_id for s in self.scenes}

    def total_duration_sec(self) -> float:
        return round(sum(s.approx_duration_sec for s in self.scenes), 1)


class WriterExecution(BaseModel):
    """§5.2b — the Writer executes and returns; no strategy, no convergence fields."""

    iteration: int = Field(ge=1)
    phase: Phase
    revised_scene_ids: list[int] = Field(default_factory=list)
    script_draft: ScriptDraft

    @model_validator(mode="after")
    def _backbone_has_no_revisions(self) -> "WriterExecution":
        if self.phase == "backbone" and self.revised_scene_ids:
            raise ValueError("revised_scene_ids must be empty on the backbone pass")
        return self


class ConvergenceDecision(BaseModel):
    """Strategist-internal; drives the next command or the §5.3 exit."""

    iteration: int = Field(ge=1)
    assessment_notes: list[str] = Field(default_factory=list)
    converged: bool
