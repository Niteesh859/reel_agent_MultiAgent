# reels-agent — Phase 1 (script pipeline)

A staged, multi-agent pipeline that turns a topic into a **human-approved, publish-ready
Instagram Reel script**. Phase 1 implements agents 1–3 of the PRD
(`reels_agent_prd.md`): **Researcher → ◇ checkpoint → Strategist ⇄ Writer script loop →
◇ checkpoint → exported script**. Agents 4–5 (Producer/Assembler: visuals, TTS, FFmpeg
render) are Phase 2 and exist only as stubs.

| | |
|---|---|
| LLM | OpenRouter · `deepseek/deepseek-v4-flash` ($0.09/$0.18 per Mtok → **≈ $0.01/reel**) |
| Web search | Tavily, basic depth (1 credit/query; free tier = 1,000/month → **$0** at 1–2 reels/day) |
| Checkpoints | TUI always · Telegram auto-enables when its keys exist · first answer wins |
| Observability | LangSmith tracing (opt-in via env) |

## Setup

```bash
uv sync                      # installs everything (Python ≥3.11)
cp .env.example .env         # then fill in the keys below
```

| Key | Required | Where to get it |
|---|---|---|
| `OPENROUTER_API_KEY` | ✅ | https://openrouter.ai/keys |
| `TAVILY_API_KEY` | ✅ | https://app.tavily.com (free tier, no card) |
| `TELEGRAM_BOT_TOKEN` + `TELEGRAM_CHAT_ID` | optional | @BotFather → token; message your bot, then read your chat id from `https://api.telegram.org/bot<TOKEN>/getUpdates` |
| `LANGSMITH_*` | optional | see **Observability** below |

The CLI refuses to start and tells you exactly which required keys are missing, so you
can fill them in whenever you're ready.

## Run

```bash
uv run python -m src.main "why octopuses are basically aliens"
uv run python -m src.main "AI in F1 racing" --angle "team radio secrets"
uv run python -m src.main --tui-only "topic"     # ignore Telegram even if configured
uv run pytest                                    # 35 offline tests (no keys needed)
```

The pipeline pauses at **two checkpoints** (research, then angle+script). On approval of
the second one, the deliverable lands in `output/scripts/` as both `.json`
(§5.3 schema, Phase-2-ready) and `.md` (human-readable, with the sources appendix).
Every intermediate artifact — each research attempt, every loop round's command/
execution/decision — is saved under `output/runs/<reel_id>/` for inspection.

**Timing expectations — it is not stuck.** A full run takes **5–10 minutes** before
the second checkpoint: research ~1–3 min, then 3–5 script-loop rounds at ~1–2 min
each (two LLM calls per round; DeepSeek spends extra time on reasoning tokens).
Every in-flight call prints a dim progress line (`writer drafting… (20-90s)`), so
silence never lasts more than a moment. If a provider call genuinely wedges, it is
killed after 240s (`llm.request_timeout_sec`), retried once with a visible
`[guardrail] … retrying once…` line, and on a second failure the pipeline pauses
with a red `⏸ pipeline paused` message — so a real hang always resolves itself
within ~8 minutes. **Don't Ctrl+C while a progress line is showing.**

## Checkpoint actions (TUI and Telegram, full parity)

| Action | How |
|---|---|
| Approve | type `approve` (or `a`/`ok`/`y`) · Telegram: ✅ button |
| Show full detail | `full` · Telegram: 📄 button |
| Skip reel | `skip` · Telegram: 🛑 button |
| Regenerate with note | `regen: too formal, punch it up` |
| Edit | field-addressed commands below |
| Help | `help` |

**Research edits** — `edit fact f2 claim: <text>` (also `source`, `url`, `date`,
`credibility`, `rank`) · `delete fact f3` · `edit summary: <text>` · `edit notes: <text>`

**Script edits** — `edit scene 3 narration: <text>` (also `text` = on-screen, `visual`,
`emphasis`, `duration`) · `3: <text>` shorthand for scene 3's narration ·
`delete scene 2` · `insert after 2: <narration> | <on-screen>` · `reorder 3,1,2` ·
`edit title:` / `edit hook:` / `edit angle:`

Loose phrasings normalize to the same operations (`change scene 2 visual to cat piano`,
`remove scene 2`). In Telegram, after 📄 Show full, **reply to any scene message** to
rewrite that scene's narration directly (or reply `text: ...` / `visual: ...` to target
a field).

## How to evaluate Phase 1

The PRD's KPI is **per-step approval rate ≈ 80%** across the two active checkpoints.

1. **Run 10–15 reels** across topic types you actually post (tech explainer, history,
   science oddity, current-ish news). For a few, pass `--angle` to test hint handling.
