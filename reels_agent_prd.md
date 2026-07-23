# Product Requirements Document — Autonomous Reels Creation Agent

**Version:** 1.0
**Status:** Locked for build
**Owner:** Solo operator (single user)
**Document type:** PRD + tool blueprint

---

## 1. Overview

A staged, multi-agent pipeline that turns a topic (and optional story angle) into a finished, publish-ready Instagram Reel. The system is a **staged copilot**: five specialized agents run in sequence, and a human reviews and approves the output of each stage before the next begins. Two of those agents — the Strategist and the Writer — do not run linearly but instead work as a **directed loop** (minimum 2–3 rounds): the Strategist directs and the Writer executes, iterating to converge on the reel's script before it reaches the human. The agent does **not** auto-publish — it delivers a finished MP4 for the operator to upload manually.

The design goal is high creative control with automation of the labor-intensive parts (research, scripting, asset gathering, rendering), not hands-off autonomy.

The full five-agent design is specified in one place below, but it ships in three build phases — **Phase 1: the script pipeline (agents 1–3)**, **Phase 2: production (agents 4–5)**, **Phase 3: retrieval/RAG**. See §12 for the phased rollout. Everything between here and §12 describes the complete system; the phases are a build order over that same design, not separate architectures.

### Design principles

- **Human-in-the-loop at every stage.** Quality is enforced by review at each checkpoint rather than by post-hoc rules.
- **One agent, one job — with one deliberate exception.** Each agent has a narrow, distinct responsibility so a rejection at any checkpoint maps to exactly one agent to fix or improve. The exception is scriptwriting: the Strategist (thinking/direction) and Writer (execution) are intentionally coupled into a directed loop rather than isolated stages, because the script's structure and its wording are developed together.
- **Research before creativity.** Facts are gathered broadly and angle-agnostically *before* any creative framing, so the angle is grounded in what the material actually supports.
- **Iterate to converge on the script.** The reel's script is the highest-leverage artifact, so it is not produced in a single pass — the Strategist directs the Writer over a minimum of 2–3 rounds until the script is tight before a human sees it.
- **Reproducible handoffs.** Agents pass structured JSON, not free prose, so each stage is inspectable and re-runnable.

---

## 2. Goals & Success Metrics

### Primary KPI

**Per-step approval rate — target ~80%.** At each of the four checkpoints, roughly 80% of the agent's proposals should be accepted without needing rework. This measures the quality of each agent's output independently, so a weak stage is visible rather than hidden inside an end-to-end number. (The Strategist–Writer loop resolves internally over 2–3 rounds and surfaces as a single script checkpoint, which is why there are four human checkpoints across five agents.)

### Scope boundaries

| In scope | Out of scope |
|---|---|
| Topic research | Auto-publishing to Instagram |
| Content strategy / angle selection | Sourcing the operator's own footage |
| Scriptwriting (scene-by-scene) | Managing the Instagram account |
| Visual + audio asset gathering | Analytics / performance tracking (v1) |
| Video rendering + captions | Multi-platform cross-posting |
| Delivery of final MP4 to operator | — |

### Volume

1–2 reels/day to start.

---

## 3. Architecture

A five-agent pipeline with four human checkpoints. Most agents run in sequence, each pausing for a human checkpoint before the next runs. The Strategist and Writer are the exception: they form an internal directed loop (Strategist directs, Writer executes) that iterates 2–3 rounds before surfacing a single converged script for review.

```
[Operator input: topic and/or angle]
        │
        ▼
┌─────────────────┐
│ 1. RESEARCHER   │  broad, angle-agnostic facts
└─────────────────┘
        │  ◇ CHECKPOINT 1: review raw research
        ▼
╔═════════════════════════════════════════════════════╗
║  SCRIPT LOOP  (min 2-3 rounds to converge)          ║
║                                                     ║
║  R1 backbone / R2+ emphasis one part                ║
║   ┌─────────────────┐ ──────────────────────▶       ║
║   │ 2. STRATEGIST   │   command (down)              ║
║   │  directs        │                               ║
║   └─────────────────┘ ◀──────────────────────       ║
║           ▲             updated script (up)         ║
║           │                                         ║
║           ▼                                         ║
║   ┌─────────────────┐                               ║
║   │ 3. WRITER       │  R1: writes full script       ║
║   │  executes       │  R2+: revises the part        ║
║   └─────────────────┘                               ║
║                                                     ║
║   Strategist judges when script is tight            ║
║   (or iteration cap hit)                            ║
╚═════════════════════════════════════════════════════╝
        │  ◇ CHECKPOINT 2: review converged angle + script
        ▼
┌─────────────────┐
│ 4. PRODUCER     │  per-scene visuals + TTS + music
└─────────────────┘
        │  ◇ CHECKPOINT 3: review visuals + audio
        ▼
┌─────────────────┐
│ 5. ASSEMBLER    │  FFmpeg render + burned captions
└─────────────────┘
        │  ◇ CHECKPOINT 4: final approve/reject
        ▼
[Final MP4 → Telegram/WhatsApp → operator uploads manually]
```

