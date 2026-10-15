"""Intake registry tables — folder index, file snapshots, change log

Revision ID: 002
Revises: 001
Create Date: 2026-06-02

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "002"
down_revision: Union[str, None] = "001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "intake_root_folders",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("project_id", sa.String(64), nullable=False, index=True),
        sa.Column("drive_folder_id", sa.String(255), nullable=False, index=True),
        sa.Column("root_name", sa.String(255), nullable=False),
        sa.Column("source", sa.String(16), server_default="drive"),
        sa.Column("first_seen_at", sa.DateTime(), server_default=sa.func.now()),
        sa.Column("last_synced_at", sa.DateTime(), nullable=True),
        sa.Column("last_file_count", sa.Integer(), server_default="0"),
        sa.Column("last_folder_count", sa.Integer(), server_default="0"),
    )
    op.create_table(
        "intake_subfolders",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("root_id", sa.String(36), sa.ForeignKey("intake_root_folders.id"), index=True),
        sa.Column("relative_path", sa.String(512), nullable=False),
        sa.Column("folder_name", sa.String(255), nullable=False),
        sa.Column("drive_folder_id", sa.String(255), nullable=True),
        sa.Column("first_seen_at", sa.DateTime(), server_default=sa.func.now()),
        sa.Column("last_seen_at", sa.DateTime(), server_default=sa.func.now()),
    )
    op.create_table(
        "intake_file_records",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("root_id", sa.String(36), sa.ForeignKey("intake_root_folders.id"), index=True),
        sa.Column("drive_file_id", sa.String(255), nullable=False),
        sa.Column("relative_path", sa.String(512), nullable=False, index=True),
        sa.Column("file_name", sa.String(255), nullable=False),
        sa.Column("mime_type", sa.String(128), nullable=True),
        sa.Column("inferred_doc_type", sa.String(16), server_default="OTHER"),
        sa.Column("content_fingerprint", sa.String(255), nullable=False),
        sa.Column("modified_at", sa.String(64), nullable=True),
        sa.Column("status", sa.String(32), server_default="indexed"),
        sa.Column("first_seen_at", sa.DateTime(), server_default=sa.func.now()),
        sa.Column("last_seen_at", sa.DateTime(), server_default=sa.func.now()),
        sa.Column("last_processed_at", sa.DateTime(), nullable=True),
        sa.Column("last_run_id", sa.String(64), nullable=True),
    )
    op.create_table(
        "intake_change_logs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("root_id", sa.String(36), sa.ForeignKey("intake_root_folders.id"), index=True),
        sa.Column("project_id", sa.String(64), nullable=False, index=True),
        sa.Column("event_type", sa.String(32), nullable=False),
        sa.Column("entity_type", sa.String(16), nullable=False),
        sa.Column("entity_path", sa.String(512), nullable=False),
        sa.Column("entity_id", sa.String(255), nullable=True),
        sa.Column("old_fingerprint", sa.String(255), nullable=True),
        sa.Column("new_fingerprint", sa.String(255), nullable=True),
        sa.Column("details", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), index=True),
    )


def downgrade() -> None:
    op.drop_table("intake_change_logs")
    op.drop_table("intake_file_records")
    op.drop_table("intake_subfolders")
    op.drop_table("intake_root_folders")
