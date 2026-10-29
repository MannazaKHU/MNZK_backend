"""Local/container entry point. Apply migrations once before starting Uvicorn."""
from pathlib import Path
import argparse
from alembic import command
from alembic.config import Config
import uvicorn
from app.config import settings


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--skip-migrations", action="store_true",
                        help="Use when the deployment pre-deploy command already ran migrations")
    args = parser.parse_args()
    settings.validate()
    if not args.skip_migrations:
        config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
        command.upgrade(config, "head")
    uvicorn.run("app.main:app", host=settings.HOST, port=settings.PORT,
                reload=settings.APP_ENV == "development")


if __name__ == "__main__":
    main()
