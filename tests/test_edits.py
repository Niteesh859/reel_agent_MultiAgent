"""Field-addressed edit commands — strict syntax and loose phrasings (PRD §6)."""

from src.io.edits import apply_research_edit, apply_script_edit
from src.schemas.outputs import ConvergedScript, FinalAngle, FinalScene
from tests.test_schemas import make_brief


def make_script(n: int = 3) -> ConvergedScript:
    return ConvergedScript(
        title="Original title",
        target_length_sec=45,
        final_angle=FinalAngle(
            chosen_angle="countdown", hook="old hook", rationale="fits"
        ),
        iterations_used=3,
        convergence="agreed",
        scenes=[
            FinalScene(
                scene_id=i,
                on_screen_text=f"overlay {i}",
                narration=f"line {i}",
                visual_keyword=f"kw {i}",
                audio_emphasis="none",
                approx_duration_sec=10.0,
            )
            for i in range(1, n + 1)
        ],
    )


# ── research edits ──────────────────────────────────────────────────────────


def test_edit_fact_claim():
    brief = make_brief()
    outcome = apply_research_edit(brief, "edit fact f2 claim: corrected claim")
    assert outcome.ok
    assert brief.get_fact("f2").claim == "corrected claim"


def test_edit_fact_loose_phrasing_and_bare_id():
    brief = make_brief()
    assert apply_research_edit(brief, "change fact 3 url to https://new.example/x").ok
    assert brief.get_fact("f3").source_url == "https://new.example/x"


def test_delete_fact():
    brief = make_brief()
    assert apply_research_edit(brief, "delete fact f1").ok
    assert brief.fact_ids() == {"f2", "f3"}


def test_edit_fact_followup_question():
    brief = make_brief()
    assert apply_research_edit(brief, "edit fact f1 followup: how does it scale?").ok
    assert brief.get_fact("f1").followup_question == "how does it scale?"
    assert apply_research_edit(brief, "change fact 2 follow-up to why now?").ok
    assert brief.get_fact("f2").followup_question == "why now?"


def test_edit_summary_and_unknown_fact():
    brief = make_brief()
    assert apply_research_edit(brief, "edit summary: new summary").ok
    assert brief.research_summary == "new summary"
    outcome = apply_research_edit(brief, "edit fact f9 claim: nope")
    assert not outcome.ok and "f9" in outcome.message


def test_unrecognized_research_edit():
    assert not apply_research_edit(make_brief(), "make it pop").ok


# ── script edits ────────────────────────────────────────────────────────────


def test_edit_scene_narration_strict_and_shorthand():
    script = make_script()
    assert apply_script_edit(script, "edit scene 2 narration: fresh line").ok
    assert script.scenes[1].narration == "fresh line"
    assert apply_script_edit(script, "3: shorthand line").ok
    assert script.scenes[2].narration == "shorthand line"


def test_edit_scene_other_fields():
    script = make_script()
    assert apply_script_edit(script, "edit scene 1 text: NEW OVERLAY").ok
    assert script.scenes[0].on_screen_text == "NEW OVERLAY"
    assert apply_script_edit(script, "change scene 1 visual to cat playing piano").ok
    assert script.scenes[0].visual_keyword == "cat playing piano"
    assert apply_script_edit(script, "set scene 1 duration: 6.5").ok
    assert script.scenes[0].approx_duration_sec == 6.5
    assert not apply_script_edit(script, "set scene 1 duration: fast").ok


def test_delete_scene_renumbers():
    script = make_script()
    assert apply_script_edit(script, "remove scene 2").ok
    assert [s.scene_id for s in script.scenes] == [1, 2]
    assert script.scenes[1].narration == "line 3"


def test_cannot_delete_last_scene():
    script = make_script(1)
    assert not apply_script_edit(script, "delete scene 1").ok


def test_insert_after():
    script = make_script()
    outcome = apply_script_edit(
        script, "insert after 1: brand new spoken line here | NEW ON-SCREEN"
    )
    assert outcome.ok
    assert [s.scene_id for s in script.scenes] == [1, 2, 3, 4]
    assert script.scenes[1].narration == "brand new spoken line here"
    assert script.scenes[1].on_screen_text == "NEW ON-SCREEN"


def test_reorder():
    script = make_script()
    assert apply_script_edit(script, "reorder 3,1,2").ok
    assert [s.narration for s in script.scenes] == ["line 3", "line 1", "line 2"]
    assert [s.scene_id for s in script.scenes] == [1, 2, 3]
    assert not apply_script_edit(script, "reorder 1,2").ok


def test_title_hook_angle():
    script = make_script()
    assert apply_script_edit(script, "edit title: Better title").ok
    assert script.title == "Better title"
    assert apply_script_edit(script, "edit hook: sharper hook").ok
    assert script.final_angle.hook == "sharper hook"
    assert apply_script_edit(script, "change angle to myth-buster").ok
    assert script.final_angle.chosen_angle == "myth-buster"


def test_unknown_scene_and_unrecognized():
    script = make_script()
    assert not apply_script_edit(script, "edit scene 9 narration: x").ok
    assert not apply_script_edit(script, "just make it better").ok
