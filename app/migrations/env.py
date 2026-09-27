"""Alembic environment for xenotag's ``state.db`` (roadmap I3).

Never run from a root ``alembic.ini``: the image ships only ``app/``, so the
config is built in code by ``app.migrate`` and the caller hands over an open
connection that is already inside its ``BEGIN IMMEDIATE`` transaction. Alembic
sees that external transaction and runs every step inside it, so an upgrade
commits or rolls back as one unit.
"""

from alembic import context

from app.state import Base

config = context.config
connection = config.attributes.get("connection")
if connection is None:
    raise RuntimeError("app/migrations is driven by app.migrate; run `python -m app.migrate --help`")

context.configure(
    connection=connection,
    # A test can plant a drifted model here to prove `check` notices it.
    target_metadata=config.attributes.get("target_metadata", Base.metadata),
    # SQLite cannot ALTER most things in place; batch mode rebuilds the table.
    render_as_batch=True,
    compare_type=True,
)

with context.begin_transaction():
    context.run_migrations()
