"""Agent 2 — Strategist (PRD §4).

Owns the angle, the content architecture, and every command in the script loop.
Round 1: whole-script backbone. Round 2+: reads the full script back, picks ONE
primary part for a major change (plus optional secondary touches), and decides
convergence. Never overrides the operator's core topic.
"""

from __future__ import annotations

import json

from openai import AsyncOpenAI
from pydantic import BaseModel, Field

from src.llm import complete_json
from src.schemas.loop_messages import (
    Angle,
    Backbone,
    BackboneSection,
    ConvergenceDecision,
    Emphasis,
    EmphasisPrimary,
    EmphasisSecondary,
    StrategistCommand,
    WriterExecution,
)
from src.schemas.outputs import ConvergedScript, FinalAngle, PathConsidered
from src.schemas.research import ResearchBrief
from src.settings import Settings
from src.tracing import traceable

_SYSTEM = """You are the STRATEGIST in a two-agent script loop for Instagram Reels
(vertical, 30-60 seconds, tone: {tone}).

You do the thinking; a separate Writer executes. You own the angle, the hook, the
structure, and the convergence decision. The Writer owns wording only — never write
script lines yourself, direct them.

Hard rules:
- NEVER override the operator's core topic. You suggest and build; you do not redirect.
- The core question is always "how should this content be SHOWN?" — enumerate the
  plausible presentation paths (myth-buster, countdown, did-you-know, problem→reveal,
  story-driven, comparison, timeline, ...), reason about fit, and commit to the strongest.
- Ground everything in the research facts provided; reference facts by fact_id."""

_BACKBONE_TASK = """Design the reel from the approved research below.

Produce:
- chosen_angle: the presentation path you commit to, stated concretely for THIS topic.
- hook: the spoken opening line (max ~12 words, scroll-stopping, no false claims).
- rationale: why this angle beats the alternatives you considered.
- paths_considered: 3-5 paths you weighed, each with why_not_chosen
  (null for the chosen one).
- content_architecture: the reel's arc as pipe-separated beats, e.g.
  "hook | context | build | reveal | payoff (held off)". Only the order matters.
- target_length_sec: pick within 30-60 (default {default_len}).
- sections: 3-6 backbone sections. For each: purpose (one of hook/context/build/
  reveal/payoff/cta or similar), note (what this section must ACCOMPLISH — intent,
  not wording), facts_to_use (fact_ids from the research that belong there)."""

_ASSESS_TASK = """You are reviewing the Writer's full script for round {iteration}
(minimum rounds before the script may surface: {min_rounds}; hard cap: {max_rounds}).

Judge the WHOLE script against the locked angle and architecture:
- Does the hook land in the first 2 seconds?
- Does the structure build — does every scene earn its place?
- Pacing: total duration within 30-60s and near the target? Any scene dragging?
- Tone: {tone}?
- Are the strongest facts used, woven into the story (not cited on screen)?

Then decide:
- converged=true ONLY if the script is genuinely tight. You may not surface the
  script before round {min_rounds}: if it is round {min_rounds_minus} or earlier,
  treat converged as a signal only and STILL provide the next emphasis.
- If converged=false (or the floor is not yet met): emphasis with exactly ONE
  primary — the single part most in need of a MAJOR change (scene_ref must be an
  existing scene_id; change_type one of: rewrite, sharpen hook, fix pacing,
  punch up, cut, reorder, tighten; note = the specific change and WHY), plus 0-3
  secondary lighter touches. Each revision round is targeted, not a full rewrite.

Prior round notes (avoid repeating resolved issues):
{prior_notes}"""


class _PathLLM(BaseModel):
    path: str
    why_not_chosen: str | None = None


class _SectionLLM(BaseModel):
    purpose: str
    note: str
    facts_to_use: list[str] = Field(default_factory=list)


