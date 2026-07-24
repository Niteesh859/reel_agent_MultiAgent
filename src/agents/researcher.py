"""Agent 1 — Researcher (PRD §4).

Topic → broad, angle-agnostic facts, gathered in two tiers of depth:
  TIER 1 (broad pass): angle-agnostic queries that map the whole topic.
  TIER 2 (deep pass): the model reads Tier-1's real results, autonomously picks
    the ≤3 most promising threads, and chases each with sharper digging queries.
The two-tier strategy is internal — the output is a flat §5.1 facts[] list,
ordered most-relevant first. The operator angle hint is carried through untouched.
Search backend: Tavily (search_depth configurable; advanced = 2 credits/query).
"""

from __future__ import annotations

import asyncio
from urllib.parse import urlparse

import httpx
from openai import AsyncOpenAI
from pydantic import BaseModel, Field

from src.llm import complete_json
from src.schemas.research import Fact, ResearchBrief
from src.settings import Settings, env
from src.tracing import traceable

TAVILY_URL = "https://api.tavily.com/search"
UNVERIFIED_PREFIX = "[unverified source — URL not among search results] "

_QUERY_SYSTEM = """You are an investigative journalist scoping a story. You plan web
research for a short-form explainer video (Instagram Reel).

Work in two tiers of depth:
- TIER 1 (broad pass): produce diverse, angle-agnostic queries that together cover
  core facts and definitions, surprising statistics or records, recent developments,
  common misconceptions, and notable expert findings or studies. Produce at most 6
  such queries — fewer if the topic is thin.
- TIER 2 (deep pass): from what Tier 1 surfaces, pick the most promising threads —
  the leads a journalist would chase past the surface answer — and produce sharper,
  digging queries for them. Follow your instinct for what's story-worthy; there is no
  fixed rule for what to pick. At most 3 threads, at most 3 queries deep each, and you
  may stop a thread early once it's answered.

Stay BROAD in Tier 1: do not narrow toward any single story angle, even if a hint is
provided — the hint only tells you one area that must not be missed, not the only area
to cover. Save the narrowing for Tier 2, where depth is the point."""

_BRIEF_SYSTEM = """You are an investigative journalist working a story — the Researcher
in a pipeline that turns topics into short Instagram Reels. From the web sources
provided, extract the handful of facts a content strategist could build a 30-60 second
energetic explainer from.

Chase like a journalist, but write like one too — the opposite discipline. Have strong
taste in WHAT you surface: favor the counterintuitive number, the detail that overturns
what people assume, the specific over the generic; a reader could have guessed a weak
fact without you, while a strong one makes them stop and go "wait, really?". But stay
completely neutral in HOW you write it up: every claim is plain, concrete, and sober —
no hype, no selling adjectives, no "mind-blowing". The excitement lives in the fact, not
your phrasing. You gather and report; you never pitch the story angle — that's the
editor's call.

Rules:
- Use ONLY the numbered sources provided. Every fact's source_url must be copied
  exactly from one of them. One credible source per claim is acceptable. A fact you
  can't source doesn't run.
- claim: your own concise restatement of the finding (1-2 sentences, concrete,
  self-contained).
- source_date: publication date if visible in the source text, else null.
- followup_question: the natural next question a curious viewer would ask after
  hearing this fact — concrete and specific, usable as a CTA or a future reel topic.
- List facts most-relevant first; order is the only ranking signal.
- research_summary: 2-3 sentences synthesizing what you found overall.
- topic_level_notes: observations about the topic as a whole that don't attach to a
  single fact (or null).
- Stay angle-agnostic: gather what the material supports, do not pitch a story."""


class _Tier1Plan(BaseModel):
    queries: list[str] = Field(min_length=2, max_length=10)


class _DeepThread(BaseModel):
    thread: str  # the lead being chased, in a few words
    queries: list[str] = Field(min_length=1, max_length=6)


class _Tier2Plan(BaseModel):
    threads: list[_DeepThread] = Field(default_factory=list, max_length=6)


