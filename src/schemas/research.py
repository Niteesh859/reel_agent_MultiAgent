"""PRD §5.1 — Researcher → Strategist research brief contract."""

from __future__ import annotations

from pydantic import BaseModel, Field


class Fact(BaseModel):
    fact_id: str
    claim: str
    source_name: str
    source_url: str
    source_date: str | None = None
    credibility_note: str
    relevance_rank: int = Field(ge=1)


class ResearchBrief(BaseModel):
    topic: str
    operator_angle_hint: str | None = None
    research_summary: str
    facts: list[Fact]
    topic_level_notes: str | None = None

    def fact_ids(self) -> set[str]:
        return {f.fact_id for f in self.facts}

    def get_fact(self, fact_id: str) -> Fact | None:
        return next((f for f in self.facts if f.fact_id == fact_id), None)
