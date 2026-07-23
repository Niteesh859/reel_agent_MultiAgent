"""Agent 1 — Researcher (PRD §4).

Topic → broad, angle-agnostic facts. Even when the operator supplies an angle
hint, research goes broad first; the hint is carried through untouched.
Search backend: Tavily (basic depth = 1 credit/query).
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

_QUERY_SYSTEM = """You plan web research for a short-form explainer video (Instagram Reel).
Given a topic, produce diverse, angle-agnostic search queries that together cover:
core facts and definitions, surprising statistics or records, recent developments,
common misconceptions, and notable expert findings or studies.
Stay BROAD: do not narrow toward any single story angle, even if a hint is provided —
the hint only tells you one area that must not be missed, not the only area to cover."""

_BRIEF_SYSTEM = """You are the Researcher in a pipeline that turns topics into short
Instagram Reels. From the web sources provided, extract the facts a content strategist
could build a 30-60 second energetic explainer from.

Rules:
- Use ONLY the numbered sources provided. Every fact's source_url must be copied
  exactly from one of them. One credible source per claim is acceptable.
- claim: your own concise restatement of the finding (1-2 sentences, concrete,
  self-contained). Prefer surprising, visual, specific facts over generic ones.
- source_name: the publication/site name; source_date: publication date if visible
  in the source text, else null.
- credibility_note: your honest judgment of how much to trust this source
  (e.g. "peer-reviewed journal", "major outlet citing a study", "enthusiast blog").
- relevance_rank: 1 = most relevant to the topic; each fact gets a distinct rank.
- research_summary: 2-3 sentences synthesizing what you found overall.
- topic_level_notes: observations about the topic as a whole that don't attach to a
  single fact (or null).
- Stay angle-agnostic: gather what the material supports, do not pitch a story."""


class _QueryPlan(BaseModel):
    queries: list[str] = Field(min_length=2, max_length=10)


class _FactLLM(BaseModel):
    claim: str
    source_name: str
    source_url: str
    source_date: str | None = None
    credibility_note: str
    relevance_rank: int = Field(ge=1)


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
        },
    )
    response.raise_for_status()
    return response.json().get("results", [])


@traceable(name="tavily-search", run_type="tool")
async def _gather_sources(settings: Settings, queries: list[str]) -> list[dict]:
    async with httpx.AsyncClient(timeout=30.0) as http:
        batches = await asyncio.gather(
            *(_tavily_search(http, settings, q) for q in queries),
            return_exceptions=True,
        )
    sources: dict[str, dict] = {}
    errors: list[BaseException] = []
    for batch in batches:
        if isinstance(batch, BaseException):
            errors.append(batch)
            continue
        for item in batch:
            url = item.get("url") or ""
            if not url or url in sources:
                continue
            sources[url] = {
                "title": item.get("title") or "(untitled)",
                "url": url,
                "content": (item.get("content") or "")[:1500],
                "published_date": item.get("published_date"),
                "score": item.get("score") or 0.0,
            }
    if not sources:
        detail = f" (last error: {errors[-1]})" if errors else ""
        raise RuntimeError(f"web search returned no usable sources{detail}")
    ranked = sorted(sources.values(), key=lambda s: s["score"], reverse=True)
    return ranked[:20]


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

    note_block = (
        f"\n\nOPERATOR REGENERATION NOTE — the previous research was rejected; "
        f"you must address this: {operator_note}"
        if operator_note
        else ""
    )
    plan = await complete_json(
        client,
        settings,
        model=model,
        schema=_QueryPlan,
        system=_QUERY_SYSTEM,
        user=(
            f"Topic: {topic}\n"
            f"Operator angle hint (context only, stay broad): {angle_hint or 'none'}\n"
            f"Produce up to {settings.research.max_search_queries} search queries."
            f"{note_block}"
        ),
        temperature=temperature,
    )
    queries = [topic, *plan.queries]
    seen: set[str] = set()
    queries = [q for q in queries if not (q.lower() in seen or seen.add(q.lower()))]
    queries = queries[: settings.research.max_search_queries]

    sources = await _gather_sources(settings, queries)
    known_urls = {_normalize_url(s["url"]) for s in sources}

    def _check(brief: _BriefLLM) -> None:
        ranks = [f.relevance_rank for f in brief.facts]
        if len(set(ranks)) != len(ranks):
            raise ValueError("relevance_rank values must be distinct across facts")

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
        post_validate=_check,
    )

    facts: list[Fact] = []
    ordered = sorted(brief_llm.facts, key=lambda f: f.relevance_rank)
    for i, f in enumerate(ordered[: settings.research.max_facts], start=1):
        credibility = f.credibility_note
        if _normalize_url(f.source_url) not in known_urls:
            credibility = UNVERIFIED_PREFIX + credibility
        facts.append(
            Fact(
                fact_id=f"f{i}",
                claim=f.claim,
                source_name=f.source_name,
                source_url=f.source_url,
                source_date=f.source_date,
                credibility_note=credibility,
                relevance_rank=i,
            )
        )

    return ResearchBrief(
        topic=topic,
        operator_angle_hint=angle_hint,
        research_summary=brief_llm.research_summary,
        facts=facts,
        topic_level_notes=brief_llm.topic_level_notes,
    )
