"""Resize knowledge_chunks.embedding for text-embedding-3-small (1536 dims)."""
from typing import Sequence, Union

from alembic import op

revision: str = "006"
down_revision: Union[str, None] = "005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TABLE knowledge_chunks DROP COLUMN IF EXISTS embedding")
    op.execute("ALTER TABLE knowledge_chunks ADD COLUMN embedding vector(1536)")


def downgrade() -> None:
    op.execute("ALTER TABLE knowledge_chunks DROP COLUMN IF EXISTS embedding")
    op.execute("ALTER TABLE knowledge_chunks ADD COLUMN embedding vector(384)")
