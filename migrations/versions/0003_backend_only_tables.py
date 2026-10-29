"""Protect backend-owned tables from direct anonymous Supabase Data API access."""
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None

TABLES = ("meetings", "members", "recommendation_runs", "alembic_version")


def upgrade():
    if op.get_bind().dialect.name == "postgresql":
        # The migration/runtime table owner still has access. No frontend policies are added.
        for table in TABLES:
            op.execute(f'ALTER TABLE "{table}" ENABLE ROW LEVEL SECURITY')


def downgrade():
    # Keep protection enabled when rolling back the application version.
    pass
