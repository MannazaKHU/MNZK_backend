import json
import os
from pathlib import Path
import subprocess
import sys
from datetime import datetime, timezone
from sqlalchemy import MetaData, Table, create_engine, select


def test_upgrade_preserves_legacy_meeting_and_backfills_submission_count(tmp_path):
    root = Path(__file__).resolve().parents[1]
    env = {**os.environ, "DATABASE_URL": f"sqlite:///{tmp_path / 'legacy.sqlite3'}"}

    def migrate(*args):
        subprocess.run([sys.executable, "-m", "alembic", *args], cwd=root, env=env,
                       check=True, capture_output=True, text=True)

    migrate("upgrade", "0001")
    engine = create_engine(env["DATABASE_URL"])
    metadata = MetaData()
    meetings = Table("meetings", metadata, autoload_with=engine)
    members = Table("members", metadata, autoload_with=engine)
    now = datetime.now(timezone.utc)
    with engine.begin() as db:
        db.execute(meetings.insert().values(id="legacy", title="existing", invite_code="legacy-code",
                   candidate_dates=["2026-11-01"], time_range={"start":"10:00","end":"14:00"},
                   slot_minutes=60, status="recommended", revision=2, created_at=now,
                   recommendation_result={"recommendations": []}))
        for i in range(2):
            db.execute(members.insert().values(id=f"member-{i}", meeting_id="legacy", name=f"m{i}",
                       is_host=i == 0, token_digest=str(i) * 64, availability={},
                       availability_submitted=i == 0, preference={"start_location": {"text":"x","lat":37,"lng":127}} if i == 0 else None,
                       created_at=now))
    migrate("upgrade", "head")
    migrate("check")
    fresh = MetaData()
    meetings = Table("meetings", fresh, autoload_with=engine)
    members = Table("members", fresh, autoload_with=engine)
    with engine.connect() as db:
        meeting = db.execute(select(meetings)).mappings().one()
        assert meeting["title"] == "existing"
        assert meeting["expected_member_count"] == 2
        assert meeting["recommendation_result"] == {"recommendations": []}
        submitted = db.execute(select(members.c.submitted_at).order_by(members.c.id)).scalars().all()
        assert submitted[0] is not None and submitted[1] is None
    engine.dispose()