class _BackboneLLM(BaseModel):
    chosen_angle: str
    hook: str
    rationale: str
    paths_considered: list[_PathLLM] = Field(min_length=2, max_length=6)
    content_architecture: str
    target_length_sec: int = Field(ge=30, le=60)
    sections: list[_SectionLLM] = Field(min_length=3, max_length=6)


class _EmphasisPrimaryLLM(BaseModel):
    scene_ref: int
    change_type: str
    note: str
    facts_to_use: list[str] = Field(default_factory=list)


class _EmphasisLLM(BaseModel):
    primary: _EmphasisPrimaryLLM
    secondary: list[EmphasisSecondary] = Field(default_factory=list, max_length=3)


class _AssessLLM(BaseModel):
    assessment_notes: list[str] = Field(min_length=1)
    converged: bool
    emphasis: _EmphasisLLM | None = None


def brief_block(brief: ResearchBrief) -> str:
    lines = [f"TOPIC: {brief.topic}"]
    if brief.operator_angle_hint:
        lines.append(
            f"OPERATOR ANGLE HINT (strong guidance, topic itself is untouchable): "
            f"{brief.operator_angle_hint}"
        )
    lines.append(f"RESEARCH SUMMARY: {brief.research_summary}")
    if brief.topic_level_notes:
        lines.append(f"TOPIC NOTES: {brief.topic_level_notes}")
    lines.append("FACTS:")
    for f in brief.facts:
        lines.append(
            f"- {f.fact_id} (rank {f.relevance_rank}): {f.claim} "
            f"[source: {f.source_name}; credibility: {f.credibility_note}]"
        )
    return "\n".join(lines)


