from dataclasses import dataclass

from app.config.settings import Settings


@dataclass(frozen=True, slots=True)
class CommunityTuning:
    """Thresholds for community intelligence. Similarities are calibrated for nomic-embed-text."""

    topic_similarity: float = 0.75
    question_similarity: float = 0.80
    topic_min_messages: int = 3
    topic_min_participants: int = 2
    recurring_min_asks: int = 2
    topic_archive_days: int = 21

    @classmethod
    def from_settings(cls, s: Settings) -> "CommunityTuning":
        return cls(
            topic_similarity=s.community_topic_similarity,
            question_similarity=s.community_question_similarity,
            topic_min_messages=s.community_topic_min_messages,
            topic_min_participants=s.community_topic_min_participants,
            recurring_min_asks=s.community_recurring_min_asks,
        )
