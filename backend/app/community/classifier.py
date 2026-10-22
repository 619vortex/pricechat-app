"""Message classification: a fast routed model, with a rule-based fallback.

The fallback keeps the pipeline moving if the model is unavailable; messages
classified that way are marked so they can be re-classified later.
"""

import logging
import re
from dataclasses import dataclass
from typing import Literal, Protocol

from pydantic import BaseModel, Field

from app.ai.routing import ModelRouter
from app.ai.structured import complete_structured
from app.community.formatting import format_messages
from app.models.enums import MessageCategory
from app.models.telegram import TelegramMessage

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class Classification:
    category: MessageCategory
    is_question: bool


class MessageClassifier(Protocol):
    name: str

    async def classify(self, messages: list[TelegramMessage]) -> dict[int, Classification]: ...


_QUESTION_START = re.compile(
    r"^(who|what|when|where|why|how|which|is|are|can|could|does|do|did|will|would|should|any(one|body)?|has|have)\b", re.I
)
_NOISE = re.compile(r"^(gm|gn|lol|lmao|ok|okay|thanks|thank you|ty|nice|wow|yes|no|\W+|\d+)[\s!.?]*$", re.I)
_CONCERN = re.compile(r"\b(scam|worried|concern|problem|issue|bug|broken|not working|lost|hack|fake|refund|down)\b", re.I)
_FEEDBACK = re.compile(r"\b(love|like|hate|wish|should add|feature|suggest|improve|better if|ui|app)\b", re.I)
_IDEA = re.compile(r"\b(idea|what if|we could|how about|maybe we)\b", re.I)


def looks_like_question(text: str) -> bool:
    t = text.strip()
    return len(t) >= 8 and (t.endswith("?") or bool(_QUESTION_START.match(t)) and len(t.split()) >= 3)


class HeuristicClassifier:
    name = "rules"

    async def classify(self, messages: list[TelegramMessage]) -> dict[int, Classification]:
        return {m.id: self.one(m) for m in messages}

    @staticmethod
    def one(m: TelegramMessage) -> Classification:
        text = m.text.strip()
        if m.author_is_bot or not text or _NOISE.match(text) or len(text) < 4:
            return Classification(MessageCategory.NOISE, False)
        is_q = looks_like_question(text)
        if is_q:
            category = MessageCategory.QUESTION
        elif _CONCERN.search(text):
            category = MessageCategory.CONCERN
        elif _IDEA.search(text):
            category = MessageCategory.IDEA
        elif _FEEDBACK.search(text):
            category = MessageCategory.FEEDBACK
        elif len(text.split()) <= 3:
            category = MessageCategory.CHATTER
        else:
            category = MessageCategory.INFORMATION
        return Classification(category, is_q)


class _Item(BaseModel):
    id: str = Field(description="message id, e.g. m123")
    category: Literal["question", "feedback", "concern", "idea", "information", "announcement", "chatter", "noise"]
    is_question: bool = Field(description="true only if the member is genuinely asking something")


class _Batch(BaseModel):
    items: list[_Item]


SYSTEM = (
    "You label messages from an online community chat. For every message return its id, one category and "
    "is_question.\nCategories: question (asks something), feedback (opinion about the product/brand), "
    "concern (worry, complaint, problem), idea (suggestion), information (shares facts/news/analysis), "
    "announcement (official-sounding notice), chatter (social small talk), noise (greetings, emojis, spam, bots).\n"
    "Rhetorical questions and jokes are not questions. Return JSON only."
)


class LLMClassifier:
    """Labels messages in batches with the 'telegram.classify' task (fast tier by default)."""

    def __init__(self, router: ModelRouter, batch_size: int = 25) -> None:
        self.router = router
        self.batch_size = batch_size
        self.name = "model"

    async def classify(self, messages: list[TelegramMessage]) -> dict[int, Classification]:
        out: dict[int, Classification] = {}
        for start in range(0, len(messages), self.batch_size):
            batch = messages[start : start + self.batch_size]
            text, included = format_messages(batch, char_budget=10000, max_chars_each=400)
            parsed, _, route = await complete_structured(
                self.router, "telegram.classify", SYSTEM, f"Messages:\n{text}", _Batch
            )
            self.name = f"model:{route.model}"
            allowed = set(included)
            for item in parsed.items:
                try:
                    mid = int(item.id.lstrip("mM"))
                except ValueError:
                    continue
                if mid in allowed:
                    out[mid] = Classification(MessageCategory(item.category), item.is_question)
            # Anything the model skipped gets the rule-based label.
            for m in batch:
                out.setdefault(m.id, HeuristicClassifier.one(m))
        return out
