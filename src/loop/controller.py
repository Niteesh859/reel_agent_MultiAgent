"""Script-loop controller — PRD §9/§10.

Drives Strategist ⇄ Writer: tracks the round counter, enforces the iteration
floor (converged=true before min_rounds is treated as a signal only) and the
hard cap (best draft surfaces flagged "cap_reached"), and assembles the single
converged §5.3 package the operator reviews.

The strategist/writer are duck-typed so tests can drive the loop with fakes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Protocol

from src.guardrails.failures import with_retry
from src.loop.phase import make_revision_command
from src.schemas.loop_messages import (
    Angle,
    ConvergenceDecision,
    Emphasis,
    ScriptDraft,
    StrategistCommand,
    WriterExecution,
)
from src.schemas.outputs import ConvergedScript, FinalAngle, FinalScene
from src.schemas.research import ResearchBrief
from src.tracing import traceable


class StrategistLike(Protocol):
    async def open_backbone(
        self,
        brief: ResearchBrief,
        operator_note: str | None = None,
        prior_script: ConvergedScript | None = None,
    ) -> tuple[StrategistCommand, FinalAngle]: ...

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
    ) -> tuple[ConvergenceDecision, Emphasis | None]: ...


class WriterLike(Protocol):
    async def execute(
        self,
        brief: ResearchBrief,
        command: StrategistCommand,
        current_script: ScriptDraft | None = None,
    ) -> WriterExecution: ...


@dataclass
class LoopResult:
    script: ConvergedScript
    content_architecture: str
    rounds: list[dict] = field(default_factory=list)


OnEvent = Callable[[str, dict[str, Any]], None]


class ScriptLoopController:
    def __init__(
        self,
        strategist: StrategistLike,
        writer: WriterLike,
        min_rounds: int,
        max_rounds: int,
        on_event: OnEvent | None = None,
    ):
        if not (2 <= min_rounds <= max_rounds):
            raise ValueError(
                f"need 2 <= min_rounds <= max_rounds, got {min_rounds}/{max_rounds}"
            )
        self.strategist = strategist
        self.writer = writer
        self.min_rounds = min_rounds
        self.max_rounds = max_rounds
        self._emit = on_event or (lambda kind, payload: None)

    @traceable(name="script-loop", run_type="chain")
    async def run(
        self,
        brief: ResearchBrief,
        operator_note: str | None = None,
        prior_script: ConvergedScript | None = None,
    ) -> LoopResult:
        rounds: list[dict] = []
        prior_notes: list[str] = []

        command, final_angle = await with_retry(
            self.strategist.open_backbone,
            brief,
            operator_note,
            prior_script,
            stage="script loop (strategist backbone)",
        )
        angle = command.angle
        content_architecture = command.content_architecture
        self._emit("command", {"iteration": 1, "command": command.model_dump()})

        current: ScriptDraft | None = None
        iteration = 1
        convergence: str = "cap_reached"

        while True:
            execution: WriterExecution = await with_retry(
                self.writer.execute,
                brief,
                command,
                current,
                stage="script loop (writer)",
            )
            current = execution.script_draft
            self._emit(
                "execution", {"iteration": iteration, "execution": execution.model_dump()}
            )

            decision, emphasis = await with_retry(
                self.strategist.assess,
                brief,
                angle,
                content_architecture,
                execution,
                iteration,
                self.min_rounds,
                self.max_rounds,
                prior_notes,
                stage="script loop (strategist assess)",
            )
            prior_notes.extend(decision.assessment_notes)
            self._emit(
                "decision", {"iteration": iteration, "decision": decision.model_dump()}
            )
            rounds.append(
                {
                    "iteration": iteration,
                    "command": command.model_dump(),
                    "execution": execution.model_dump(),
                    "decision": decision.model_dump(),
                }
            )

            floor_met = iteration >= self.min_rounds
            if decision.converged and floor_met:
                convergence = "agreed"
                break
            if iteration >= self.max_rounds:
                convergence = "cap_reached"
                break
            if emphasis is None:
                # Strategist contract guarantees emphasis whenever the loop must
                # continue; treat absence as an infra fault, not a silent stop.
                raise RuntimeError(
                    "strategist provided no emphasis but the loop must continue "
                    f"(round {iteration}, converged={decision.converged})"
                )

            iteration += 1
            command = make_revision_command(
                iteration, angle, content_architecture, emphasis
            )
            self._emit(
                "command", {"iteration": iteration, "command": command.model_dump()}
            )

        assert current is not None
        script = ConvergedScript(
            title=current.title,
            target_length_sec=current.target_length_sec,
            final_angle=final_angle,
            iterations_used=iteration,
            convergence=convergence,  # type: ignore[arg-type]
            scenes=[
                FinalScene(
                    scene_id=s.scene_id,
                    on_screen_text=s.on_screen_text,
                    narration=s.narration,
                    visual_keyword=s.visual_keyword,
                    audio_emphasis=s.audio_emphasis,
                    approx_duration_sec=s.approx_duration_sec,
                )
                for s in current.scenes
            ],
        )
        script.renumber()
        return LoopResult(
            script=script, content_architecture=content_architecture, rounds=rounds
        )
