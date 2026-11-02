"""Preserve unknown provider costs as NULL rather than reporting free usage."""
from alembic import op
import sqlalchemy as sa

revision = "0046_usage_cost_unknown"
down_revision = "0045_embedding_pricing_audit"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("ai_usage_events") as batch:
        batch.alter_column("cost_usd", existing_type=sa.Numeric(), nullable=True)
        batch.alter_column("cost_toman", existing_type=sa.Numeric(), nullable=True)


def downgrade():
    # Unknown historical charges cannot truthfully be converted to zero.
    connection = op.get_bind()
    unknown = connection.scalar(sa.text(
        "SELECT count(*) FROM ai_usage_events WHERE cost_usd IS NULL OR cost_toman IS NULL"
    ))
    if unknown:
        raise RuntimeError("Reconcile unknown usage costs before downgrading 0046")
    with op.batch_alter_table("ai_usage_events") as batch:
        batch.alter_column("cost_usd", existing_type=sa.Numeric(), nullable=False)
        batch.alter_column("cost_toman", existing_type=sa.Numeric(), nullable=False)
