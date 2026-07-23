"""Agent 3 — Writer (PRD §4).

Executes the Strategist's commands as script text. Round 1: full script from the
backbone skeleton in one pass. Round 2+: targeted revision of the emphasized
parts, rest untouched except continuity. Never pushes back on strategy.
"""

from __future__ import annotations

import json

from openai import AsyncOpenAI
from pydantic import BaseModel, Field

from src.llm import complete_json
from src.schemas.loop_messages import (
    SceneDraft,
    ScriptDraft,
    StrategistCommand,
    WriterExecution,
)
from src.schemas.research import ResearchBrief
from src.settings import Settings
from src.tracing import traceable

_SYSTEM = """You are the WRITER in a two-agent script loop for Instagram Reels
(vertical, 30-60 seconds, tone: {tone}).

A Strategist directs; you execute. You own wording only — word choice, phrasing,
pacing. Do NOT push back on the strategy, change the angle, or restructure beyond
what the command asks. Focus entirely on HOW to render each command as effective
script text.

Scene field rules:
- narration: the exact words a TTS voice will speak. Natural spoken language,
  short punchy sentences, numbers written to be heard. Facts are woven into the
  story — never read out citations or URLs.
- on_screen_text: short overlay text, max ~8 words, punchy, complements (not
  duplicates) the narration.
- visual_keyword: 2-4 word search term the Producer will use on Giphy/Pexels.
- audio_emphasis: 1-2 things the TTS should vocally stress in this scene.
- approx_duration_sec: realistic spoken length (~2.5 words/second of narration)."""

_BACKBONE_TASK = """Write the COMPLETE script in one pass from the Strategist's
backbone below. Turn each section into 1-2 scenes, in section order. Every scene's
section_id must be one of the backbone section ids. Open scene 1 with the hook
(you may polish its wording, keep its promise identical). Total duration must land
near the target ({target}s, hard range 30-60s)."""

_REVISION_TASK = """Revise the current script per the Strategist's emphasis command.

- PRIMARY (must change substantially): scene {primary_ref} — {primary_type}.
  Direction: {primary_note}{primary_facts}
- SECONDARY (lighter touches): {secondary}

Apply the major change to the primary scene, the lighter adjustments to secondary
scenes, and leave every other scene UNTOUCHED unless a small continuity tweak is
unavoidable. If the change_type is "cut", remove the scene. Return the FULL updated
script (every scene, changed or not) and list the scene_ids you actually changed in
revised_scene_ids."""


class _SceneBackboneLLM(BaseModel):
    section_id: str
    on_screen_text: str
    narration: str
    visual_keyword: str
    audio_emphasis: str
    approx_duration_sec: float = Field(gt=0)


class _BackboneOutLLM(BaseModel):
    title: str
    scenes: list[_SceneBackboneLLM] = Field(min_length=3, max_length=12)


class _SceneRevisionLLM(BaseModel):
    scene_id: int
    section_id: str
    on_screen_text: str
    narration: str
    visual_keyword: str
    audio_emphasis: str
    approx_duration_sec: float = Field(gt=0)


class _RevisionOutLLM(BaseModel):
    title: str
    revised_scene_ids: list[int] = Field(min_length=1)
    scenes: list[_SceneRevisionLLM] = Field(min_length=3, max_length=12)


def _facts_appendix(brief: ResearchBrief) -> str:
    lines = ["RESEARCH FACTS (referenced by fact_id):"]
    for f in brief.facts:
        lines.append(f"- {f.fact_id}: {f.claim} [source: {f.source_name}]")
    return "\n".join(lines)


