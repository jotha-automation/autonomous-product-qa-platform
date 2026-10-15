"""Knowledge chunks table for pgvector retrieval."""
from typing import Sequence, Union

from alembic import op

revision: str = "005"
down_revision: Union[str, None] = "004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS knowledge_chunks (
            id VARCHAR(36) PRIMARY KEY,
            project_id VARCHAR(64) NOT NULL,
            run_id VARCHAR(64),
            chunk_type VARCHAR(32) NOT NULL,
            external_id VARCHAR(128),
            title VARCHAR(512),
            content TEXT NOT NULL,
            metadata JSONB,
            created_at TIMESTAMP DEFAULT now(),
            embedding vector(384)
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_knowledge_chunks_project "
        "ON knowledge_chunks (project_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_knowledge_chunks_type "
        "ON knowledge_chunks (chunk_type)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS knowledge_chunks")
