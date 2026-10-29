"""Track dynamic group size, complete submissions and automatic processing."""
import json
from alembic import op
import sqlalchemy as sa

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("meetings", sa.Column("expected_member_count", sa.Integer(), nullable=False, server_default="1"))
    options = '{"count":5,"radius_m":1000,"extra_text":""}'
    op.add_column("meetings", sa.Column("recommendation_options", sa.JSON(), nullable=False, server_default=options))
    op.add_column("meetings", sa.Column("processing_started_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("meetings", sa.Column("processing_error", sa.JSON(), nullable=True))
    op.add_column("members", sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True))
    conn = op.get_bind()
    conn.execute(sa.text("UPDATE meetings SET expected_member_count = (SELECT COUNT(*) FROM members WHERE members.meeting_id = meetings.id) WHERE EXISTS (SELECT 1 FROM members WHERE members.meeting_id = meetings.id)"))
    # Preserve existing meetings and results. Count complete legacy inputs as submitted.
    for row in conn.execute(sa.text("SELECT id, preference, availability_submitted, created_at FROM members")).mappings():
        pref = row["preference"]
        if isinstance(pref, str):
            pref = json.loads(pref)
        if row["availability_submitted"] and pref and pref.get("start_location"):
            conn.execute(sa.text("UPDATE members SET submitted_at = :at WHERE id = :id"),
                         {"at": row["created_at"], "id": row["id"]})
    conn.execute(sa.text("UPDATE meetings SET status = 'waiting' WHERE status IN ('collecting', 'scheduled')"))


def downgrade():
    # Extra capacity/state columns can be removed without deleting meetings or members.
    op.drop_column("members", "submitted_at")
    for name in ("processing_error", "processing_started_at", "recommendation_options", "expected_member_count"):
        op.drop_column("meetings", name)
