"""Create missing default rows from the seed JSON. Never overwrites existing data."""

import json
import logging
from functools import lru_cache
from pathlib import Path
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config.settings import get_settings
from app.models.agent import AgentConfig, AgentGoal
from app.models.brand import BRAND_LIST_FIELDS, BRAND_TEXT_FIELDS, BrandProfile
from app.models.enums import AgentStatus
from app.services.safety import get_or_create_policy

logger = logging.getLogger(__name__)

DEFAULT_AGENT_SLUG = "price-chat"


@lru_cache
def load_seed(path: Path | None = None) -> dict[str, Any]:
    seed_path = path or get_settings().seed_file
    return json.loads(Path(seed_path).read_text(encoding="utf-8"))


def seed_defaults(db: Session) -> AgentConfig:
    seed = load_seed()
    agent = db.scalar(select(AgentConfig).where(AgentConfig.slug == DEFAULT_AGENT_SLUG))
    created = agent is None

    if agent is None:
        agent_seed = seed["agent"]
        agent = AgentConfig(
            slug=DEFAULT_AGENT_SLUG,
            name=agent_seed["name"],
            identity=agent_seed["identity"],
            behavior_rules=list(agent_seed["behavior_rules"]),
            status=AgentStatus.ACTIVE,
        )
        db.add(agent)
        db.flush()
        logger.info("Seeded default agent")
    elif not agent.behavior_rules:
        # Behaviour rules are new in Phase 2; upgraded databases start with none.
        agent.behavior_rules = list(seed["agent"]["behavior_rules"])

    if db.scalar(select(BrandProfile).where(BrandProfile.agent_id == agent.id)) is None:
        brand_seed = seed["brand"]
        db.add(
            BrandProfile(
                agent_id=agent.id,
                **{f: brand_seed.get(f, "") for f in BRAND_TEXT_FIELDS},
                **{f: list(brand_seed.get(f, [])) for f in BRAND_LIST_FIELDS},
            )
        )
        logger.info("Seeded brand profile")

    goal_count = db.scalar(select(func.count()).select_from(AgentGoal).where(AgentGoal.agent_id == agent.id))
    if created and not goal_count:
        for goal in seed["goals"]:
            db.add(AgentGoal(agent_id=agent.id, enabled=True, **goal))
        logger.info("Seeded %d goals", len(seed["goals"]))

    db.commit()
    get_or_create_policy(db, agent.id)
    return agent
