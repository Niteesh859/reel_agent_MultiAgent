"""Entrypoint — wires the Phase 1 pipeline and the checkpoint loop together.

    uv run python -m src.main "your topic" [--angle "optional hint"]

Flow (PRD §3/§8): Researcher → ◇ research checkpoint → Strategist⇄Writer script
loop → ◇ angle+script checkpoint → export approved script (Phase 1 deliverable).
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import datetime
from pathlib import Path

from rich.console import Console

from src.agents.researcher import run_research
from src.agents.strategist import Strategist
from src.agents.writer import Writer
from src.guardrails.failures import SkipReel, StageFailure, with_retry
from src.io.checkpoint import (
    CheckpointView,
    run_checkpoint,
)
from src.io.edits import (
    RESEARCH_HELP,
    SCRIPT_HELP,
    apply_research_edit,
    apply_script_edit,
)
from src.io.export import export_script, save_json
from src.io.tui import TuiChannel
from src.loop.controller import LoopResult, ScriptLoopController
from src.schemas.outputs import ConvergedScript
from src.schemas.research import ResearchBrief
from src.settings import (
    OUTPUT_DIR,
    Settings,
    env,
    langsmith_enabled,
    load_settings,
    missing_required_keys,
    telegram_configured,
)
from src.llm import make_client
from src.tracing import traceable

console = Console()


# ── checkpoint views (header / summary / full — PRD §6 progressive disclosure) ──


def research_view(brief: ResearchBrief, reel_id: str) -> CheckpointView:
    header = [f"reel {reel_id} · stage 1/2", f"topic: {brief.topic}"]
    if brief.operator_angle_hint:
        header.append(f"angle hint: {brief.operator_angle_hint}")
    facts = brief.facts  # researcher order = most relevant first
    summary = [brief.research_summary, ""]
    for f in facts[:5]:
        summary.append(f"{f.fact_id} {f.claim}")
    if len(facts) > 5:
        summary.append(f"… {len(facts) - 5} more facts — type 'full'")
    full = [f"RESEARCH SUMMARY: {brief.research_summary}", ""]
    for f in facts:
        full += [
            f"{f.fact_id} {f.claim}",
            f"    source: {f.source_url}"
            + (f" · {f.source_date}" if f.source_date else ""),
            f"    follow-up: {f.followup_question or '(none)'}",
            "",
        ]
    if brief.topic_level_notes:
        full.append(f"TOPIC NOTES: {brief.topic_level_notes}")
    return CheckpointView(reel_id, "RESEARCH", header, summary, full)


def script_view(script: ConvergedScript, spine: str, reel_id: str) -> CheckpointView:
    angle = script.final_angle
    header = [
        f"reel {reel_id} · stage 2/2",
        f"spine: {spine}",
        f"angle: {angle.chosen_angle}",
        f"convergence: {script.convergence} after {script.iterations_used} rounds",
    ]
    summary = [
        f"“{script.title}”",
        f"hook: {angle.hook}",
        f"{len(script.scenes)} scenes · ~{script.total_duration_sec()}s "
        f"(target {script.target_length_sec}s)",
        "",
    ]
    for s in script.scenes:
        summary.append(f"{s.scene_id}. (~{s.approx_duration_sec:g}s) {s.narration}")
    full = [
        f"TITLE: {script.title}",
        f"ANGLE: {angle.chosen_angle}",
        f"HOOK: {angle.hook}",
        f"RATIONALE: {angle.rationale}",
        "PATHS CONSIDERED:",
        *[
            f"  - {p.path} — {p.why_not_chosen or '✔ chosen'}"
            for p in angle.paths_considered
        ],
        "",
    ]
    scene_lines: dict[int, str] = {}
    for s in script.scenes:
        block = (
            f"scene {s.scene_id} (~{s.approx_duration_sec:g}s)\n"
            f"  ON-SCREEN: {s.on_screen_text}\n"
            f"  NARRATION: {s.narration}\n"
            f"  VISUAL: {s.visual_keyword}\n"
            f"  TTS EMPHASIS: {s.audio_emphasis}"
        )
        full.append(block)
        full.append("")
        scene_lines[s.scene_id] = block
    return CheckpointView(reel_id, "ANGLE + SCRIPT", header, summary, full, scene_lines)


# ── stages ──────────────────────────────────────────────────────────────────


async def _notify(channels, text: str) -> None:
    """Best-effort fan-out: a channel hiccup must never take the pipeline down."""
    for ch in channels:
        try:
            await ch.notify(text)
        except Exception as err:  # noqa: BLE001
            console.print(
                f"[dim yellow]({ch.name} notify failed: {type(err).__name__} — continuing)[/dim yellow]"
            )


async def _research_stage(
    client, settings: Settings, channels, topic: str, angle_hint: str | None,
    reel_id: str, run_dir: Path,
) -> ResearchBrief:
    note: str | None = None
    attempt = 1
    while True:
        await _notify(
            channels,
            f"🔎 researching “{topic}” — two-tier pass "
            f"(attempt {attempt}, ~2-5 min)…",
        )
        brief: ResearchBrief = await with_retry(
            run_research, client, settings, topic, angle_hint, note, stage="research"
        )
        save_json(run_dir / f"research_attempt{attempt}.json", brief.model_dump())
        result = await run_checkpoint(
            lambda: research_view(brief, reel_id),
            channels,
            lambda cmd: apply_research_edit(brief, cmd),
            RESEARCH_HELP,
        )
        if result.kind == "approve":
            save_json(run_dir / "research_approved.json", brief.model_dump())
            return brief
        note = result.note or "operator requested regeneration"
        attempt += 1


async def _script_stage(
    client, settings: Settings, channels, brief: ResearchBrief,
    reel_id: str, run_dir: Path,
) -> LoopResult:
    strategist = Strategist(client, settings)
    writer = Writer(client, settings)
    note: str | None = None
    prior: ConvergedScript | None = None
    attempt = 1
    while True:
        await _notify(
            channels,
            f"✍️ script loop running (attempt {attempt}, "
            f"{settings.loop.min_rounds}-{settings.loop.max_rounds} rounds)…",
        )

        def on_event(kind: str, payload: dict, _attempt: int = attempt) -> None:
            iteration = payload.get("iteration", 0)
            if kind == "backbone_start":
                console.log(
                    "[dim][loop] strategist designing angle + backbone… (~30-120s)[/dim]"
                )
                return
            if kind == "writer_start":
                console.log(
                    f"[dim][loop] round {iteration}: writer drafting… "
                    f"(20-90s, be patient)[/dim]"
                )
                return
            if kind == "assess_start":
                console.log(
                    f"[dim][loop] round {iteration}: strategist reviewing… (~15-60s)[/dim]"
                )
                return
            save_json(
                run_dir / f"loop_attempt{_attempt}_round{iteration}_{kind}.json",
                payload,
            )
            if kind == "command":
                phase = payload["command"]["phase"]
                console.log(f"[loop] round {iteration}: strategist → {phase} command")
            elif kind == "execution":
                scenes = len(payload["execution"]["script_draft"]["scenes"])
                console.log(f"[loop] round {iteration}: writer → {scenes} scenes")
            elif kind == "decision":
                converged = payload["decision"]["converged"]
                console.log(f"[loop] round {iteration}: converged={converged}")

        controller = ScriptLoopController(
            strategist,
            writer,
            settings.loop.min_rounds,
            settings.loop.max_rounds,
            on_event,
        )
        loop_result = await controller.run(brief, note, prior)
        script = loop_result.script
        save_json(run_dir / f"converged_attempt{attempt}.json", script.model_dump())
        result = await run_checkpoint(
            lambda: script_view(script, loop_result.content_architecture, reel_id),
            channels,
            lambda cmd: apply_script_edit(script, cmd),
            SCRIPT_HELP,
        )
        if result.kind == "approve":
            save_json(run_dir / "converged_approved.json", script.model_dump())
            return loop_result
        note = result.note or "operator requested regeneration"
        prior = script
        attempt += 1


@traceable(name="reel-pipeline", run_type="chain")
async def run_pipeline(
    client, settings: Settings, channels, topic: str, angle_hint: str | None,
    reel_id: str,
) -> tuple[Path, Path]:
    run_dir = OUTPUT_DIR / "runs" / reel_id
    run_dir.mkdir(parents=True, exist_ok=True)
    brief = await _research_stage(
        client, settings, channels, topic, angle_hint, reel_id, run_dir
    )
    loop_result = await _script_stage(
        client, settings, channels, brief, reel_id, run_dir
    )
    return export_script(
        loop_result.script, brief, loop_result.content_architecture, reel_id
    )


# ── CLI ─────────────────────────────────────────────────────────────────────


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="reels-agent",
        description="Phase 1: topic → research → strategist⇄writer loop → approved script.",
    )
    parser.add_argument("topic", nargs="?", help="reel topic (prompted if omitted)")
    parser.add_argument("--angle", default=None, help="optional operator angle hint")
    parser.add_argument(
        "--tui-only", action="store_true", help="ignore Telegram even if configured"
    )
    parser.add_argument(
        "--settings", default=None, help="path to an alternate settings.yaml"
    )
    return parser.parse_args(argv)


async def main_async(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    settings = load_settings(Path(args.settings) if args.settings else None)

    missing = missing_required_keys()
    if missing:
        console.print(
            f"[red]Missing required keys in .env: {', '.join(missing)}[/red]\n"
            "Copy .env.example → .env and fill them in (see README)."
        )
        return 2

    topic = (args.topic or "").strip() or input("Topic: ").strip()
    if not topic:
        console.print("[red]A topic is required.[/red]")
        return 2

    reel_id = f"r-{datetime.now():%Y%m%d-%H%M%S}"
    channels = [TuiChannel(console)]
    if not args.tui_only and telegram_configured():
        from src.io.telegram import TelegramChannel

        telegram = TelegramChannel(
            env("TELEGRAM_BOT_TOKEN"),  # type: ignore[arg-type]
            env("TELEGRAM_CHAT_ID"),  # type: ignore[arg-type]
            settings.io_cfg.telegram_poll_timeout_sec,
        )
        try:
            bot_name = await telegram.verify()
            channels.append(telegram)
            console.print(f"[green]Telegram channel connected (@{bot_name}).[/green]")
        except Exception as err:  # noqa: BLE001 — degrade to TUI-only, never crash
            await telegram.aclose()
            console.print(
                f"[yellow]Telegram configured but not working — continuing TUI-only.\n"
                f"  reason: {err}\n"
                f"  hint: open your bot's chat in Telegram, press Start, then re-run.[/yellow]"
            )

    console.print(
        f"[bold]reels-agent · Phase 1[/bold] — reel [cyan]{reel_id}[/cyan]\n"
        f"model: {settings.models.writer} · channels: "
        f"{' + '.join(ch.name for ch in channels)} · "
        f"tracing: {'LangSmith ON (project ' + (env('LANGSMITH_PROJECT') or 'default') + ')' if langsmith_enabled() else 'off'}"
    )

    client = make_client(settings)
    try:
        json_path, md_path = await run_pipeline(
            client, settings, channels, topic, args.angle, reel_id
        )
        done_msg = (
            f"🎬 script approved & exported\n  JSON: {json_path}\n  MD:   {md_path}\n"
            "Phase 2 (visuals/TTS/render) is not built yet — use the export manually."
        )
        await _notify(channels, done_msg)
        return 0
    except SkipReel:
        console.print("[yellow]Reel skipped by operator.[/yellow]")
        return 0
    except StageFailure as failure:
        pause_msg = f"⏸ pipeline paused — {failure}"
        for ch in channels:
            try:
                await ch.notify(pause_msg)
            except Exception:  # noqa: BLE001
                pass
        console.print(
            f"[red]{pause_msg}[/red]\n"
            f"Artifacts so far: {OUTPUT_DIR / 'runs' / reel_id}"
        )
        return 1
    finally:
        for ch in channels:
            try:
                await ch.aclose()
            except Exception:  # noqa: BLE001
                pass
        await client.close()


def main() -> int:
    try:
        return asyncio.run(main_async())
    except KeyboardInterrupt:
        console.print("\n[yellow]interrupted[/yellow]")
        return 130


if __name__ == "__main__":
    sys.exit(main())
