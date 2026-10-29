"""Check the configured DB without printing credentials. Migrate only with --migrate."""
import argparse
from pathlib import Path
import sys


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--supabase", action="store_true", help="Reject a local/non-Supabase DB")
    parser.add_argument("--project-ref", help="Require this Supabase project before connecting")
    parser.add_argument("--migrate", action="store_true", help="Apply project Alembic migrations")
    args = parser.parse_args()
    try:
        from sqlalchemy import inspect, text
        from app.config import settings
        from app.database import connection_url, engine

        url = connection_url(settings.DATABASE_URL)
        host = (url.host or "").lower()
        supabase = host.endswith((".supabase.co", ".supabase.com"))
        if args.supabase and not supabase:
            print("DATABASE_URL is not configured for Supabase. No connection or migration attempted.")
            return 1
        if args.project_ref and not (host == f"db.{args.project_ref}.supabase.co"
                                     or (url.username or "").endswith("." + args.project_ref)):
            print("DATABASE_URL does not match the requested project. No changes made.")
            return 1
        with engine.connect() as db:
            db.execute(text("SELECT 1"))
        print("Database connection OK:", "Supabase PostgreSQL" if supabase else url.get_backend_name())
        if args.migrate:
            from alembic import command
            from alembic.config import Config
            command.upgrade(Config(str(Path(__file__).resolve().parents[1] / "alembic.ini")), "head")
            print("Migrations applied.")
        expected = {"meetings", "members", "recommendation_runs", "alembic_version"}
        with engine.connect() as db:
            present = set(inspect(db).get_table_names())
            missing = sorted(expected - present)
            if missing:
                print("Missing project tables:", ", ".join(missing))
                return 1
            for table in sorted(expected):
                db.execute(text(f'SELECT 1 FROM "{table}" LIMIT 1'))
            if supabase:
                protected = set(db.execute(text(
                    "SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
                    "WHERE n.nspname='public' AND c.relrowsecurity"
                )).scalars())
                if not expected.issubset(protected):
                    print("Backend table RLS is incomplete. Apply migrations before publishing.")
                    return 1
        print("Project tables ready.")
        return 0
    except Exception as exc:
        # Do not print SQLAlchemy's exception text; connection failures can include private connection data.
        print("Database check failed (" + type(exc).__name__ + "). Check URI, password, SSL and network.")
        return 1


if __name__ == "__main__":
    sys.exit(main())