class _FactLLM(BaseModel):
    claim: str
    source_url: str
    source_date: str | None = None
    followup_question: str


class _BriefLLM(BaseModel):
    research_summary: str
    facts: list[_FactLLM] = Field(min_length=3)
    topic_level_notes: str | None = None


def _normalize_url(url: str) -> str:
    parsed = urlparse(url.strip())
    return f"{parsed.netloc.lower().removeprefix('www.')}{parsed.path.rstrip('/')}"


async def _tavily_search(
    http: httpx.AsyncClient, settings: Settings, query: str
) -> list[dict]:
    response = await http.post(
        TAVILY_URL,
        headers={"Authorization": f"Bearer {env('TAVILY_API_KEY')}"},
        json={
            "query": query,
            "search_depth": settings.research.search_depth,
            "max_results": settings.research.tavily_max_results,
            "include_answer": False,
            # chunks_per_source is honored only on advanced depth; harmless on basic
            **(
                {"chunks_per_source": settings.research.chunks_per_source}
                if settings.research.search_depth == "advanced"
                else {}
            ),
        },
    )
    response.raise_for_status()
    return response.json().get("results", [])


async def _run_searches(
    settings: Settings, queries: list[str]
) -> tuple[dict[str, dict], list[BaseException]]:
    """Run every query concurrently; return {url: source} plus any per-query errors.

    Never raises on an empty result — the caller decides when the *combined* pool
    (across both tiers) is empty enough to fail the stage.
    """
    if not queries:
        return {}, []
    async with httpx.AsyncClient(timeout=30.0) as http:
        batches = await asyncio.gather(
            *(_tavily_search(http, settings, q) for q in queries),
            return_exceptions=True,
        )
    pool: dict[str, dict] = {}
    errors: list[BaseException] = []
    for batch in batches:
        if isinstance(batch, BaseException):
            errors.append(batch)
            continue
        for item in batch:
            url = item.get("url") or ""
            if not url or url in pool:
                continue
            pool[url] = {
                "title": item.get("title") or "(untitled)",
                "url": url,
                "content": (item.get("content") or "")[
                    : settings.research.source_content_chars
                ],
                "published_date": item.get("published_date"),
                "score": item.get("score") or 0.0,
            }
    return pool, errors


@traceable(name="tavily-broad", run_type="tool")
async def _search_broad(
    settings: Settings, queries: list[str]
) -> tuple[dict[str, dict], list[BaseException]]:
    return await _run_searches(settings, queries)


@traceable(name="tavily-deep", run_type="tool")
async def _search_deep(
    settings: Settings, queries: list[str]
) -> tuple[dict[str, dict], list[BaseException]]:
    return await _run_searches(settings, queries)


def _digest(pool: dict[str, dict], limit: int = 15) -> str:
    """Compact view of Tier-1 hits to inform Tier-2 thread selection."""
    ranked = sorted(pool.values(), key=lambda s: s["score"], reverse=True)[:limit]
    lines = []
    for i, s in enumerate(ranked, start=1):
        snippet = " ".join((s["content"] or "").split())[:220]
        lines.append(f"[{i}] {s['title']} — {snippet}")
    return "\n".join(lines) or "(no sources found in the broad pass)"


def _sources_block(sources: list[dict]) -> str:
    lines = []
    for i, s in enumerate(sources, start=1):
        date = f" | published: {s['published_date']}" if s.get("published_date") else ""
        lines.append(f"[{i}] {s['title']}{date}\nURL: {s['url']}\n{s['content']}\n")
    return "\n".join(lines)


