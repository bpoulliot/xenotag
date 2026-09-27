"""Add media_state.field_order (roadmap U5: the `xt-interlaced` tag).

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-27

Additive and nullable: every existing row reads NULL -- "not probed since this
revision" -- and tags no `xt-interlaced` until a scan re-probes it. The tagger's
vocabulary bump (``tagger.TAG_VOCABULARY`` 4) makes the first scan after the
upgrade a full one, which re-probes every reachable item and fills the column.
An older image (which knows only 0001) still starts against it, and ignores it.

SQLite can ``ADD COLUMN`` in place, so batch mode issues a plain ``ALTER TABLE``
here rather than rebuilding the table.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | Sequence[str] | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("media_state", schema=None) as batch_op:
        batch_op.add_column(sa.Column("field_order", sa.String(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("media_state", schema=None) as batch_op:
        batch_op.drop_column("field_order")
