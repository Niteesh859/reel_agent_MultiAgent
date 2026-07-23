"""PRD §5.3 — converged Script Loop → Producer output (the operator-reviewed package)
and §5.4 — Producer → Assembler manifest (defined now, consumed in Phase 2)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class PathConsidered(BaseModel):
    path: str
    why_not_chosen: str | None = None


class FinalAngle(BaseModel):
    chosen_angle: str
    hook: str
    rationale: str
    paths_considered: list[PathConsidered] = Field(default_factory=list)


class FinalScene(BaseModel):
    scene_id: int
    on_screen_text: str
    narration: str
    visual_keyword: str
    audio_emphasis: str
    approx_duration_sec: float = Field(gt=0)


class ConvergedScript(BaseModel):
    title: str
    target_length_sec: int
    final_angle: FinalAngle
    iterations_used: int
    convergence: Literal["agreed", "cap_reached"]
    scenes: list[FinalScene] = Field(min_length=1)

    def total_duration_sec(self) -> float:
        return round(sum(s.approx_duration_sec for s in self.scenes), 1)

    def scene_ids(self) -> set[int]:
        return {s.scene_id for s in self.scenes}

    def renumber(self) -> None:
        for i, scene in enumerate(self.scenes, start=1):
            scene.scene_id = i


# ── §5.4 Producer → Assembler (Phase 2; schema locked now per PRD phasing note) ──


class MusicTrack(BaseModel):
    name: str
    file: str


class SceneVisual(BaseModel):
    type: Literal["image", "gif"]
    source: Literal["giphy", "pexels", "unsplash"]
    file: str


class NarrationAudio(BaseModel):
    file: str
    duration_sec: float


class ProducedScene(BaseModel):
    scene_id: int
    visual: SceneVisual
    narration_audio: NarrationAudio
    on_screen_text: str
    caption_style: Literal["word_highlight"] = "word_highlight"
    audio_emphasis: str


class ProducerManifest(BaseModel):
    title: str
    music_track: MusicTrack
    scenes: list[ProducedScene]
