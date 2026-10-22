from enum import StrEnum

from sqlalchemy import Enum


def str_enum(enum_cls: type[StrEnum]) -> Enum:
    """Store enums as VARCHAR: portable across SQLite/PostgreSQL with no native enum migrations."""
    return Enum(
        enum_cls,
        native_enum=False,
        create_constraint=False,
        length=32,
        values_callable=lambda e: [m.value for m in e],
        validate_strings=True,
    )
