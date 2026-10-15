"""Project pipeline lifecycle: success tracking and user closure."""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "007"
down_revision: Union[str, None] = "006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "intake_root_folders",
        sa.Column("last_pipeline_status", sa.String(32), nullable=True),
    )
    op.add_column(
        "intake_root_folders",
        sa.Column("last_successful_run_id", sa.String(64), nullable=True),
    )
    op.add_column(
        "intake_root_folders",
        sa.Column("project_closed_at", sa.DateTime(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("intake_root_folders", "project_closed_at")
    op.drop_column("intake_root_folders", "last_successful_run_id")
    op.drop_column("intake_root_folders", "last_pipeline_status")
