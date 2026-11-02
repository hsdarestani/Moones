"""Auditable embedding rate snapshot.

Revision ID: 0045_embedding_pricing_audit
Revises: 0044_memory_privacy_usage
"""
from alembic import op
import sqlalchemy as sa

revision = "0045_embedding_pricing_audit"
down_revision = "0044_memory_privacy_usage"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("memory_embedding_usage", sa.Column("price_usd_per_million", sa.Numeric(12, 6), nullable=True))
    op.add_column("memory_embedding_usage", sa.Column("pricing_basis", sa.String(32), nullable=True))


def downgrade():
    op.drop_column("memory_embedding_usage", "pricing_basis")
    op.drop_column("memory_embedding_usage", "price_usd_per_million")
