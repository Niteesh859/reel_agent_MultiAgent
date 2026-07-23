"""Checks the loop controller honors the iteration floor, hard cap, and
convergence rules (PRD §4 loop mechanics / §8) using fake agents."""

import pytest

from src.loop.controller import ScriptLoopController
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
from src.schemas.outputs import FinalAngle, PathConsidered
from tests.test_schemas import make_brief

SPINE = "hook | build | payoff"


class FakeStrategist:
    """Signals convergence from `converge_at` onward (never, if None).
    Mirrors the real contract: emphasis accompanies every decision that
    cannot end the loop."""

    def __init__(self, converge_at: int | None, drop_emphasis: bool = False):
        self.converge_at = converge_at
        self.drop_emphasis = drop_emphasis

    async def open_backbone(self, brief, operator_note=None, prior_script=None):
        command = StrategistCommand(
            iteration=1,
            phase="backbone",
            content_architecture=SPINE,
            angle=Angle(chosen_angle="countdown", hook="You missed these three"),
            backbone=Backbone(
                target_length_sec=45,
                sections=[
                    BackboneSection(section_id=f"s{i}", purpose=p, note=f"do {p}")
                    for i, p in enumerate(("hook", "build", "payoff"), start=1)
                ],
            ),
        )
        final_angle = FinalAngle(
            chosen_angle="countdown",
            hook="You missed these three",
            rationale="material stacks well",
            paths_considered=[PathConsidered(path="countdown")],
        )
        return command, final_angle

    async def assess(
        self, brief, angle, content_architecture, execution,
        iteration, min_rounds, max_rounds, prior_notes,
    ):
        converged = self.converge_at is not None and iteration >= self.converge_at
        decision = ConvergenceDecision(
            iteration=iteration,
            assessment_notes=[f"note r{iteration}"],
            converged=converged,
        )
        if self.drop_emphasis or (converged and iteration >= min_rounds):
            return decision, None
        emphasis = Emphasis(
            primary=EmphasisPrimary(
                scene_ref=2, change_type="punch up", note="more energy"
            )
        )
        return decision, emphasis


class FakeWriter:
    async def execute(self, brief, command, current_script=None):
        if command.phase == "backbone":
            scenes = [
                SceneDraft(
                    scene_id=i,
                    section_id=f"s{i}",
                    on_screen_text=f"t{i}",
                    narration=f"line {i}",
                    visual_keyword="kw",
                    audio_emphasis="none",
                    approx_duration_sec=15.0,
                )
                for i in (1, 2, 3)
            ]
            return WriterExecution(
                iteration=command.iteration,
                phase="backbone",
                script_draft=ScriptDraft(title="T", target_length_sec=45, scenes=scenes),
            )
        assert current_script is not None
        ref = command.emphasis.primary.scene_ref
        scenes = [s.model_copy(deep=True) for s in current_script.scenes]
        for scene in scenes:
            if scene.scene_id == ref:
                scene.narration = f"line {ref} v{command.iteration}"
        return WriterExecution(
            iteration=command.iteration,
            phase="revision",
            revised_scene_ids=[ref],
            script_draft=ScriptDraft(title="T", target_length_sec=45, scenes=scenes),
        )


def make_controller(converge_at, min_rounds=3, max_rounds=5, **kwargs):
    return ScriptLoopController(
        FakeStrategist(converge_at, **kwargs), FakeWriter(), min_rounds, max_rounds
    )


async def test_early_convergence_is_held_to_the_floor():
    # Strategist says "converged" from round 1 — loop must still run min_rounds.
    result = await make_controller(converge_at=1).run(make_brief())
    assert result.script.iterations_used == 3
    assert result.script.convergence == "agreed"
    assert len(result.rounds) == 3


async def test_cap_reached_when_never_converging():
    result = await make_controller(converge_at=None).run(make_brief())
    assert result.script.iterations_used == 5
    assert result.script.convergence == "cap_reached"
    assert len(result.rounds) == 5


async def test_agreed_exactly_at_floor():
    result = await make_controller(converge_at=3).run(make_brief())
    assert result.script.iterations_used == 3
    assert result.script.convergence == "agreed"


async def test_convergence_on_final_capped_round_counts_as_agreed():
    result = await make_controller(converge_at=5).run(make_brief())
    assert result.script.iterations_used == 5
    assert result.script.convergence == "agreed"


async def test_round_phases_and_static_spine():
    result = await make_controller(converge_at=None).run(make_brief())
    phases = [r["command"]["phase"] for r in result.rounds]
    assert phases == ["backbone", "revision", "revision", "revision", "revision"]
    angles = {r["command"]["angle"]["chosen_angle"] for r in result.rounds}
    spines = {r["command"]["content_architecture"] for r in result.rounds}
    assert angles == {"countdown"} and spines == {SPINE}
    assert result.content_architecture == SPINE


async def test_revisions_are_targeted_and_tracked():
    result = await make_controller(converge_at=None).run(make_brief())
    assert result.rounds[0]["execution"]["revised_scene_ids"] == []
    for later in result.rounds[1:]:
        assert later["execution"]["revised_scene_ids"] == [2]
    # untouched scenes survive verbatim; the emphasized one changed
    final = result.script
    assert final.scenes[0].narration == "line 1"
    assert final.scenes[1].narration.startswith("line 2 v")


async def test_missing_emphasis_mid_loop_is_an_error():
    controller = make_controller(converge_at=None, drop_emphasis=True)
    with pytest.raises(RuntimeError, match="no emphasis"):
        await controller.run(make_brief())


def test_bounds_validated():
    with pytest.raises(ValueError):
        ScriptLoopController(FakeStrategist(None), FakeWriter(), 1, 5)
    with pytest.raises(ValueError):
        ScriptLoopController(FakeStrategist(None), FakeWriter(), 4, 3)
