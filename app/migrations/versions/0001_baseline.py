"""Baseline: the schema every pre-I3 release built (create_all + _migrate_schema).

Revision ID: 0001
Revises:
Create Date: 2026-09-26

Recorded exactly as production had it on 2026-09-26: four tables and one explicit
index, including the five ``media_state`` columns the old unversioned
``_migrate_schema()`` added by ``ALTER TABLE``.

This runs only on an EMPTY database. A database an older release already built
is never upgraded through here: ``app.migrate`` compares it with this revision's
schema and stamps it on an exact match. Run against such a database anyway,
the first ``create_table`` fails inside the one migration transaction, so
nothing is half-applied.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "media_state",
        sa.Column("item_id", sa.String(), nullable=False),
        sa.Column("source", sa.String(), nullable=False),
        sa.Column("file_path", sa.Text(), nullable=True),
        sa.Column("resolution", sa.String(), nullable=True),
        sa.Column("languages", sa.Text(), nullable=True),
        sa.Column("tags_applied", sa.Text(), nullable=True),
        sa.Column("image_path", sa.Text(), nullable=True),
        sa.Column("last_scanned", sa.DateTime(), nullable=True),
        sa.Column("file_mtime", sa.Float(), nullable=True),
        sa.Column("video_codec", sa.String(), nullable=True),
        sa.Column("hdr_type", sa.String(), nullable=True),
        sa.Column("audio_tracks", sa.Text(), nullable=True),
        sa.Column("subtitle_tracks", sa.Text(), nullable=True),
        sa.Column("content_rating", sa.String(), nullable=True),
        sa.PrimaryKeyConstraint("item_id"),
    )
    op.create_table(
        "app_meta",
        sa.Column("key", sa.String(), nullable=False),
        sa.Column("value", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("key"),
    )
    op.create_table(
        "scan_runs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.Column("items_scanned", sa.Integer(), nullable=True),
        sa.Column("items_tagged", sa.Integer(), nullable=True),
        sa.Column("items_image_modified", sa.Integer(), nullable=True),
        sa.Column("scan_type", sa.String(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "scan_errors",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("item_id", sa.String(), nullable=True),
        sa.Column("item_name", sa.Text(), nullable=True),
        sa.Column("file_path", sa.Text(), nullable=True),
        sa.Column("error_type", sa.String(), nullable=True),
        sa.Column("first_seen", sa.DateTime(), nullable=True),
        sa.Column("last_seen", sa.DateTime(), nullable=True),
        sa.Column("scan_count", sa.Integer(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_scan_errors_item_id", "scan_errors", ["item_id"], unique=False)


def downgrade() -> None:
    # Below the baseline there is no schema: a downgrade would drop every table
    # and the whole scan index with it. Restore a backup instead.
    raise NotImplementedError("refusing to downgrade below the baseline; restore a state.db backup instead")
