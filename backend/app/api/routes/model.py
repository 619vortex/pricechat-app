from dataclasses import asdict

from fastapi import APIRouter, HTTPException, status

from app.ai.routing import TASKS, ModelTier, list_routes, set_route
from app.api.community_schemas import RouteOut, RouteUpdate, RoutingOut
from app.api.deps import DbSession, Runtime
from app.services.activity import ActivityLogger
from app.api.schemas import ModelConfigUpdate, ModelStatusOut, ModelTestOut
from app.services.agent_service import get_agent_config, update_agent_config
from app.services.model_service import model_status, test_model

router = APIRouter(prefix="/model", tags=["model"])


@router.get("", response_model=ModelStatusOut)
async def get_model(db: DbSession, runtime: Runtime) -> ModelStatusOut:
    return ModelStatusOut(**asdict(await model_status(db, runtime)))


@router.patch("", response_model=ModelStatusOut)
async def update_model(payload: ModelConfigUpdate, db: DbSession, runtime: Runtime) -> ModelStatusOut:
    changes = payload.model_dump(exclude_unset=True)
    if "model_name" in changes and not changes["model_name"]:
        changes["model_name"] = None  # back to the .env default
    update_agent_config(db, get_agent_config(db), changes)
    runtime.invalidate_health()
    return ModelStatusOut(**asdict(await model_status(db, runtime)))


@router.post("/test", response_model=ModelTestOut)
async def run_model_test(db: DbSession, runtime: Runtime) -> ModelTestOut:
    result = await test_model(db, runtime)
    return ModelTestOut(
        ok=True,
        reply=result.content,
        model=result.model,
        provider=result.provider,
        latency_ms=result.latency_ms,
        prompt_tokens=result.prompt_tokens,
        completion_tokens=result.completion_tokens,
        tokens_per_second=result.tokens_per_second,
    )


# --- task → model routing ---------------------------------------------------------------


def _routing_out(db: DbSession, runtime: Runtime, installed: list[str]) -> RoutingOut:
    router_ = runtime.router(db)
    return RoutingOut(
        tiers={tier.value: router_.tier_model(tier) for tier in ModelTier},
        routes=[
            RouteOut(
                task=spec.key,
                label=spec.label,
                description=spec.description,
                default_tier=spec.tier.value,
                tier=decision.tier.value if decision.tier else None,
                model=decision.model,
                temperature=decision.temperature,
                customised=decision.customised,
            )
            for spec, decision in list_routes(router_)
        ],
        installed_models=installed,
    )


@router.get("/routes", response_model=RoutingOut)
async def get_routes(db: DbSession, runtime: Runtime) -> RoutingOut:
    health = await runtime.llm_health()
    return _routing_out(db, runtime, health.models)


@router.put("/routes/{task}", response_model=RoutingOut)
async def put_route(task: str, payload: RouteUpdate, db: DbSession, runtime: Runtime) -> RoutingOut:
    """Point a task at a tier or a specific model. An empty body resets it to the default."""
    if task not in TASKS:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Unknown task")
    set_route(db, task, payload.tier, payload.model, payload.temperature)
    target = payload.model or (f"the {payload.tier} tier" if payload.tier else "its default")
    ActivityLogger(db).log("model_route_updated", f"'{TASKS[task].label}' now uses {target}", actor="dashboard", details={"task": task})
    health = await runtime.llm_health()
    return _routing_out(db, runtime, health.models)