### Checkpoint interaction model

- **Channels:** Telegram/WhatsApp (mobile, on-the-go) **and** a TUI (terminal, denser/faster review), with full parity.
- **Four actions per checkpoint:** Approve (advance), Edit (change the content directly via field-addressed commands), Regenerate-with-note (send a comment, agent redoes the stage), and Skip-reel (abandon this reel). The pipeline resumes from the approved/edited output, re-runs the stage on regenerate, or halts on skip.
- Full message layout, action surfacing, and concurrency behavior are specified in §6 (Checkpoint interaction).

---

## 4. Agent Specifications

### Agent 1 — Researcher

- **Job:** Given the topic, gather facts broadly and angle-agnostically — even when the operator supplies an angle upfront, research goes broad first and the angle is refined from findings later.
- **Fact-check rule:** single credible source is acceptable; the source is captured for the operator's own verification at the research checkpoint, but is used as story material rather than displayed on-screen.
- **Output:** structured research brief (see schema §5).
- **Checkpoint:** operator reviews raw facts on their own merit before any angle is layered on.

### Agents 2 & 3 — Strategist + Writer (Script Loop)

The Strategist and Writer operate as a **coupled directed loop**, not two isolated stages — but the relationship is hierarchical, not peer-to-peer. The **Strategist does the thinking**: it holds full context (angle, hook, research, and the whole script) and issues commands; the **Writer executes**, rendering those commands as script text. The loop runs in two distinct phases:

- **Round 1 — backbone pass:** the Strategist hands the Writer an *overall backbone* — the whole-script skeleton (structure, beat order, what each section is for) — and the Writer writes the **complete** script in one pass from it.
- **Round 2+ — focused revision passes:** the Strategist reads the full script back, then zooms in on **one primary part** for a major change (plus optional lighter touches on a few secondary parts), and the Writer revises accordingly. Each revision round is targeted, not a full rewrite.

They iterate for a **minimum of 2–3 rounds** until the Strategist judges the script tight. This split exists because the agent tracking the overall arc (does the hook land, does the structure build, does every scene earn its place) needs the wide view of the whole script, while writing and revising strong individual lines needs focused execution — so one agent stays wide and directs, the other goes deep and writes.

**Agent 2 — Strategist**

- **Job:** Given the approved research, ask the core question — *how should this content be shown?* — rather than settling on the first workable angle. It enumerates the plausible ways to present the material (e.g. myth-buster, countdown, "did you know," problem→reveal, story-driven), reasons about which best fits the topic and the casual/energetic explainer tone, and optimizes toward the strongest of those paths. It then critiques and steers each of the Writer's drafts across loop iterations against that chosen path.
- **Persona:** thinks like a content strategist — proposes hooks, framing, and "why this angle over the alternatives it considered." **Never overrides the operator's core topic**; it suggests and builds, it does not redirect.
- **In the loop:** owns the angle and presentation strategy. On the **backbone pass** it hands the Writer the whole-script skeleton — beat order and the purpose of each section — without dictating wording. On every **revision pass** it reads the full returned script, identifies the single part most in need of a major change, emphasizes that one **primary** part (optionally naming a few **secondary** parts for lighter fixes), and specifies what should change and why. It re-evaluates whether the chosen path is still best as the script takes shape, and decides when the script is tight enough to converge.

**Agent 3 — Writer**

- **Job:** On the backbone pass, take the Strategist's whole-script skeleton and write the complete script from it in one pass — turning each section of the backbone into scene-by-scene outline and dialogue/script text. On revision passes, apply the Strategist's targeted changes to the emphasized parts.
- **In the loop:** owns the script draft. Round 1 it writes the full script from the backbone. Round 2+ it applies the requested **major change to the primary emphasized part** and any lighter adjustments to the named secondary parts, leaving the rest of the script untouched unless continuity requires a small tweak. It focuses purely on **how** to execute each command in effective script text — word choice, phrasing, pacing — and does not push back on the ideas or redirect the strategy; that thinking belongs to the Strategist.

