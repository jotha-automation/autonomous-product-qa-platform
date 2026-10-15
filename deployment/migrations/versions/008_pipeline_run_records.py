"""Pipeline run history independent of Drive uploads."""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "008"
down_revision: Union[str, None] = "007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "pipeline_run_records" in inspector.get_table_names():
        return
    op.create_table(
        "pipeline_run_records",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("run_id", sa.String(64), nullable=False),
        sa.Column("project_id", sa.String(64), nullable=False),
        sa.Column("drive_folder_id", sa.String(255), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="running"),
        sa.Column("started_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.Column("artifact_dir", sa.String(512), nullable=True),
        sa.Column("drive_uploaded", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("drive_run_folder_id", sa.String(255), nullable=True),
    )
    op.create_index("ix_pipeline_run_records_run_id", "pipeline_run_records", ["run_id"], unique=True)
    op.create_index("ix_pipeline_run_records_drive_folder_id", "pipeline_run_records", ["drive_folder_id"])
    op.create_index("ix_pipeline_run_records_project_id", "pipeline_run_records", ["project_id"])


def downgrade() -> None:
    op.drop_index("ix_pipeline_run_records_project_id", table_name="pipeline_run_records")
    op.drop_index("ix_pipeline_run_records_drive_folder_id", table_name="pipeline_run_records")
    op.drop_index("ix_pipeline_run_records_run_id", table_name="pipeline_run_records")
    op.drop_table("pipeline_run_records")