@traceable(name="researcher", run_type="chain")
async def run_research(
    client: AsyncOpenAI,
    settings: Settings,
    topic: str,
    angle_hint: str | None,
    operator_note: str | None = None,
) -> ResearchBrief:
    model = settings.models.researcher
    temperature = settings.llm.temperatures.get("researcher", 0.3)
    reasoning_effort = settings.llm.reasoning_efforts.get("researcher")

    note_block = (
        f"\n\nOPERATOR REGENERATION NOTE — the previous research was rejected; "
        f"you must address this: {operator_note}"
        if operator_note
        else ""
    )

    # ── TIER 1 — broad pass ────────────────────────────────────────────────
    tier1 = await complete_json(
        client,
        settings,
        model=model,
        schema=_Tier1Plan,
        system=_QUERY_SYSTEM,
        user=(
            f"Topic: {topic}\n"
            f"Operator angle hint (context only, stay broad): {angle_hint or 'none'}\n"
            f"TIER 1 — broad pass. Produce up to "
            f"{settings.research.max_search_queries} diverse, angle-agnostic queries "
            f"that map the whole topic. Do not narrow yet."
            f"{note_block}"
        ),
        temperature=temperature,
        reasoning_effort=reasoning_effort,
    )
    searched: set[str] = set()
    broad_queries: list[str] = []
    for q in [topic, *tier1.queries]:
        if q.lower() not in searched:
            searched.add(q.lower())
            broad_queries.append(q)
    broad_queries = broad_queries[: settings.research.max_search_queries]

    pool, errors = await _search_broad(settings, broad_queries)

    # ── TIER 2 — deep pass, chosen from what Tier 1 actually surfaced ───────
    tier2 = await complete_json(
        client,
        settings,
        model=model,
        schema=_Tier2Plan,
        system=_QUERY_SYSTEM,
        user=(
            f"Topic: {topic}\n"
            f"Operator angle hint (context only): {angle_hint or 'none'}\n\n"
            f"TIER 1 surfaced these sources:\n{_digest(pool)}\n\n"
            f"TIER 2 — deep pass. Pick the up to "
            f"{settings.research.max_deep_threads} most promising threads a "
            f"journalist would chase past the surface, and for each give up to "
            f"{settings.research.max_deep_queries_per_thread} sharper, digging "
            f"queries (specifics, causes, implications). Return fewer threads or "
            f"queries if the leads are thin.{note_block}"
        ),
        temperature=temperature,
        reasoning_effort=reasoning_effort,
    )
    deep_queries: list[str] = []
    for thread in tier2.threads[: settings.research.max_deep_threads]:
        for q in thread.queries[: settings.research.max_deep_queries_per_thread]:
            if q.lower() not in searched:
                searched.add(q.lower())
                deep_queries.append(q)

    deep_pool, deep_errors = await _search_deep(settings, deep_queries)
    errors += deep_errors
    for url, src in deep_pool.items():
        pool.setdefault(url, src)

    if not pool:
        detail = f" (last error: {errors[-1]})" if errors else ""
        raise RuntimeError(f"web search returned no usable sources{detail}")
    sources = sorted(pool.values(), key=lambda s: s["score"], reverse=True)[:20]
    known_urls = {_normalize_url(s["url"]) for s in sources}

    # ── SYNTHESIS — flat facts[], two-tier depth invisible in the output ────
    brief_llm = await complete_json(
        client,
        settings,
        model=model,
        schema=_BriefLLM,
        system=_BRIEF_SYSTEM,
        user=(
            f"Topic: {topic}\n"
            f"Extract at most {settings.research.max_facts} facts.{note_block}\n\n"
            f"SOURCES:\n{_sources_block(sources)}"
        ),
        temperature=temperature,
        reasoning_effort=reasoning_effort,
    )

    facts: list[Fact] = []
    # LLM output order is the ranking (most-relevant first, per the system prompt).
    for i, f in enumerate(brief_llm.facts[: settings.research.max_facts], start=1):
        claim = f.claim
        if _normalize_url(f.source_url) not in known_urls:
            claim = UNVERIFIED_PREFIX + claim
        facts.append(
            Fact(
                fact_id=f"f{i}",
                claim=claim,
                source_url=f.source_url,
                source_date=f.source_date,
                followup_question=f.followup_question,
            )
        )

    return ResearchBrief(
        topic=topic,
        operator_angle_hint=angle_hint,
        research_summary=brief_llm.research_summary,
        facts=facts,
        topic_level_notes=brief_llm.topic_level_notes,
    )
