"""Terminal Intelligence Engine, Phase 0: the one contract every analyser fills (local Ollama, Claude, or the rules fallback).

A model only EXTRACTS and LABELS. It never decides whether something is verified: the epistemic class of every claim is computed
by deterministic code from the sources (app.intel.classes). The same schema is used by the Phase 0 benchmark, the shared
semantic gate (shadow) and the PC worker's intel.analyze job, so their results are comparable.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

SCHEMA_VERSION = "intel-item-v1"

EventType = Literal[
    "market_move", "merger_acquisition", "earnings_results", "leadership_change", "legal_regulatory_action", "ipo_listing",
    "policy_government", "central_bank", "macro_data", "conflict_military", "attack_terror", "disaster_accident",
    "outage_incident", "hack_exploit", "sanctions_trade", "energy_supply_shipping", "crypto_protocol", "product_technology",
    "opinion_analysis_feature", "other",
]
ConsequenceKind = Literal[
    "deaths_injuries", "price_move", "money_amount", "deal", "supply_disruption", "policy_change", "legal_outcome",
    "leadership_change", "service_disruption", "escalation", "none",
]
Materiality = Literal["major", "significant", "notable", "noise"]
Audience = Literal["crypto", "markets", "world"]


class Claim(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str = Field(description="One assertion, as close to the source wording as possible")
    attributed_to: str = Field(description="Who the text says made the claim (outlet, official, company), or empty")
    hedged: bool = Field(description="True for could/may/reportedly/sources say/plans to/expected to/forecast")
    model_inference: bool = Field(description="True if this is YOUR conclusion (a cause, an implication), not stated in the text")


class Consequence(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: ConsequenceKind
    stated_in_text: bool = Field(description="True only if the text itself states the consequence")
    magnitude: str = Field(description="The figure as written in the text (e.g. '57%', '$41 billion', '6 killed'), or empty")


class ItemAnalysis(BaseModel):
    model_config = ConfigDict(extra="forbid")
    event_type: EventType
    entities: list[str] = Field(description="Up to 6 named companies, people, places, assets, institutions")
    consequence: Consequence
    materiality: Materiality = Field(description="major / significant / notable / noise, for a markets-and-world news audience")
    audiences: list[Audience]
    is_speculative: bool = Field(description="Nothing has happened yet: a forecast, plan, rumour, hedge or question")
    is_opinion_or_feature: bool = Field(description="Opinion, analysis, explainer, live blog, round-up, feature, listicle")
    claims: list[Claim] = Field(description="At most 4")
    reason: str = Field(description="One sentence: why this materiality")
