from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agents.prompting import BrandProfileData, GoalData
from app.db.seed import seed_defaults
from app.memory.store import clean_text
from app.models.agent import AgentGoal
from app.models.brand import BRAND_LIST_FIELDS, BRAND_TEXT_FIELDS, BrandProfile
from app.services.activity import ActivityLogger

ALL_BRAND_FIELDS = BRAND_TEXT_FIELDS + BRAND_LIST_FIELDS
X_VOICE_FIELDS = ("x_voice", "x_avoid")  # X-specific voice guidance (used by the X reply/post writer)


def get_brand(db: Session, agent_id: int) -> BrandProfile:
    brand = db.scalar(select(BrandProfile).where(BrandProfile.agent_id == agent_id))
    if brand is None:
        seed_defaults(db)
        brand = db.scalar(select(BrandProfile).where(BrandProfile.agent_id == agent_id))
    assert brand is not None
    return brand


def brand_data(brand: BrandProfile) -> BrandProfileData:
    return BrandProfileData(**{f: getattr(brand, f) for f in ALL_BRAND_FIELDS})


def brand_completeness(brand: BrandProfile) -> tuple[int, list[str]]:
    """Percentage of profile fields filled in, and the names of empty ones."""
    empty = [f for f in ALL_BRAND_FIELDS if not getattr(brand, f)]
    return round(100 * (len(ALL_BRAND_FIELDS) - len(empty)) / len(ALL_BRAND_FIELDS)), empty


def _clean_list(items: list[str]) -> list[str]:
    out: list[str] = []
    for item in items:
        value = clean_text(item)
        if value and value not in out:
            out.append(value)
    return out


def update_brand(db: Session, agent_id: int, changes: dict[str, Any], actor: str = "dashboard") -> BrandProfile:
    brand = get_brand(db, agent_id)
    changed: list[str] = []
    for key, value in changes.items():
        if key not in ALL_BRAND_FIELDS and key not in X_VOICE_FIELDS:
            continue
        value = _clean_list(value) if key in BRAND_LIST_FIELDS or key in X_VOICE_FIELDS else clean_text(value)
        if getattr(brand, key) != value:
            setattr(brand, key, value)
            changed.append(key)
    db.commit()
    if changed:
        labels = ", ".join(c.replace("_", " ") for c in changed)
        ActivityLogger(db, agent_id).log(
            "brand_profile_updated", f"Brand profile updated: {labels}", actor=actor, details={"fields": changed}
        )
    return brand


# --- goals --------------------------------------------------------------------


def list_goals(db: Session, agent_id: int, enabled_only: bool = False) -> list[AgentGoal]:
    stmt = select(AgentGoal).where(AgentGoal.agent_id == agent_id)
    if enabled_only:
        stmt = stmt.where(AgentGoal.enabled.is_(True))
    return list(db.scalars(stmt.order_by(AgentGoal.priority, AgentGoal.id)))


def goal_data(goals: list[AgentGoal]) -> list[GoalData]:
    return [GoalData(g.name, g.description, g.priority) for g in goals if g.enabled]


def create_goal(db: Session, agent_id: int, values: dict[str, Any], actor: str = "dashboard") -> AgentGoal:
    goal = AgentGoal(
        agent_id=agent_id,
        name=clean_text(values["name"]),
        description=clean_text(values.get("description") or ""),
        priority=values.get("priority", 3),
        enabled=values.get("enabled", True),
    )
    db.add(goal)
    db.commit()
    ActivityLogger(db, agent_id).log("goal_created", f'Added goal "{goal.name}" (priority {goal.priority})', actor=actor)
    return goal


def update_goal(db: Session, goal: AgentGoal, changes: dict[str, Any], actor: str = "dashboard") -> AgentGoal:
    for key, value in changes.items():
        if key in ("name", "description") and isinstance(value, str):
            value = clean_text(value)
        setattr(goal, key, value)
    db.commit()
    what = ", ".join(changes) or "nothing"
    state = "" if "enabled" not in changes else (" (enabled)" if goal.enabled else " (disabled)")
    ActivityLogger(db, goal.agent_id).log(
        "goal_updated", f'Goal "{goal.name}" changed: {what}{state}', actor=actor, details={"fields": list(changes)}
    )
    return goal


def delete_goal(db: Session, goal: AgentGoal, actor: str = "dashboard") -> None:
    name, agent_id = goal.name, goal.agent_id
    db.delete(goal)
    db.commit()
    ActivityLogger(db, agent_id).log("goal_deleted", f'Removed goal "{name}"', actor=actor)
