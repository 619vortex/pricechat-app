"""Output schemas of the committee's language-model roles (the TradingAgents v0.7.0 structured-output pattern: the decision
makers answer in typed JSON whose field descriptions are the instructions). Every field is short text for Telegram.

There are deliberately NO price fields: the trader and the portfolio manager choose a direction, and the plan's numbers are
copied from the evidence pack's own scenarios by code (roles.plan_from). A model cannot type a price into the run. Long
lists and long strings are cut, not trusted.
"""

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

Lean = Literal["bullish", "bearish", "neutral", "unknown"]
Confidence = Literal["low", "medium", "high"]
Rating = Literal["Buy", "Overweight", "Hold", "Underweight", "Sell"]
Action = Literal["LONG", "SHORT", "WAIT"]
Stance = Literal["support", "reduce", "reject"]
RATINGS: tuple[str, ...] = ("Buy", "Overweight", "Hold", "Underweight", "Sell")

MAX_TEXT = 400
MAX_ITEMS = 4


def _cut(v: Any) -> Any:
    if isinstance(v, str):
        return v.strip()[:MAX_TEXT]
    if isinstance(v, list):
        return [_cut(x) for x in v[:MAX_ITEMS]]
    return v


class _Out(BaseModel):
    @field_validator("*", mode="before")
    @classmethod
    def _short(cls, v: Any) -> Any:
        return _cut(v)


class Point(_Out):
    text: str = Field(description="One short sentence, numbers copied exactly from the evidence.")
    refs: list[str] = Field(default_factory=list, description='Evidence ref ids that support it, e.g. ["T1"].')


class AnalystOut(_Out):
    lean: Lean = Field(description="bullish / bearish / neutral, or unknown when the section is unavailable.")
    confidence: Confidence = Field(description="How strong and complete this section's evidence is (not a probability).")
    summary: str = Field(description="One or two short sentences.")
    points: list[Point] = Field(default_factory=list, description="Up to 4 evidence points with ref ids.")
    limitations: list[str] = Field(default_factory=list, description="What is missing or noisy in this section.")


class DebaterOut(_Out):
    thesis: str = Field(description="The case in one or two short sentences.")
    points: list[Point] = Field(default_factory=list, description="Up to 4 evidence points with ref ids.")


class RebuttalsOut(_Out):
    bull: str = Field(description="The bull's one-sentence answer to the bear's strongest point.")
    bear: str = Field(description="The bear's one-sentence answer to the bull's strongest point.")


class ManagerOut(_Out):
    rating: Rating = Field(description="Exactly one of Buy / Overweight / Hold / Underweight / Sell. Hold when the evidence is "
                                       "balanced or too thin; conflict alone is not a reason to Hold.")
    summary: str = Field(description="Which side's evidence carried it, in one or two short sentences.")


class TraderOut(_Out):
    action: Action = Field(description="LONG or SHORT only when the matching evidence scenario exists; otherwise WAIT.")
    rationale: str = Field(description="Why, in one or two short sentences, citing the scenario's own rule text.")


class RiskVoice(_Out):
    stance: Stance = Field(description="support / reduce / reject the trader's proposal.")
    concerns: list[str] = Field(default_factory=list, description="Up to 3 short concerns.")


class RiskPanelOut(_Out):
    aggressive: RiskVoice
    neutral: RiskVoice
    conservative: RiskVoice
    summary: str = Field(description="One sentence: where the three agree and disagree.")


class PortfolioOut(_Out):
    action: Action = Field(description="Final LONG / SHORT / WAIT. LONG or SHORT only when that evidence scenario exists.")
    rating: Rating = Field(description="Exactly one of Buy / Overweight / Hold / Underweight / Sell.")
    summary: str = Field(description="The decision in two short sentences.")
    what_changes_view: list[str] = Field(default_factory=list,
                                         description="Up to 3 observable conditions (from the evidence rules) that would change it.")
