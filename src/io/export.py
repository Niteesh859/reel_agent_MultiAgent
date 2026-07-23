"""Phase 1 deliverable (PRD §12): the approved script exported as JSON + Markdown."""

from __future__ import annotations

import json
import re
import time
from pathlib import Path

from src.schemas.outputs import ConvergedScript
from src.schemas.research import ResearchBrief
from src.settings import OUTPUT_DIR

SCRIPTS_DIR = OUTPUT_DIR / "scripts"


def save_json(path: Path, data: dict | list) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False))


def _slug(text: str, max_len: int = 40) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug[:max_len].rstrip("-") or "reel"


def _markdown(script: ConvergedScript, brief: ResearchBrief, spine: str, reel_id: str) -> str:
    angle = script.final_angle
    lines = [
        f"# {script.title}",
        "",
        f"- **Reel:** `{reel_id}` · exported {time.strftime('%Y-%m-%d %H:%M')}",
        f"- **Convergence:** {script.convergence} after {script.iterations_used} rounds",
        f"- **Length:** ~{script.total_duration_sec()}s "
        f"(target {script.target_length_sec}s, spec 30-60s vertical)",
        "",
        "## Angle",
        "",
        f"- **Chosen angle:** {angle.chosen_angle}",
        f"- **Hook:** {angle.hook}",
        f"- **Spine:** `{spine}`",
        f"- **Rationale:** {angle.rationale}",
        "",
        "**Paths considered:**",
        "",
    ]
    for path in angle.paths_considered:
        why = path.why_not_chosen or "✔ chosen"
        lines.append(f"- {path.path} — {why}")
    lines += [
        "",
        "## Script",
        "",
        "| # | ~sec | On-screen text | Narration | Visual keyword | TTS emphasis |",
        "|---|------|----------------|-----------|----------------|--------------|",
    ]
    for s in script.scenes:
        row = [
            str(s.scene_id),
            f"{s.approx_duration_sec:g}",
            s.on_screen_text.replace("|", "\\|"),
            s.narration.replace("|", "\\|"),
            s.visual_keyword.replace("|", "\\|"),
            s.audio_emphasis.replace("|", "\\|"),
        ]
        lines.append("| " + " | ".join(row) + " |")
    lines += ["", "## Sources (from the approved research)", ""]
    for f in brief.facts:
        date = f" ({f.source_date})" if f.source_date else ""
        lines.append(
            f"- **{f.fact_id}** {f.claim}\n"
            f"  — {f.source_name}{date} · {f.source_url} · _{f.credibility_note}_"
        )
    lines += [
        "",
        "---",
        "_Phase 1 deliverable: script only. Producer/Assembler (visuals, TTS, render) arrive in Phase 2._",
        "",
    ]
    return "\n".join(lines)


def export_script(
    script: ConvergedScript,
    brief: ResearchBrief,
    content_architecture: str,
    reel_id: str,
) -> tuple[Path, Path]:
    SCRIPTS_DIR.mkdir(parents=True, exist_ok=True)
    base = f"{reel_id}_{_slug(script.title)}"
    json_path = SCRIPTS_DIR / f"{base}.json"
    md_path = SCRIPTS_DIR / f"{base}.md"
    json_path.write_text(script.model_dump_json(indent=2))
    md_path.write_text(_markdown(script, brief, content_architecture, reel_id))
    return json_path, md_path
