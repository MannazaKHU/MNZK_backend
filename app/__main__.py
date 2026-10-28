"""Local/container entry point. Apply migrations once before starting Uvicorn."""
from pathlib import Path
from alembic import command
from alembic.config import Config
import uvicorn
from app.config import settings


def main():
    settings.validate()
    config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    command.upgrade(config, "head")
    uvicorn.run("app.main:app", host=settings.HOST, port=settings.PORT,
                reload=settings.APP_ENV == "development")


if __name__ == "__main__":
    main()
