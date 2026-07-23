"""Field-addressed edit commands (PRD §6), shared verbatim by TUI and Telegram.

Strict commands are the backbone; looser phrasings ("change scene 3 narration to
...", "remove scene 2", "3: new line") normalize to the same operations.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from src.schemas.outputs import ConvergedScript, FinalScene
from src.schemas.research import ResearchBrief

_VERB = r"(?:edit|change|update|set)"
_SEP = r"(?:\s*[:=]\s*|\s+to\s+)"

_FACT_EDIT = re.compile(
    rf"^{_VERB}\s+fact\s+(f?\d+)\s+"
    r"(claim|source_name|source_url|source|url|source_date|date|"
    r"credibility(?:_note)?|relevance(?:_rank)?|rank)"
    rf"{_SEP}(.+)$",
    re.IGNORECASE | re.DOTALL,
)
_FACT_DELETE = re.compile(
    r"^(?:delete|remove|drop)\s+fact\s+(f?\d+)\s*$", re.IGNORECASE
)
_SUMMARY_EDIT = re.compile(rf"^{_VERB}\s+summary{_SEP}(.+)$", re.IGNORECASE | re.DOTALL)
_NOTES_EDIT = re.compile(rf"^{_VERB}\s+notes{_SEP}(.+)$", re.IGNORECASE | re.DOTALL)

_SCENE_EDIT = re.compile(
    rf"^{_VERB}\s+scene\s+(\d+)\s+"
    r"(narration|text|on[_\- ]?screen(?:[_\- ]?text)?|visual(?:[_\- ]?keyword)?|"
    r"emphasis|audio[_\- ]?emphasis|duration)"
    rf"{_SEP}(.+)$",
    re.IGNORECASE | re.DOTALL,
)
_SCENE_DELETE = re.compile(
    r"^(?:delete|remove|drop|cut)\s+scene\s+(\d+)\s*$", re.IGNORECASE
)
_SCENE_INSERT = re.compile(
    r"^insert\s+(?:scene\s+)?after\s+(\d+)(?:\s*[:=]\s*|\s+)(.+)$",
    re.IGNORECASE | re.DOTALL,
)
_REORDER = re.compile(r"^reorder\s+([\d\s,]+)$", re.IGNORECASE)
_TITLE_EDIT = re.compile(rf"^{_VERB}\s+title{_SEP}(.+)$", re.IGNORECASE | re.DOTALL)
_HOOK_EDIT = re.compile(rf"^{_VERB}\s+hook{_SEP}(.+)$", re.IGNORECASE | re.DOTALL)
_ANGLE_EDIT = re.compile(rf"^{_VERB}\s+angle{_SEP}(.+)$", re.IGNORECASE | re.DOTALL)
_SHORTHAND = re.compile(r"^(\d+)\s*[:\-]\s*(.+)$", re.DOTALL)

RESEARCH_HELP = """Edit commands (research):
  edit fact f2 claim: <text>          also: source, url, date, credibility, rank
  delete fact f3
  edit summary: <text>
  edit notes: <text>"""

SCRIPT_HELP = """Edit commands (script):
  edit scene 3 narration: <text>      also: text (on-screen), visual, emphasis, duration
  3: <text>                           shorthand — replace scene 3's narration
  delete scene 2
  insert after 2: <narration> | <on-screen text>
  reorder 3,1,2,4
  edit title: <text> · edit hook: <text> · edit angle: <text>"""


@dataclass
class EditOutcome:
    ok: bool
    message: str


def _norm_fact_id(raw: str) -> str:
    raw = raw.lower().strip()
    return raw if raw.startswith("f") else f"f{raw}"


def apply_research_edit(brief: ResearchBrief, command: str) -> EditOutcome:
    command = command.strip()

    if m := _FACT_DELETE.match(command):
        fid = _norm_fact_id(m.group(1))
        fact = brief.get_fact(fid)
        if fact is None:
            return EditOutcome(False, f"no fact '{fid}' — ids: {sorted(brief.fact_ids())}")
        brief.facts.remove(fact)
        return EditOutcome(True, f"deleted fact {fid} ({len(brief.facts)} remain)")

    if m := _FACT_EDIT.match(command):
        fid, field_raw, value = _norm_fact_id(m.group(1)), m.group(2).lower(), m.group(3).strip()
        fact = brief.get_fact(fid)
        if fact is None:
            return EditOutcome(False, f"no fact '{fid}' — ids: {sorted(brief.fact_ids())}")
        if field_raw == "claim":
            fact.claim = value
        elif field_raw in {"source_name", "source"}:
            fact.source_name = value
        elif field_raw in {"source_url", "url"}:
            fact.source_url = value
        elif field_raw in {"source_date", "date"}:
            fact.source_date = value
        elif field_raw.startswith("credibility"):
            fact.credibility_note = value
        else:  # relevance / rank
            try:
                fact.relevance_rank = int(value)
            except ValueError:
                return EditOutcome(False, f"rank must be an integer, got '{value}'")
        return EditOutcome(True, f"updated fact {fid} {field_raw}")

    if m := _SUMMARY_EDIT.match(command):
        brief.research_summary = m.group(1).strip()
        return EditOutcome(True, "updated research summary")

    if m := _NOTES_EDIT.match(command):
        brief.topic_level_notes = m.group(1).strip()
        return EditOutcome(True, "updated topic notes")

    return EditOutcome(False, "unrecognized edit — type 'help' for commands")


def _find_scene(script: ConvergedScript, scene_id: int) -> FinalScene | None:
    return next((s for s in script.scenes if s.scene_id == scene_id), None)


def apply_script_edit(script: ConvergedScript, command: str) -> EditOutcome:
    command = command.strip()

    if m := _SCENE_DELETE.match(command):
        sid = int(m.group(1))
        scene = _find_scene(script, sid)
        if scene is None:
            return EditOutcome(False, f"no scene {sid} — ids: {sorted(script.scene_ids())}")
        if len(script.scenes) == 1:
            return EditOutcome(False, "cannot delete the only scene")
        script.scenes.remove(scene)
        script.renumber()
        return EditOutcome(True, f"deleted scene {sid}; scenes renumbered 1-{len(script.scenes)}")

    if m := _SCENE_INSERT.match(command):
        after, payload = int(m.group(1)), m.group(2).strip()
        if after != 0 and _find_scene(script, after) is None:
            return EditOutcome(False, f"no scene {after} to insert after (use 0 for start)")
        narration, _, on_screen = (p.strip() for p in payload.partition("|"))
        if not narration:
            return EditOutcome(False, "insert needs narration text")
        words = narration.split()
        new_scene = FinalScene(
            scene_id=0,
            on_screen_text=on_screen or " ".join(words[:6]),
            narration=narration,
            visual_keyword=" ".join(words[:3]),
            audio_emphasis="natural delivery",
            approx_duration_sec=max(2.0, round(len(words) / 2.5, 1)),
        )
        index = next(
            (i + 1 for i, s in enumerate(script.scenes) if s.scene_id == after), 0
        )
        script.scenes.insert(index, new_scene)
        script.renumber()
        return EditOutcome(
            True, f"inserted new scene {new_scene.scene_id}; scenes renumbered"
        )

    if m := _REORDER.match(command):
        try:
            order = [int(x) for x in re.split(r"[\s,]+", m.group(1).strip()) if x]
        except ValueError:
            return EditOutcome(False, "reorder takes scene numbers, e.g. reorder 3,1,2")
        if sorted(order) != sorted(script.scene_ids()):
            return EditOutcome(
                False,
                f"reorder must list every scene exactly once — ids: {sorted(script.scene_ids())}",
            )
        by_id = {s.scene_id: s for s in script.scenes}
        script.scenes = [by_id[i] for i in order]
        script.renumber()
        return EditOutcome(True, f"reordered; scenes renumbered 1-{len(script.scenes)}")

    if m := _SCENE_EDIT.match(command):
        sid, field_raw, value = int(m.group(1)), m.group(2).lower(), m.group(3).strip()
        scene = _find_scene(script, sid)
        if scene is None:
            return EditOutcome(False, f"no scene {sid} — ids: {sorted(script.scene_ids())}")
        if field_raw == "narration":
            scene.narration = value
        elif field_raw == "text" or field_raw.startswith("on"):
            scene.on_screen_text = value
        elif field_raw.startswith("visual"):
            scene.visual_keyword = value
        elif "emphasis" in field_raw:
            scene.audio_emphasis = value
        else:  # duration
            try:
                scene.approx_duration_sec = float(value.removesuffix("s").strip())
            except ValueError:
                return EditOutcome(False, f"duration must be a number of seconds, got '{value}'")
        return EditOutcome(True, f"updated scene {sid} {field_raw}")

    if m := _TITLE_EDIT.match(command):
        script.title = m.group(1).strip()
        return EditOutcome(True, "updated title")

    if m := _HOOK_EDIT.match(command):
        script.final_angle.hook = m.group(1).strip()
        return EditOutcome(True, "updated hook (angle rationale unchanged)")

    if m := _ANGLE_EDIT.match(command):
        script.final_angle.chosen_angle = m.group(1).strip()
        return EditOutcome(True, "updated chosen angle")

    if m := _SHORTHAND.match(command):
        sid, value = int(m.group(1)), m.group(2).strip()
        scene = _find_scene(script, sid)
        if scene is None:
            return EditOutcome(False, f"no scene {sid} — ids: {sorted(script.scene_ids())}")
        scene.narration = value
        return EditOutcome(True, f"updated scene {sid} narration")

    return EditOutcome(False, "unrecognized edit — type 'help' for commands")
