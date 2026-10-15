"""System root APTP folder + project folder linkage

Revision ID: 003
Revises: 002
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "003"
down_revision: Union[str, None] = "002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "intake_system_roots",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("drive_folder_id", sa.String(255), nullable=False, unique=True),
        sa.Column("root_name", sa.String(64), server_default="APTP"),
        sa.Column("first_seen_at", sa.DateTime(), server_default=sa.func.now()),
        sa.Column("last_synced_at", sa.DateTime(), nullable=True),
        sa.Column("project_folder_count", sa.Integer(), server_default="0"),
    )
    op.add_column(
        "intake_root_folders",
        sa.Column("system_root_drive_id", sa.String(255), nullable=True),
    )
    op.add_column(
        "intake_root_folders",
        sa.Column("folder_slug", sa.String(255), nullable=True),
    )
    op.create_index(
        "ix_intake_root_folders_system_root",
        "intake_root_folders",
        ["system_root_drive_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_intake_root_folders_system_root", "intake_root_folders")
    op.drop_column("intake_root_folders", "folder_slug")
    op.drop_column("intake_root_folders", "system_root_drive_id")
    op.drop_table("intake_system_roots")
