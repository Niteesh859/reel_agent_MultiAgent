"""Validates each PRD §5 handoff shape, including the phase-XOR rules of §5.2a."""

import pytest
from pydantic import ValidationError

from src.schemas.loop_messages import (
    Angle,
    Backbone,
    BackboneSection,
    ConvergenceDecision,
    Emphasis,
    EmphasisPrimary,
    ScriptDraft,
    SceneDraft,
    StrategistCommand,
    WriterExecution,
)
from src.schemas.outputs import ConvergedScript, FinalAngle, FinalScene, PathConsidered
from src.schemas.research import Fact, ResearchBrief


def make_brief() -> ResearchBrief:
    return ResearchBrief(
        topic="octopus intelligence",
        operator_angle_hint=None,
        research_summary="Octopuses show surprising cognition.",
        facts=[
            Fact(
                fact_id=f"f{i}",
                claim=f"claim {i}",
                source_name="Nature",
                source_url=f"https://example.com/{i}",
                source_date=None,
                credibility_note="peer-reviewed",
                relevance_rank=i,
            )
            for i in (1, 2, 3)
        ],
        topic_level_notes=None,
    )


def make_scenes(n: int = 3) -> list[SceneDraft]:
    return [
        SceneDraft(
            scene_id=i,
            section_id=f"s{i}",
            on_screen_text=f"overlay {i}",
            narration=f"spoken line {i}",
            visual_keyword="octopus close up",
            audio_emphasis="stress the number",
            approx_duration_sec=15.0,
        )
        for i in range(1, n + 1)
    ]


ANGLE = Angle(chosen_angle="did-you-know countdown", hook="Your brain is lying to you")
BACKBONE = Backbone(
    target_length_sec=45,
    sections=[
        BackboneSection(section_id="s1", purpose="hook", note="stop the scroll", facts_to_use=["f1"]),
        BackboneSection(section_id="s2", purpose="build", note="stack surprises", facts_to_use=["f2", "f3"]),
        BackboneSection(section_id="s3", purpose="payoff", note="land the twist"),
    ],
)
EMPHASIS = Emphasis(
    primary=EmphasisPrimary(scene_ref=2, change_type="punch up", note="more energy", facts_to_use=["f2"])
)


def test_research_brief_round_trip():
    brief = make_brief()
    again = ResearchBrief.model_validate_json(brief.model_dump_json())
    assert again == brief
    assert again.fact_ids() == {"f1", "f2", "f3"}
    assert again.get_fact("f2").claim == "claim 2"


def test_backbone_command_valid():
    cmd = StrategistCommand(
        iteration=1,
        phase="backbone",
        content_architecture="hook | build | payoff",
        angle=ANGLE,
        backbone=BACKBONE,
    )
    assert StrategistCommand.model_validate_json(cmd.model_dump_json()) == cmd


def test_revision_command_valid():
    cmd = StrategistCommand(
        iteration=2,
        phase="revision",
        content_architecture="hook | build | payoff",
        angle=ANGLE,
        emphasis=EMPHASIS,
    )
    assert cmd.emphasis.primary.scene_ref == 2


@pytest.mark.parametrize(
    "kwargs",
    [
        # backbone phase missing its block
        dict(iteration=1, phase="backbone"),
        # backbone phase carrying an emphasis
        dict(iteration=1, phase="backbone", backbone=BACKBONE, emphasis=EMPHASIS),
        # backbone on a later round
        dict(iteration=2, phase="backbone", backbone=BACKBONE),
        # revision missing emphasis
        dict(iteration=2, phase="revision"),
        # revision carrying a backbone
        dict(iteration=2, phase="revision", emphasis=EMPHASIS, backbone=BACKBONE),
        # revision on round 1
        dict(iteration=1, phase="revision", emphasis=EMPHASIS),
    ],
)
def test_command_phase_shape_enforced(kwargs):
    with pytest.raises(ValidationError):
        StrategistCommand(
            content_architecture="hook | build | payoff", angle=ANGLE, **kwargs
        )


def test_writer_execution_round_trip():
    execution = WriterExecution(
        iteration=2,
        phase="revision",
        revised_scene_ids=[2],
        script_draft=ScriptDraft(title="T", target_length_sec=45, scenes=make_scenes()),
    )
    again = WriterExecution.model_validate_json(execution.model_dump_json())
    assert again.script_draft.total_duration_sec() == 45.0
    assert again.script_draft.scene_ids() == {1, 2, 3}


def test_backbone_execution_rejects_revised_ids():
    with pytest.raises(ValidationError):
        WriterExecution(
            iteration=1,
            phase="backbone",
            revised_scene_ids=[1],
            script_draft=ScriptDraft(title="T", target_length_sec=45, scenes=make_scenes()),
        )


def test_convergence_decision():
    decision = ConvergenceDecision(iteration=3, assessment_notes=["tight"], converged=True)
    assert ConvergenceDecision.model_validate(decision.model_dump()).converged


def test_converged_script_round_trip_and_renumber():
    script = ConvergedScript(
        title="T",
        target_length_sec=45,
        final_angle=FinalAngle(
            chosen_angle="countdown",
            hook="hook line",
            rationale="fits the material",
            paths_considered=[PathConsidered(path="myth-buster", why_not_chosen="weaker")],
        ),
        iterations_used=3,
        convergence="agreed",
        scenes=[
            FinalScene(
                scene_id=7,
                on_screen_text="x",
                narration="y",
                visual_keyword="z",
                audio_emphasis="e",
                approx_duration_sec=10.0,
            ),
            FinalScene(
                scene_id=9,
                on_screen_text="x",
                narration="y",
                visual_keyword="z",
                audio_emphasis="e",
                approx_duration_sec=10.0,
            ),
        ],
    )
    script.renumber()
    assert [s.scene_id for s in script.scenes] == [1, 2]
    again = ConvergedScript.model_validate_json(script.model_dump_json())
    assert again.convergence == "agreed"
    assert again.total_duration_sec() == 20.0