2. **At each checkpoint judge the output on its own merit**, then act honestly:
   approve if you'd genuinely ship it, `regen: <why>` if not, edit only for small fixes.
   Every action is logged to `output/metrics.jsonl`.
3. **Compute the approval rate per stage:**

   ```bash
   uv run python - <<'PY'
   import json, collections
   stats = collections.defaultdict(collections.Counter)
   for line in open("output/metrics.jsonl"):
       r = json.loads(line)
       stats[r["stage"]][r["action"]] += 1
   for stage, c in stats.items():
       decisions = c["approve"] + c["regen"] + c["skip"]
       rate = c["approve"] / decisions if decisions else 0
       print(f"{stage:14s} approve-rate {rate:.0%}  {dict(c)}")
   PY
   ```

4. **What to look for per stage** (a weak stage maps to exactly one agent to fix):
   - *Research:* facts concrete/surprising vs generic? every source URL real (open 2–3
     spot-checks — facts with `[unverified source]` prefixes are the Researcher
     hallucinating URLs)? broad coverage even when you gave an angle hint?
   - *Angle+script:* would the hook stop your scroll in <2s? does `paths_considered`
     show real alternatives or filler? read the narration aloud — does it fit the
     target length (~2.5 words/sec)? `convergence: cap_reached` flags scripts that
     needed the iteration cap — inspect those closer (PRD §4).
   - *Loop behavior:* in `output/runs/<reel_id>/`, diff round N vs N+1 executions —
     revisions should be **targeted** (only `revised_scene_ids` change materially),
     not full rewrites.
5. **Tuning levers** (in `config/settings.yaml`): loop `min_rounds`/`max_rounds`,
   per-agent `temperatures`, `max_facts`, models per agent (swap any OpenRouter slug).
   Agent behavior lives in the prompt constants at the top of
   `src/agents/{researcher,strategist,writer}.py`.

## Observability — LangSmith

Set in `.env`:

```
LANGSMITH_TRACING=true
LANGSMITH_API_KEY=<your key>
LANGSMITH_PROJECT=reels-agent
LANGSMITH_ENDPOINT=https://api.smith.langchain.com   # default; change for EU/self-hosted
```

Every run then produces one trace per reel in the `reels-agent` project at
https://smith.langchain.com with this run tree (names come from `@traceable`
decorators; the reel_id/topic appear in the root run's inputs):

```
reel-pipeline
├── researcher
│   ├── ChatCompletion (query planning)
│   ├── tavily-search
│   └── ChatCompletion (brief extraction)
└── script-loop
    ├── strategist-backbone → ChatCompletion
    ├── writer-execute      → ChatCompletion     (round 1)
    ├── strategist-assess   → ChatCompletion
    └── …repeats per round (up to max_rounds)
```

Raw OpenRouter calls are captured via LangSmith's OpenAI wrapper, so each
`ChatCompletion` child shows the exact prompts, JSON outputs, token counts, and
latency — useful for documenting per-stage cost and for debugging schema-retry loops
(a retried call appears as extra messages in the same run). Tracing is a strict no-op
when `LANGSMITH_TRACING` is unset, and a LangSmith outage can never block the pipeline.

## Project structure

PRD §10 layout, plus four small shared modules the build phase added:

```
src/
├── main.py                 entrypoint: wiring, checkpoint views, CLI
├── settings.py             settings.yaml + .env loader          (added)
├── llm.py                  OpenRouter client + schema-validated JSON calls (added)
├── tracing.py              LangSmith @traceable shim            (added)
├── agents/                 researcher · strategist · writer · producer/assembler stubs
├── loop/                   controller (floor/cap/convergence) · phase logic
├── schemas/                §5.1 research · §5.2 loop messages · §5.3/§5.4 outputs
├── io/                     tui · telegram · export · edits (added) · checkpoint (added)
└── guardrails/failures.py  infra retry-once → notify + pause (§7)
output/
├── runs/<reel_id>/         every stage + loop-round artifact (reproducible handoffs)
├── scripts/                approved exports (the Phase 1 deliverable)
└── metrics.jsonl           checkpoint actions → KPI approval rate
```

Notes: `min_rounds=3` implements the PRD's "minimum 2–3 rounds" (floor is enforced by
the controller even if the Strategist signals convergence earlier); the iteration cap
surfaces the best draft flagged `cap_reached`. Infra failures retry once, then the
pipeline notifies all channels and pauses with artifacts intact (§7). Fact JSON travels
in-context to both loop agents per the pre-Phase-3 stopgap (§11).

## Phase 2 (not built)

Producer (Giphy/Pexels visuals, TTS, music) and Assembler (FFmpeg render, word-highlight
captions, MP4 delivery). The approved script JSON already carries `visual_keyword` and
`audio_emphasis` per scene, so Phase 2 consumes it without rework.
