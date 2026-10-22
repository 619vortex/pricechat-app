"""Conversation memory: recent turns of a conversation, persisted in conversation_messages.

Persisting (rather than holding turns in process memory) means conversations
survive restarts, every exchange is logged, and multiple workers share state.
"""

from dataclasses import dataclass
from typing import Protocol

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.conversation import ConversationMessage
from app.models.enums import AgentTask, PromptPlatform
from app.providers.base import ChatMessage, ChatResult

HISTORY_CHAR_BUDGET = 16_000  # ~4k tokens of earlier turns sent with each request


@dataclass(frozen=True, slots=True)
class TurnContext:
    task: AgentTask = AgentTask.CHAT
    platform: PromptPlatform = PromptPlatform.DASHBOARD


class ConversationMemory(Protocol):
    conversation_id: str

    def recent(self) -> list[ChatMessage]: ...

    def record_exchange(
        self, user_text: str, result: ChatResult, context: TurnContext, memory_ids: list[int]
    ) -> tuple[int, int]: ...


class SqlConversationMemory:
    def __init__(
        self, db: Session, agent_id: int, conversation_id: str, window: int = 20, char_budget: int = HISTORY_CHAR_BUDGET
    ) -> None:
        self.db = db
        self.agent_id = agent_id
        self.conversation_id = conversation_id
        self.window = window
        self.char_budget = char_budget

    def recent(self) -> list[ChatMessage]:
        stmt = (
            select(ConversationMessage.role, ConversationMessage.content)
            .where(
                ConversationMessage.agent_id == self.agent_id,
                ConversationMessage.conversation_id == self.conversation_id,
            )
            .order_by(ConversationMessage.id.desc())
            .limit(self.window)
        )
        rows = list(self.db.execute(stmt).all())[::-1]
        # Messages can be 8,000 chars each: keep the newest that fit the budget, not 20 of any size, on every turn.
        total = sum(len(content) for _, content in rows)
        while rows and total > self.char_budget:
            total -= len(rows.pop(0)[1])
        # Never start the history with a dangling assistant turn.
        while rows and rows[0][0] != "user":
            rows.pop(0)
        return [ChatMessage(role, content) for role, content in rows]  # type: ignore[arg-type]

    def record_exchange(
        self, user_text: str, result: ChatResult, context: TurnContext, memory_ids: list[int]
    ) -> tuple[int, int]:
        user = ConversationMessage(
            agent_id=self.agent_id,
            conversation_id=self.conversation_id,
            role="user",
            content=user_text,
            task=context.task,
            platform=context.platform,
        )
        assistant = ConversationMessage(
            agent_id=self.agent_id,
            conversation_id=self.conversation_id,
            role="assistant",
            content=result.content,
            task=context.task,
            platform=context.platform,
            provider=result.provider,
            model=result.model,
            latency_ms=result.latency_ms,
            prompt_tokens=result.prompt_tokens,
            completion_tokens=result.completion_tokens,
            memory_ids=memory_ids,
            extra={"tokens_per_second": result.tokens_per_second} if result.tokens_per_second else {},
        )
        self.db.add_all([user, assistant])
        self.db.commit()
        return user.id, assistant.id


def conversation_history(db: Session, agent_id: int, conversation_id: str, limit: int = 100) -> list[ConversationMessage]:
    stmt = (
        select(ConversationMessage)
        .where(ConversationMessage.agent_id == agent_id, ConversationMessage.conversation_id == conversation_id)
        .order_by(ConversationMessage.id.desc())
        .limit(limit)
    )
    return list(db.scalars(stmt))[::-1]


def conversation_count(db: Session, agent_id: int) -> int:
    stmt = select(func.count(func.distinct(ConversationMessage.conversation_id))).where(
        ConversationMessage.agent_id == agent_id
    )
    return int(db.scalar(stmt) or 0)
