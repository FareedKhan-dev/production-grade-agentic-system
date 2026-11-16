"""schema.sql documents the application's tables and must match the models that create them."""

from pathlib import Path

from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import (
    CreateIndex,
    CreateTable,
)
from sqlmodel import SQLModel

# The models the database service imports, and so the tables the application creates
import src.data.models.session  # noqa: F401
import src.data.models.user  # noqa: F401

ROOT = Path(__file__).resolve().parents[1]

HEADER = """-- Database schema for the application, as PostgreSQL DDL.
-- The SQLModel classes in src/data/models create these tables at startup, and
-- tests/test_schema.py checks that this file matches them. LangGraph's checkpointer
-- and mem0 create their own tables the first time the agent runs.
"""


def _normalized(sql: str) -> str:
    return "\n".join(line.rstrip() for line in sql.replace("\r\n", "\n").strip().splitlines())


def render_schema() -> str:
    dialect = postgresql.dialect()
    statements = []
    for table in SQLModel.metadata.sorted_tables:
        statements.append(str(CreateTable(table).compile(dialect=dialect)).strip() + ";")
        for index in sorted(table.indexes, key=lambda index: index.name):
            statements.append(str(CreateIndex(index).compile(dialect=dialect)).strip() + ";")
    return _normalized(HEADER + "\n" + "\n\n".join(statements)) + "\n"


def test_schema_sql_matches_the_models():
    expected = render_schema()
    actual = _normalized((ROOT / "schema.sql").read_text(encoding="utf-8")) + "\n"
    assert actual == expected, "schema.sql is out of date. It should read:\n" + expected