class Strategist:
    def __init__(self, client: AsyncOpenAI, settings: Settings):
        self.client = client
        self.settings = settings
        self.model = settings.models.strategist
        self.temperature = settings.llm.temperatures.get("strategist", 0.7)
        self.reasoning_effort = settings.llm.reasoning_efforts.get("strategist")

    def _system(self) -> str:
        return _SYSTEM.format(tone=self.settings.pipeline.tone)

    @traceable(name="strategist-backbone", run_type="chain")
    async def open_backbone(
        self,
        brief: ResearchBrief,
        operator_note: str | None = None,
        prior_script: ConvergedScript | None = None,
    ) -> tuple[StrategistCommand, FinalAngle]:
        """Round 1 — lock the angle and hand the Writer the whole-script skeleton."""
        valid_ids = brief.fact_ids()

        def _check(out: _BackboneLLM) -> None:
            unknown = {
                fid for s in out.sections for fid in s.facts_to_use
            } - valid_ids
            if unknown:
                raise ValueError(
                    f"facts_to_use contains unknown fact_ids {sorted(unknown)}; "
                    f"valid ids: {sorted(valid_ids)}"
                )
            if "|" not in out.content_architecture:
                raise ValueError(
                    "content_architecture must be pipe-separated beats, "
                    "e.g. 'hook | build | reveal | payoff'"
                )

        parts = [
            _BACKBONE_TASK.format(
                default_len=self.settings.pipeline.target_length_sec
            ),
            "",
            brief_block(brief),
        ]
        if prior_script is not None:
            parts += [
                "",
                "PREVIOUS (REJECTED) SCRIPT for reference — do not simply repeat it:",
                prior_script.model_dump_json(),
            ]
        if operator_note:
            parts += [
                "",
                f"OPERATOR REGENERATION NOTE — the previous package was rejected; "
                f"you must address this: {operator_note}",
            ]

        out = await complete_json(
            self.client,
            self.settings,
            model=self.model,
            schema=_BackboneLLM,
            system=self._system(),
            user="\n".join(parts),
            temperature=self.temperature,
            reasoning_effort=self.reasoning_effort,
            post_validate=_check,
        )

        sections = [
            BackboneSection(
                section_id=f"s{i}",
                purpose=s.purpose,
                note=s.note,
                facts_to_use=s.facts_to_use,
            )
            for i, s in enumerate(out.sections, start=1)
        ]
        command = StrategistCommand(
            iteration=1,
            phase="backbone",
            content_architecture=out.content_architecture,
            angle=Angle(chosen_angle=out.chosen_angle, hook=out.hook),
            backbone=Backbone(
                target_length_sec=out.target_length_sec, sections=sections
            ),
        )
        final_angle = FinalAngle(
            chosen_angle=out.chosen_angle,
            hook=out.hook,
            rationale=out.rationale,
            paths_considered=[
                PathConsidered(path=p.path, why_not_chosen=p.why_not_chosen)
                for p in out.paths_considered
            ],
        )
        return command, final_angle

    @traceable(name="strategist-assess", run_type="chain")
    async def assess(
        self,
        brief: ResearchBrief,
        angle: Angle,
        content_architecture: str,
        execution: WriterExecution,
        iteration: int,
        min_rounds: int,
        max_rounds: int,
        prior_notes: list[str],
    ) -> tuple[ConvergenceDecision, Emphasis | None]:
        """After each Writer round — assess, then converge or direct the next revision."""
        scene_ids = sorted(execution.script_draft.scene_ids())
        valid_facts = brief.fact_ids()

        def _check(out: _AssessLLM) -> None:
            needs_emphasis = (not out.converged) or (iteration < min_rounds)
            if needs_emphasis and out.emphasis is None:
                raise ValueError(
                    "emphasis (with exactly one primary) is required unless "
                    f"converged on round {min_rounds} or later"
                )
            if out.emphasis is not None:
                refs = [out.emphasis.primary.scene_ref] + [
                    s.scene_ref for s in out.emphasis.secondary
                ]
                bad = [r for r in refs if r not in scene_ids]
                if bad:
                    raise ValueError(
                        f"scene_ref {bad} not in existing scene_ids {scene_ids}"
                    )
                unknown = set(out.emphasis.primary.facts_to_use) - valid_facts
                if unknown:
                    raise ValueError(
                        f"facts_to_use contains unknown fact_ids {sorted(unknown)}"
                    )

        task = _ASSESS_TASK.format(
            iteration=iteration,
            min_rounds=min_rounds,
            min_rounds_minus=max(min_rounds - 1, 1),
            max_rounds=max_rounds,
            tone=self.settings.pipeline.tone,
            prior_notes="\n".join(f"- {n}" for n in prior_notes) or "- (none yet)",
        )
        user = "\n\n".join(
            [
                task,
                f"LOCKED ANGLE: {angle.chosen_angle}\nHOOK: {angle.hook}\n"
                f"CONTENT ARCHITECTURE: {content_architecture}",
                brief_block(brief),
                "CURRENT FULL SCRIPT (round "
                f"{execution.iteration}, total "
                f"{execution.script_draft.total_duration_sec()}s of "
                f"{execution.script_draft.target_length_sec}s target):\n"
                + json.dumps(execution.script_draft.model_dump(), indent=1),
            ]
        )

        out = await complete_json(
            self.client,
            self.settings,
            model=self.model,
            schema=_AssessLLM,
            system=self._system(),
            user=user,
            temperature=self.temperature,
            reasoning_effort=self.reasoning_effort,
            post_validate=_check,
        )

        decision = ConvergenceDecision(
            iteration=iteration,
            assessment_notes=out.assessment_notes,
            converged=out.converged,
        )
        emphasis = None
        if out.emphasis is not None:
            emphasis = Emphasis(
                primary=EmphasisPrimary(
                    scene_ref=out.emphasis.primary.scene_ref,
                    change_type=out.emphasis.primary.change_type,
                    note=out.emphasis.primary.note,
                    facts_to_use=out.emphasis.primary.facts_to_use,
                ),
                secondary=out.emphasis.secondary,
            )
        return decision, emphasis
