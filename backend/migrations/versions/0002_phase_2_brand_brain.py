"""phase 2 brand brain

Brand profile and goals move from JSON columns on agent_configs into their own
tables (existing values are copied). Memories gain Phase 2 metadata, plus new
memory_embeddings and conversation_messages tables.

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-03
"""

from collections.abc import Sequence
from datetime import datetime, timezone

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _enum(name: str, *values: str) -> sa.Enum:
    return sa.Enum(*values, name=name, native_enum=False, length=32, create_constraint=False)


def _timestamps() -> list[sa.Column]:
    return [
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    ]


# Phase 1 memory kinds -> (Phase 2 kind, layer)
_KIND_MAP = {
    "fact": ("note", "brand"),
    "brand": ("brand_fact", "brand"),
    "insight": ("community_insight", "community"),
    "conversation": ("note", "conversation"),
}


def upgrade() -> None:
    op.create_table(
        "agent_goals",
        sa.Column("agent_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(["agent_id"], ["agent_configs.id"], name=op.f("fk_agent_goals_agent_id_agent_configs"), ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_agent_goals")),
    )
    op.create_index(op.f("ix_agent_goals_agent_id"), "agent_goals", ["agent_id"])

    list_cols = ["products_services", "topics_of_interest", "topics_to_avoid", "terminology",
                 "allowed_claims", "restricted_claims", "social_objectives"]
    op.create_table(
        "brand_profiles",
        sa.Column("agent_id", sa.Integer(), nullable=False),
        sa.Column("brand_name", sa.String(length=128), nullable=False),
        *[sa.Column(c, sa.Text(), nullable=False) for c in
          ["description", "mission", "audience", "tone", "writing_style", "personality"]],
        *[sa.Column(c, sa.JSON(), nullable=False) for c in list_cols],
        *_timestamps(),
        sa.ForeignKeyConstraint(["agent_id"], ["agent_configs.id"], name=op.f("fk_brand_profiles_agent_id_agent_configs"), ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_brand_profiles")),
        sa.UniqueConstraint("agent_id", name=op.f("uq_brand_profiles_agent_id")),
    )

    op.create_table(
        "conversation_messages",
        sa.Column("agent_id", sa.Integer(), nullable=False),
        sa.Column("conversation_id", sa.String(length=64), nullable=False),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("task", _enum("agenttask", "chat", "draft_post", "draft_reply", "summarize", "model_test"), nullable=False),
        sa.Column("platform", _enum("promptplatform", "dashboard", "x", "telegram"), nullable=False),
        sa.Column("provider", sa.String(length=64), nullable=True),
        sa.Column("model", sa.String(length=128), nullable=True),
        sa.Column("latency_ms", sa.Float(), nullable=True),
        sa.Column("prompt_tokens", sa.Integer(), nullable=True),
        sa.Column("completion_tokens", sa.Integer(), nullable=True),
        sa.Column("memory_ids", sa.JSON(), nullable=False),
        sa.Column("extra", sa.JSON(), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(["agent_id"], ["agent_configs.id"], name=op.f("fk_conversation_messages_agent_id_agent_configs"), ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_conversation_messages")),
    )
    op.create_index(op.f("ix_conversation_messages_agent_id"), "conversation_messages", ["agent_id"])
    op.create_index(op.f("ix_conversation_messages_conversation_id"), "conversation_messages", ["conversation_id"])

    op.create_table(
        "memory_embeddings",
        sa.Column("memory_id", sa.Integer(), nullable=False),
        sa.Column("model", sa.String(length=128), nullable=False),
        sa.Column("dimensions", sa.Integer(), nullable=False),
        sa.Column("vector", sa.LargeBinary(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(["memory_id"], ["memories.id"], name=op.f("fk_memory_embeddings_memory_id_memories"), ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_memory_embeddings")),
        sa.UniqueConstraint("memory_id", name=op.f("uq_memory_embeddings_memory_id")),
    )
    op.create_index(op.f("ix_memory_embeddings_model"), "memory_embeddings", ["model"])

    # --- copy Phase 1 brand/goals JSON into the new tables -------------------------
    bind = op.get_bind()
    now = datetime.now(timezone.utc)
    old_agents = sa.table(
        "agent_configs",
        sa.column("id", sa.Integer), sa.column("name", sa.String),
        sa.column("brand_profile", sa.JSON), sa.column("goals", sa.JSON),
    )
    goals_t = sa.table(
        "agent_goals",
        sa.column("agent_id", sa.Integer), sa.column("name", sa.String), sa.column("description", sa.Text),
        sa.column("priority", sa.Integer), sa.column("enabled", sa.Boolean),
        sa.column("created_at", sa.DateTime), sa.column("updated_at", sa.DateTime),
    )
    brand_t = sa.table(
        "brand_profiles",
        *[sa.column(c, sa.Text) for c in
          ["brand_name", "description", "mission", "audience", "tone", "writing_style", "personality"]],
        *[sa.column(c, sa.JSON) for c in list_cols],
        sa.column("agent_id", sa.Integer), sa.column("created_at", sa.DateTime), sa.column("updated_at", sa.DateTime),
    )

    def as_list(value: object) -> list[str]:
        if isinstance(value, list):
            return [str(v) for v in value if str(v).strip()]
        if isinstance(value, str) and value.strip():
            return [p.strip() for p in value.split(",") if p.strip()]
        return []

    for agent in bind.execute(sa.select(old_agents)).mappings().all():
        brand = agent["brand_profile"] or {}
        bind.execute(brand_t.insert().values(
            agent_id=agent["id"],
            brand_name=str(brand.get("brand_name") or agent["name"]),
            description="", mission="",
            audience=str(brand.get("audience") or ""),
            tone=str(brand.get("voice") or ""),
            writing_style="", personality="",
            products_services=[], terminology=[], allowed_claims=[], social_objectives=[],
            topics_of_interest=as_list(brand.get("topics")),
            topics_to_avoid=as_list(brand.get("avoid")),
            restricted_claims=[],
            created_at=now, updated_at=now,
        ))
        for goal in agent["goals"] or []:
            bind.execute(goals_t.insert().values(
                agent_id=agent["id"], name=str(goal)[:160], description="", priority=3, enabled=True,
                created_at=now, updated_at=now,
            ))

    with op.batch_alter_table("agent_configs") as batch_op:
        batch_op.add_column(sa.Column("behavior_rules", sa.JSON(), nullable=False, server_default="[]"))
        batch_op.drop_column("brand_profile")
        batch_op.drop_column("goals")

    with op.batch_alter_table("memories") as batch_op:
        batch_op.add_column(sa.Column("layer", _enum("memorylayer", "conversation", "brand", "community", "social"), nullable=False, server_default="brand"))
        batch_op.add_column(sa.Column("title", sa.String(length=256), nullable=True))
        batch_op.add_column(sa.Column("tags", sa.JSON(), nullable=False, server_default="[]"))
        batch_op.add_column(sa.Column("source_type", _enum("sourcetype", "manual", "chat", "agent", "telegram", "x", "document", "url", "system"), nullable=False, server_default="manual"))
        batch_op.add_column(sa.Column("external_id", sa.String(length=128), nullable=True))
        batch_op.add_column(sa.Column("platform", _enum("platform", "x", "telegram"), nullable=True))
        batch_op.add_column(sa.Column("author", sa.String(length=128), nullable=True))
        batch_op.add_column(sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=True))
        batch_op.alter_column("source", existing_type=sa.String(length=64), type_=sa.String(length=256), existing_nullable=True)
        batch_op.create_index(batch_op.f("ix_memories_layer"), ["layer"])
        batch_op.create_index(batch_op.f("ix_memories_source_type"), ["source_type"])
        batch_op.create_unique_constraint(batch_op.f("uq_memories_agent_id"), ["agent_id", "source_type", "external_id"])

    memories = sa.table("memories", sa.column("kind", sa.String), sa.column("layer", sa.String))
    for old, (kind, layer) in _KIND_MAP.items():
        bind.execute(memories.update().where(memories.c.kind == old).values(kind=kind, layer=layer))


def downgrade() -> None:
    with op.batch_alter_table("memories") as batch_op:
        batch_op.drop_constraint(batch_op.f("uq_memories_agent_id"), type_="unique")
        batch_op.drop_index(batch_op.f("ix_memories_source_type"))
        batch_op.drop_index(batch_op.f("ix_memories_layer"))
        batch_op.alter_column("source", existing_type=sa.String(length=256), type_=sa.String(length=64), existing_nullable=True)
        for col in ("occurred_at", "author", "platform", "external_id", "source_type", "tags", "title", "layer"):
            batch_op.drop_column(col)

    with op.batch_alter_table("agent_configs") as batch_op:
        batch_op.add_column(sa.Column("goals", sa.JSON(), nullable=False, server_default="[]"))
        batch_op.add_column(sa.Column("brand_profile", sa.JSON(), nullable=False, server_default="{}"))
        batch_op.drop_column("behavior_rules")

    op.drop_index(op.f("ix_memory_embeddings_model"), table_name="memory_embeddings")
    op.drop_table("memory_embeddings")
    op.drop_index(op.f("ix_conversation_messages_conversation_id"), table_name="conversation_messages")
    op.drop_index(op.f("ix_conversation_messages_agent_id"), table_name="conversation_messages")
    op.drop_table("conversation_messages")
    op.drop_table("brand_profiles")
    op.drop_index(op.f("ix_agent_goals_agent_id"), table_name="agent_goals")
    op.drop_table("agent_goals")
