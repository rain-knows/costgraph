from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from langgraph.checkpoint.postgres import PostgresSaver
from psycopg import Connection
from psycopg.rows import dict_row

from app.settings import AppSettings, get_settings


@contextmanager
def open_postgres_checkpointer(
    settings: AppSettings | None = None,
) -> Iterator[PostgresSaver]:
    """Open a saver bound to the Alembic-managed checkpoint schema."""

    configured = settings or get_settings()
    with Connection.connect(
        configured.psycopg_database_url,
        autocommit=True,
        prepare_threshold=0,
        row_factory=dict_row,
        options="-c search_path=agent_checkpoint,public",
    ) as connection:
        yield PostgresSaver(connection)