**Loop mechanics**

- **Iteration floor:** at least the backbone pass (round 1) plus 1–2 focused revision passes before the script can surface for review — so a minimum of 2–3 rounds total; a lone backbone pass with no revision is not permitted.
- **Convergence / exit:** the loop ends when the **Strategist** judges the script tight (hook lands, pacing works, every scene earns its place) **or** a hard iteration cap is reached (recommend 5) to prevent infinite loops. On hitting the cap, the best draft so far surfaces, flagged as "cap-reached" so the operator knows it may need a closer look.
- **Output:** a single converged package — angle + hook + rationale **and** the full scene-by-scene script (see schemas §5).
- **Checkpoint (shared):** the operator reviews the converged angle **and** script together at one checkpoint, rather than approving the angle and the script separately.

### Agent 4 — Producer

- **Job:** For each scene: source a visual (Giphy + Pexels/Unsplash keyword search), generate the TTS narration, and select a background track from a **fixed, pre-cleared royalty-free playlist** (rotated).
- **Output:** per-scene asset manifest (visual file/URL, audio file, timing) + selected music track (see schema §5).
- **Checkpoint:** operator reviews visuals + audio together.

### Agent 5 — Assembler

- **Job:** Render the final video via FFmpeg — compose visuals, narration, and music; burn in **Instagram-native-style word-by-word highlight captions**. Output a 30–60s vertical MP4.
- **Delivery:** send the finished MP4 to the operator via Telegram/WhatsApp/TUI.
- **Checkpoint:** operator gives final approve/reject; on approval the operator uploads to Instagram manually.

---

## 5. Data Handoff Schemas

Each stage emits JSON consumed by the next. All schemas are illustrative starting points.

### 5.1 Researcher → Strategist

```json
{
  "topic": "string (operator-provided)",
  "operator_angle_hint": "string | null",
  "research_summary": "string (2-3 sentence overview of what was found)",
  "facts": [
    {
      "fact_id": "f1",
      "claim": "string",
      "source_name": "string",
      "source_url": "string",
      "source_date": "string | null (publication date if available)",
      "credibility_note": "string",
      "relevance_rank": 1
    }
  ],
  "topic_level_notes": "string | null (anything about the topic as a whole the Strategist should know)"
}
```

**Field provenance — how each field is created:**

| Field | Created by | How |
|---|---|---|
| `topic` | Passed through | Copied verbatim from the operator's input. |
| `operator_angle_hint` | Passed through | The operator's optional angle, carried forward untouched (research still goes broad first regardless). |
| `research_summary` | Generated | Written by the Researcher after gathering, as a 2–3 sentence synthesis of the findings. |
| `facts[].fact_id` | Assigned | Auto-assigned sequentially (`f1`, `f2`, …) by the Researcher so downstream agents can reference facts stably. |
| `facts[].claim` | Generated | The Researcher's own concise restatement of each finding. |
| `facts[].source_name` / `source_url` | Retrieved | Captured directly from the source the claim came from. |
| `facts[].source_date` | Retrieved | Pulled from the source's metadata; `null` if none is available. |
| `facts[].credibility_note` | Derived | The Researcher's judgment on how much to trust the source. |
| `facts[].relevance_rank` | Derived | The Researcher's ranking of each fact's importance to the topic (1 = most relevant), used to triage for a short format. |
| `topic_level_notes` | Generated | Optional observations about the topic as a whole that don't attach to any single fact. |

