"""Add processed tracking to intake_root_folders."""
from alembic import op
import sqlalchemy as sa

revision = "004"
down_revision = "003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "intake_root_folders",
        sa.Column("processed", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(
        "intake_root_folders",
        sa.Column("process_count", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "intake_root_folders",
        sa.Column("last_pipeline_run_id", sa.String(64), nullable=True),
    )
    op.add_column(
        "intake_root_folders",
        sa.Column("last_results_drive_folder_id", sa.String(255), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("intake_root_folders", "last_results_drive_folder_id")
    op.drop_column("intake_root_folders", "last_pipeline_run_id")
    op.drop_column("intake_root_folders", "process_count")
    op.drop_column("intake_root_folders", "processed")
