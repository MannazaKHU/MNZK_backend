"""Create persistent meetings, members and recommendation runs."""
from alembic import op
import sqlalchemy as sa

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "meetings",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("title", sa.String(40), nullable=False),
        sa.Column("invite_code", sa.String(64), nullable=False, unique=True),
        sa.Column("candidate_dates", sa.JSON(), nullable=False),
        sa.Column("time_range", sa.JSON(), nullable=False),
        sa.Column("slot_minutes", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("confirmed", sa.JSON(), nullable=True),
        sa.Column("recommendation_result", sa.JSON(), nullable=True),
        sa.Column("confirmed_place", sa.JSON(), nullable=True),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "members",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("meeting_id", sa.String(36), sa.ForeignKey("meetings.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(20), nullable=False),
        sa.Column("is_host", sa.Boolean(), nullable=False),
        sa.Column("token_digest", sa.String(64), nullable=False, unique=True),
        sa.Column("availability", sa.JSON(), nullable=False),
        sa.Column("availability_submitted", sa.Boolean(), nullable=False),
        sa.Column("preference", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("meeting_id", "name", name="uq_member_name"),
    )
    op.create_index("ix_members_meeting_id", "members", ["meeting_id"])
    op.create_table(
        "recommendation_runs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("meeting_id", sa.String(36), sa.ForeignKey("meetings.id", ondelete="CASCADE"), nullable=False),
        sa.Column("input_revision", sa.Integer(), nullable=False),
        sa.Column("request", sa.JSON(), nullable=False),
        sa.Column("result", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_recommendation_runs_meeting_id", "recommendation_runs", ["meeting_id"])


def downgrade():
    op.drop_table("recommendation_runs")
    op.drop_table("members")
    op.drop_table("meetings")