Categories: **passed through** (unchanged from input), **retrieved** (lifted from a source), **assigned** (mechanically generated ID), **generated** (Researcher writes it fresh), **derived** (Researcher's judgment/analysis over the findings — e.g. `credibility_note`, `relevance_rank`).

### 5.2 Script Loop — internal messages (Strategist ↔ Writer)

Exchanged **within** the loop on each round; not surfaced to the operator. The flow is strictly one-way in authority: the **Strategist issues a command** message down to the Writer, and the **Writer returns an execution** message back up. The command comes in one of two shapes depending on phase — a **backbone** command on round 1, or a **focused-emphasis** command on every revision round. The Strategist owns strategy and the convergence decision; the Writer owns only the wording.

**Structure at a glance** — the three message shapes and how they nest:

```
5.2a  Strategist → Writer (command)
├─ iteration
├─ phase ................... "backbone" | "revision"
├─ content_architecture ... static all loop: "hook | buildup | reveal | payoff (held off)"
├─ angle  (read-only context, static all loop)
│   ├─ chosen_angle
│   └─ hook
├─ backbone  ◄── present only when phase = "backbone"
│   ├─ target_length_sec
│   └─ sections[]
│       ├─ section_id
│       ├─ purpose
│       ├─ note
│       └─ facts_to_use[]
└─ emphasis  ◄── present only when phase = "revision"
    ├─ primary  (required — the one major change)
    │   ├─ scene_ref
    │   ├─ change_type
    │   ├─ note
    │   └─ facts_to_use[]
    └─ secondary[]  (optional lighter touches)
        ├─ scene_ref
        └─ note

5.2b  Writer → Strategist (execution)
├─ iteration
├─ phase
├─ revised_scene_ids[] ..... which scenes changed (empty on backbone)
└─ script_draft
    ├─ title
    ├─ target_length_sec
    ├─ status
    └─ scenes[]
        ├─ scene_id
        ├─ section_id ........ maps scene back to a backbone section
        ├─ on_screen_text
        ├─ narration
        ├─ visual_keyword
        ├─ audio_emphasis .... 1-2 things for the Producer's TTS to stress
        └─ approx_duration_sec

Convergence decision (Strategist-internal)
├─ iteration
├─ assessment_notes[]
└─ converged ............... true → emit 5.3 output | false → loop again
```

Loop flow: `5.2a command ─▶ 5.2b execution ─▶ convergence decision ─▶ (loop back to 5.2a | exit to §5.3)`

#### 5.2a Strategist → Writer (command)

The `phase` field selects which command shape applies. On `backbone` the `backbone` block is present and `emphasis` is null; on `revision` the `emphasis` block is present (with a required primary part) and `backbone` is null. Two fields stay **constant across every message in the loop**: `angle` and `content_architecture` — the Writer treats both as read-only context and never edits them.

```json
{
  "iteration": 1,
  "phase": "backbone | revision",
  "content_architecture": "hook | buildup | partial reveal | payoff (held off)",
  "angle": {
    "chosen_angle": "string",
    "hook": "string"
  },

  "backbone": {
    "comment": "present only when phase == 'backbone'; null otherwise",
    "target_length_sec": 45,
    "sections": [
      {
        "section_id": "s1",
        "purpose": "e.g. hook | context | build | reveal | payoff | CTA",
        "note": "string (what this section must accomplish)",
        "facts_to_use": ["fact_id"]
      }
    ]
  },

  "emphasis": {
    "comment": "present only when phase == 'revision'; null otherwise",
    "primary": {
      "scene_ref": "scene_id being emphasized (required)",
      "change_type": "e.g. rewrite | sharpen hook | fix pacing | punch up | cut | reorder",
      "note": "string (the major change this part needs and why)",
      "facts_to_use": ["fact_id"]
    },
    "secondary": [
      {
        "scene_ref": "scene_id",
        "note": "string (lighter adjustment; optional, may be empty)"
      }
    ]
  }
}
```

**Data model — how each field is created:**

| Field | Created by | How |
|---|---|---|
| `iteration` | Assigned | Loop controller's round counter; `1` on the backbone pass, incremented each round. |
| `phase` | Derived | Strategist sets `backbone` on round 1, `revision` on every later round. |
| `content_architecture` | Derived | Strategist's one-line arc of the reel (pipe-ordered beats), set once and carried unchanged on every message so the through-line stays visible after the round-1 backbone is gone. |
| `angle.chosen_angle` / `angle.hook` | Passed through | Carried unchanged from the Strategist's locked angle; read-only context for the Writer. |
| `backbone.target_length_sec` | Derived | Strategist's split of the target duration; present only on the backbone pass. |
| `backbone.sections[].section_id` | Assigned | Auto-assigned skeleton IDs (`s1`, `s2`, …) so scenes can map back to sections. |
| `backbone.sections[].purpose` | Derived | Strategist's structural intent for each section (hook, build, payoff, …). |
| `backbone.sections[].note` | Generated | Strategist writes what the section must accomplish. |
| `backbone.sections[].facts_to_use` | Derived | Strategist selects which research `fact_id`s belong in that section. |
| `emphasis.primary.scene_ref` | Derived | Strategist picks the one scene to focus the major change on; present only on revision passes. |
| `emphasis.primary.change_type` | Derived | Strategist's classification of the change needed. |
| `emphasis.primary.note` | Generated | Strategist writes the specific major change and its reason. |
| `emphasis.primary.facts_to_use` | Derived | Any additional `fact_id`s the revised part should incorporate. |
| `emphasis.secondary[]` | Generated / Derived | Optional lighter touches; Strategist names the `scene_ref`s and writes each note, may be empty. |

> **`content_architecture` vs. `backbone` — same arc, different jobs.** They carry the same underlying sequence of beats, but serve different purposes and lifetimes. `content_architecture` is the loose one-line **spine** (only the `|` order is meaningful) and is **persistent** — it rides on every message so the through-line stays visible even deep into revision rounds. `backbone` is the detailed one-time **blueprint** (per-section IDs, notes, fact assignments) sent only on round 1 to build the first draft, then absent. Think compass (always in hand) vs. blueprint (used once to build, then filed away).

#### 5.2b Writer → Strategist (execution)

Carries the rendered script draft. No strategy, angle, or convergence fields — the Writer executes and returns, it does not assess. On the backbone pass it returns the complete script; on revision passes it returns the full updated script and reports which scenes it changed (the emphasized primary/secondary parts, plus any minor continuity tweaks).

```json
{
  "iteration": 1,
  "phase": "backbone | revision",
  "revised_scene_ids": ["for revision phase: scene_ids actually changed this round; empty on backbone"],
  "script_draft": {
    "title": "string",
    "target_length_sec": 45,
    "status": "complete (the full script is always returned)",
    "scenes": [
      {
        "scene_id": 1,
        "section_id": "backbone section this scene belongs to",
        "on_screen_text": "string",
        "narration": "string",
        "visual_keyword": "string",
        "audio_emphasis": "string (1-2 things the TTS should vocally stress, e.g. 'emphasize the part about the new attention mechanism')",
        "approx_duration_sec": 6
      }
    ]
  }
}
```

**Data model — how each field is created:**

| Field | Created by | How |
|---|---|---|
| `iteration` / `phase` | Passed through | Echoed back from the command so the controller can pair request and response. |
| `revised_scene_ids` | Derived | Writer reports which scenes it actually changed this round; empty on the backbone pass. |
| `script_draft.title` | Generated | Writer's title for the reel. |
| `script_draft.target_length_sec` | Passed through | Carried from the backbone command. |
| `script_draft.status` | Assigned | Always `complete` — the full script is returned every round. |
| `scenes[].scene_id` | Assigned | Sequential scene IDs the Writer assigns as it writes. |
| `scenes[].section_id` | Passed through | The backbone section this scene fulfills, carried from the command's skeleton. |
| `scenes[].on_screen_text` / `narration` | Generated | The Writer's actual script text — the core of its job. |
| `scenes[].visual_keyword` | Derived | Writer's search term suggestion for the Producer to source a visual. |
| `scenes[].audio_emphasis` | Derived | Writer names 1-2 moments the TTS should vocally stress (e.g. "emphasize the part about the new attention mechanism"), guiding the Producer's narration delivery. |
| `scenes[].approx_duration_sec` | Derived | Writer's estimate of the scene's spoken length. |

#### Convergence decision (Strategist-internal)

After reading the Writer's execution, the Strategist decides whether to issue another command (loop continues) or to converge and emit the §5.3 output. This assessment is the Strategist's alone — it is not sent to the Writer as a peer critique, it drives the next 5.2a command or the exit.

```json
{
  "iteration": 2,
  "assessment_notes": ["what's weak / what the next round must change"],
  "converged": false
}
```

**Data model — how each field is created:**

| Field | Created by | How |
|---|---|---|
| `iteration` | Passed through | The round just assessed. |
| `assessment_notes` | Derived | Strategist's judgment of what still needs work; feeds the next 5.2a command's emphasis. |
| `converged` | Derived | Strategist's decision — `true` ends the loop and triggers the §5.3 output, `false` continues it. |

### 5.3 Script Loop → Producer (converged output)

Emitted **once** when the loop converges (or hits the iteration cap). This is what the operator reviews at the shared angle+script checkpoint and what the Producer consumes.

```json
{
  "title": "string",
  "target_length_sec": 45,
  "final_angle": {
    "chosen_angle": "string",
    "hook": "string",
    "rationale": "string",
    "paths_considered": [
      { "path": "string", "why_not_chosen": "string | null" }
    ]
  },
  "iterations_used": 3,
  "convergence": "agreed | cap_reached",
  "scenes": [
    {
      "scene_id": 1,
      "on_screen_text": "string",
      "narration": "string",
      "visual_keyword": "string",
      "audio_emphasis": "string (1-2 things the TTS should vocally stress)",
      "approx_duration_sec": 6
    }
  ]
}
```

### 5.4 Producer → Assembler

```json
{
  "title": "string",
  "music_track": { "name": "string", "file": "path" },
  "scenes": [
    {
      "scene_id": 1,
      "visual": { "type": "image | gif", "source": "giphy | pexels | unsplash", "file": "path" },
      "narration_audio": { "file": "path", "duration_sec": 6.2 },
      "on_screen_text": "string",
      "caption_style": "word_highlight",
      "audio_emphasis": "string (carried from the script; already applied to narration_audio by the Producer)"
    }
  ]
}
```

---

## 6. Interface & Persona

- **Platform / spec:** Instagram Reels, vertical, 30–60 seconds.
- **Tone:** casual/energetic explainer — like a friend hyping you up.
- **Captions:** burned-in, Instagram-native word-by-word highlight style.
- **Delivery:** final MP4 via Telegram/WhatsApp + TUI; **operator uploads manually** (no Meta publishing API, no account linking, no OAuth).

### Checkpoint interaction (I/O layer)

The operator reviews and acts on each checkpoint through two channels — **Telegram/WhatsApp** (on-the-go) and a **TUI** (at a terminal) — with **full parity**: both can do everything, including edits. The design optimizes for fast evaluation first, deep editing only when needed.

**Message layout — progressive disclosure.** Every checkpoint message has the same three-part shape so the operator can judge it at a glance and drill in only if needed:

- **Header (always shown):** the persistent `content_architecture` spine + the chosen angle, plus `reel_id` and which stage this is. This keeps the reel's through-line in view at every checkpoint, so each stage is judged against the intended arc rather than in isolation.
- **Body (summary by default):** the gist only — for the research checkpoint, the top facts by `relevance_rank`; for the script checkpoint, one line per scene (opening/hook line, scene count, total runtime).
- **Full detail on demand:** a "show full" action expands to every fact / the complete scene-by-scene script.

**Four actions, surfaced by input needed.** The actions split by whether they need typed input, which is what lets full parity work despite Telegram being clunky for editing — decisions are one tap on either channel; only content edits require the keyboard:

- **Approve** — accept the stage, advance. Zero-argument → **button/keypress**.
- **Skip reel** — abandon this reel entirely, stop its pipeline. Zero-argument → **button/keypress**.
- **Edit** — change the content directly. Needs input → **typed field-addressed command**, e.g. `edit scene 3 narration: <text>`, `reorder`, `delete scene 2`, `insert`. The same syntax works identically in the TUI and Telegram. Editing is kept **flexible**: strict field commands are the backbone, but looser natural phrasing and reply-to-a-scene-message (for quick single-scene tweaks) are also accepted and normalize to the same underlying edit operations.
- **Regenerate with note** — send a comment and have the agent redo the stage, e.g. `regen: too formal, punch it up`. Needs input → **typed note**.

**Concurrency.** Phase 1 runs a **single reel at a time** — the pipeline blocks on the operator's checkpoint before starting another. However, **every checkpoint message carries a `reel_id` from day one**, even though only one reel exists in Phase 1. This is deliberate cheap insurance: supporting multiple reels in flight later becomes a message-routing change rather than a redesign, because the addressing is already in place. Multi-reel handling (a pending-reels queue/list) is a later enhancement.

The three `io/` modules implement this: `telegram.py` (Telegram/WhatsApp channel), `tui.py` (terminal), and `export.py` (saving the approved script to a file — the Phase 1 deliverable).

---

## 7. Guardrails & Safety

Because a human reviews every stage, content-quality failures are caught inline and no elaborate self-resolving rules are needed. Guardrails are therefore deliberately minimal:

- **Content quality:** enforced by per-step human review. No autonomous quality-gating.
- **Infrastructure/API failures** (e.g. TTS API down, render error): retry once, then notify the operator and pause that pipeline run — these are not content judgments and must escalate.
- **Sensitive content:** not machine-flagged. Because the operator reviews every stage — starting with the raw research at Checkpoint 1 — any health/politics/legal/tragedy sensitivity is caught by human judgment at review time rather than by an automated flag.
- **Fact integrity:** every featured claim traces to a single credible source captured during research and reviewable by the operator at the research checkpoint; facts are woven into the narrated story rather than cited on-screen, so unsourced claims should not survive the research checkpoint.

---

## 8. Checkpoint State Machine

```
       ┌──────────┐  approve   ╔══════════════════════════╗
 START │ RESEARCH │──────────▶ ║      SCRIPT LOOP         ║
       └──────────┘            ║  strategist ⇄ writer     ║
            ▲                  ║  (min 2–3, cap 5 rounds) ║
            │ edit /           ║  exit: agreed | cap      ║
            │ regen            ╚══════════════════════════╝
            └── re-run              │ approve (angle+script together)
                                    ▼
                            ┌──────────────┐
                            │   PRODUCE    │
                            └──────────────┘
                                    │ approve
                                    ▼
                  ┌──────────┐  approve   ┌──────────┐
                  │ ASSEMBLE │──────────▶ │  DELIVER │
                  └──────────┘            └──────────┘
                       ▲ edit / regen
                       └── (each review stage re-runs on edit/regen)

Human checkpoints (4): RESEARCH · SCRIPT LOOP output · PRODUCE · ASSEMBLE.
Per checkpoint, four actions: approve → advance | edit → apply direct change |
regenerate-with-note → re-run stage from a comment | skip-reel → halt this reel.
Script loop runs internally with NO human checkpoint between strategist and writer;
it iterates a minimum of 2–3 rounds and surfaces one converged script for review.
Infra failure at any stage → retry once → notify + pause.
```

---

## 9. Tool / API Requirements by Agent

| Agent | External tools/APIs |
|---|---|
| Researcher | Web search / browsing tool |
| Strategist | None (LLM reasoning over research JSON; exchanges loop messages with Writer) |
| Writer | None (LLM reasoning over angle + research; exchanges loop messages with Strategist) |
| Producer | Giphy API, Pexels API, Unsplash API, TTS API (ElevenLabs-style), local pre-cleared music library |
| Assembler | FFmpeg, caption rendering, Telegram/WhatsApp bot API for delivery |
| Orchestration | Checkpoint/state manager driving Telegram/WhatsApp + TUI; **script-loop controller** (tracks iteration count, enforces min/cap, detects convergence) |

---

## 10. Project Structure

A minimal package layout. The folder organization mirrors the pipeline: one module per agent, a shared layer for the schemas and loop controller, and a thin I/O layer for the checkpoint channels. Phase 2 agents (producer, assembler) are stubbed so the structure is stable from day one.

```
reels-agent/
├── README.md
├── pyproject.toml
├── .env.example
│
├── config/
│   ├── settings.yaml
│   └── music/
│
├── src/
│   ├── main.py
│   ├── agents/
│   │   ├── researcher.py
│   │   ├── strategist.py
│   │   ├── writer.py
│   │   ├── producer.py       [Phase 2]
│   │   └── assembler.py      [Phase 2]
│   ├── loop/
│   │   ├── controller.py
│   │   └── phase.py
│   ├── schemas/
│   │   ├── research.py
│   │   ├── loop_messages.py
│   │   └── outputs.py
│   ├── io/
│   │   ├── telegram.py
│   │   ├── tui.py
│   │   └── export.py
│   └── guardrails/
│       └── failures.py
│
├── output/
│   ├── scripts/
│   └── reels/
│
└── tests/
    ├── test_schemas.py
    └── test_loop.py
```

**What each part does:**

| Path | Role |
|---|---|
| `README.md` | Setup, run instructions, current phase status. |
| `pyproject.toml` | Dependencies and package metadata. |
| `.env.example` | Template for API keys (search, TTS, Giphy/Pexels, bot tokens). |
| `config/settings.yaml` | Tunable values: target length, iteration min/cap, KPI thresholds. |
| `config/music/` | Pre-cleared royalty-free playlist (Phase 2). |
| `src/main.py` | Entrypoint — wires the pipeline and the checkpoint loop together. |
| `agents/researcher.py` | Topic → broad, angle-agnostic facts (§5.1). |
| `agents/strategist.py` | Facts → angle + backbone/emphasis commands (§5.2a). |
| `agents/writer.py` | Commands → script execution (§5.2b). |
| `agents/producer.py` | Script → visuals + TTS + music. *Phase 2 stub.* |
| `agents/assembler.py` | Assets → rendered MP4. *Phase 2 stub.* |
| `loop/controller.py` | Runs the script loop: enforces min/cap rounds, detects convergence. |
| `loop/phase.py` | Backbone (round 1) vs. revision (round 2+) logic. |
| `schemas/research.py` | §5.1 research brief contract. |
| `schemas/loop_messages.py` | §5.2a command / §5.2b execution / convergence contracts. |
| `schemas/outputs.py` | §5.3 converged script and §5.4 producer→assembler contracts. |
| `io/telegram.py` | Telegram/WhatsApp approve / edit / reject channel. |
| `io/tui.py` | Terminal review interface. |
| `io/export.py` | Saves the approved script to a file (Phase 1 deliverable). |
| `guardrails/failures.py` | Infra-failure retry/notify — not content-quality gating. |
| `output/scripts/` | Exported approved scripts (Phase 1). |
| `output/reels/` | Rendered MP4s (Phase 2). |
| `tests/test_schemas.py` | Validates each handoff shape. |
| `tests/test_loop.py` | Checks the loop honors min/cap and convergence rules. |

The `schemas/` folder is deliberately its own layer because every agent and the loop controller depend on those contracts — keeping them in one place is what makes the handoffs "reproducible" as promised in the design principles. The `agents/` modules stay thin: each reads an input schema and emits an output schema, with no agent reaching into another's internals.

---

## 11. Open Items for Build Phase

These are implementation details, deliberately left for the build phase rather than the requirements phase:

- Exact JSON schema finalization and validation rules per handoff.
- Each agent's full system prompt / persona text.
- TUI layout and command set.
- Music playlist contents and rotation logic.
- Retry/backoff specifics for each external API.
- Caption styling parameters (font, highlight color, timing sync method).
- Script-loop convergence tuning: how "agreed/converged" is detected in practice (explicit agent signal vs. diminishing changes between rounds), and the final min/cap iteration values.

### Deferred to later phases

- **Shared sources store / RAG (Phase 3).** A persistent `SOURCES.md` (or vector store) holding every source the Researcher gathered, that the Strategist and Writer retrieve from on demand — with the Strategist pre-loaded with a medium digest for the round-1 backbone. Deferred so the earlier phases stay simple.
  - **Pre-Phase-3 stopgap:** no separate store and no retrieval layer. The Researcher's §5.1 JSON (which already contains each fact's full content) travels with the pipeline as the shared context both agents read; `facts_to_use` / `facts_to_feature` `fact_id`s resolve directly against that JSON. This works because a single reel's fact set is small enough to keep fully in context — the motivation for a retrieval store only appears at larger scale, which is why it's a later-phase concern.