class Writer:
    def __init__(self, client: AsyncOpenAI, settings: Settings):
        self.client = client
        self.settings = settings
        self.model = settings.models.writer
        self.temperature = settings.llm.temperatures.get("writer", 0.8)
        self.reasoning_effort = settings.llm.reasoning_efforts.get("writer")

    def _system(self) -> str:
        return _SYSTEM.format(tone=self.settings.pipeline.tone)

    @traceable(name="writer-execute", run_type="chain")
    async def execute(
        self,
        brief: ResearchBrief,
        command: StrategistCommand,
        current_script: ScriptDraft | None = None,
    ) -> WriterExecution:
        if command.phase == "backbone":
            return await self._execute_backbone(brief, command)
        if current_script is None:
            raise ValueError("revision command requires the current script")
        return await self._execute_revision(brief, command, current_script)

    async def _execute_backbone(
        self, brief: ResearchBrief, command: StrategistCommand
    ) -> WriterExecution:
        assert command.backbone is not None
        backbone = command.backbone
        section_ids = {s.section_id for s in backbone.sections}

        def _check(out: _BackboneOutLLM) -> None:
            unknown = {s.section_id for s in out.scenes} - section_ids
            if unknown:
                raise ValueError(
                    f"scene section_id values {sorted(unknown)} are not backbone "
                    f"section ids {sorted(section_ids)}"
                )

        skeleton_lines = [
            f"- {s.section_id} [{s.purpose}]: {s.note} "
            f"(facts: {', '.join(s.facts_to_use) or 'writer’s choice'})"
            for s in backbone.sections
        ]
        user = "\n\n".join(
            [
                _BACKBONE_TASK.format(target=backbone.target_length_sec),
                f"ANGLE (read-only): {command.angle.chosen_angle}\n"
                f"HOOK (read-only): {command.angle.hook}\n"
                f"CONTENT ARCHITECTURE (read-only): {command.content_architecture}",
                "BACKBONE SECTIONS:\n" + "\n".join(skeleton_lines),
                _facts_appendix(brief),
            ]
        )
        out = await complete_json(
            self.client,
            self.settings,
            model=self.model,
            schema=_BackboneOutLLM,
            system=self._system(),
            user=user,
            temperature=self.temperature,
            reasoning_effort=self.reasoning_effort,
            post_validate=_check,
        )
        scenes = [
            SceneDraft(
                scene_id=i,
                section_id=s.section_id,
                on_screen_text=s.on_screen_text,
                narration=s.narration,
                visual_keyword=s.visual_keyword,
                audio_emphasis=s.audio_emphasis,
                approx_duration_sec=s.approx_duration_sec,
            )
            for i, s in enumerate(out.scenes, start=1)
        ]
        return WriterExecution(
            iteration=command.iteration,
            phase="backbone",
            revised_scene_ids=[],
            script_draft=ScriptDraft(
                title=out.title,
                target_length_sec=backbone.target_length_sec,
                scenes=scenes,
            ),
        )

    async def _execute_revision(
        self,
        brief: ResearchBrief,
        command: StrategistCommand,
        current_script: ScriptDraft,
    ) -> WriterExecution:
        assert command.emphasis is not None
        emphasis = command.emphasis
        primary = emphasis.primary

        def _check(out: _RevisionOutLLM) -> None:
            returned_ids = {s.scene_id for s in out.scenes}
            bad = set(out.revised_scene_ids) - (
                returned_ids | current_script.scene_ids()
            )
            if bad:
                raise ValueError(
                    f"revised_scene_ids {sorted(bad)} match neither the returned "
                    "scenes nor the previous script"
                )

        primary_facts = ""
        if primary.facts_to_use:
            primary_facts = f" Incorporate facts: {', '.join(primary.facts_to_use)}."
        secondary = (
            "; ".join(f"scene {s.scene_ref}: {s.note}" for s in emphasis.secondary)
            or "(none)"
        )
        user = "\n\n".join(
            [
                _REVISION_TASK.format(
                    primary_ref=primary.scene_ref,
                    primary_type=primary.change_type,
                    primary_note=primary.note,
                    primary_facts=primary_facts,
                    secondary=secondary,
                ),
                f"ANGLE (read-only): {command.angle.chosen_angle}\n"
                f"HOOK (read-only): {command.angle.hook}\n"
                f"CONTENT ARCHITECTURE (read-only): {command.content_architecture}",
                "CURRENT FULL SCRIPT:\n"
                + json.dumps(current_script.model_dump(), indent=1),
                _facts_appendix(brief),
            ]
        )
        out = await complete_json(
            self.client,
            self.settings,
            model=self.model,
            schema=_RevisionOutLLM,
            system=self._system(),
            user=user,
            temperature=self.temperature,
            reasoning_effort=self.reasoning_effort,
            post_validate=_check,
        )
        scenes = [
            SceneDraft(
                scene_id=s.scene_id,
                section_id=s.section_id,
                on_screen_text=s.on_screen_text,
                narration=s.narration,
                visual_keyword=s.visual_keyword,
                audio_emphasis=s.audio_emphasis,
                approx_duration_sec=s.approx_duration_sec,
            )
            for s in out.scenes
        ]
        return WriterExecution(
            iteration=command.iteration,
            phase="revision",
            revised_scene_ids=sorted(set(out.revised_scene_ids)),
            script_draft=ScriptDraft(
                title=out.title,
                target_length_sec=current_script.target_length_sec,
                scenes=scenes,
            ),
        )
