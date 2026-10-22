from fastapi import APIRouter, HTTPException, status

from app.api.deps import DbSession
from app.api.schemas import BrandOut, BrandProfileOut, BrandProfileUpdate, GoalIn, GoalOut, GoalUpdate
from app.models.agent import AgentGoal
from app.services.agent_service import get_agent_config
from app.services.brand_service import (
    brand_completeness,
    create_goal,
    delete_goal,
    get_brand,
    list_goals,
    update_brand,
    update_goal,
)

router = APIRouter(tags=["brand"])


def _brand_out(db: DbSession, agent_id: int) -> BrandOut:
    brand = get_brand(db, agent_id)
    completeness, empty = brand_completeness(brand)
    return BrandOut(profile=BrandProfileOut.model_validate(brand), completeness=completeness, empty_fields=empty)


@router.get("/brand", response_model=BrandOut)
def get_brand_profile(db: DbSession) -> BrandOut:
    return _brand_out(db, get_agent_config(db).id)


@router.patch("/brand", response_model=BrandOut)
def update_brand_profile(payload: BrandProfileUpdate, db: DbSession) -> BrandOut:
    agent_id = get_agent_config(db).id
    update_brand(db, agent_id, payload.model_dump(exclude_unset=True, exclude_none=True))
    return _brand_out(db, agent_id)


@router.get("/goals", response_model=list[GoalOut])
def get_goals(db: DbSession) -> list[GoalOut]:
    return [GoalOut.model_validate(g) for g in list_goals(db, get_agent_config(db).id)]


@router.post("/goals", response_model=GoalOut, status_code=status.HTTP_201_CREATED)
def add_goal(payload: GoalIn, db: DbSession) -> GoalOut:
    return GoalOut.model_validate(create_goal(db, get_agent_config(db).id, payload.model_dump()))


def _goal(db: DbSession, goal_id: int) -> AgentGoal:
    goal = db.get(AgentGoal, goal_id)
    if goal is None or goal.agent_id != get_agent_config(db).id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Goal not found")
    return goal


@router.patch("/goals/{goal_id}", response_model=GoalOut)
def edit_goal(goal_id: int, payload: GoalUpdate, db: DbSession) -> GoalOut:
    changes = payload.model_dump(exclude_unset=True, exclude_none=True)
    return GoalOut.model_validate(update_goal(db, _goal(db, goal_id), changes))


@router.delete("/goals/{goal_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_goal(goal_id: int, db: DbSession) -> None:
    delete_goal(db, _goal(db, goal_id))