---

## 12. Phased Rollout

The system ships in three phases so the highest-value, highest-risk part — the script pipeline — is validated before any production tooling is built.

### Phase 1 — Script pipeline (agents 1–3)

Researcher → Strategist → Writer, with the full backbone/emphasis script loop and the human checkpoints through the script stage. This is the core bet: if the Strategist–Writer loop can't reliably produce scripts you'd approve, no amount of production polish matters, so it's proven first.

- **Scope:** topic/angle input → research → angle → script loop → approved script. Agents 4–5 are not built.
- **Deliverable:** the final script is both surfaced for approval in the TUI/Telegram **and** saved as an exportable script file (markdown or JSON) the operator can read or use manually.
- **Checkpoints active:** research, and the converged angle+script. (The visuals+audio and final-video checkpoints don't exist yet.)
- **KPI:** the per-step approval-rate target applies to the research and script stages only.
- **Production hints:** the Writer still emits `visual_keyword` and `audio_emphasis` on each scene even though nothing consumes them yet — they're cheap to produce and leave the script output Phase-2-ready with no rework.

### Phase 2 — Production (agents 4–5)

Producer + Assembler layered on top of the Phase 1 script output: visuals (Giphy/Pexels), TTS, music, FFmpeg render, word-highlight captions, and MP4 delivery. This is where the previously-unused `visual_keyword` and `audio_emphasis` fields start being consumed.

- **Scope:** approved script → produced assets → rendered MP4 → delivered for manual upload.
- **Checkpoints added:** visuals+audio, and final approve/reject.
- **Delivery:** shifts from "exported script file" to "finished MP4 sent via Telegram/WhatsApp."

### Phase 3 — Retrieval / RAG

Replace the "§5.1 JSON travels in context" stopgap with the shared sources store described under *Deferred to later phases*: a persistent source file/store the Strategist and Writer retrieve from, with a medium digest pre-loaded to the Strategist for the round-1 backbone. Motivated by scale — worth building once fact sets grow beyond what fits comfortably in context.

> **Phasing note:** the agent numbering (1–5) and all schemas are defined once, in full, above; the phases are purely a *build order* over that same design, not different architectures. A field or checkpoint being "Phase 2" doesn't change its schema — it just isn't wired up until that phase.
