"""Liveness and DB readiness. AI usage is process-local and exposed only in development."""
from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session
from app.clients.ai_client import USAGE
from app.config import settings
from app.database import get_session
from app.errors import ApiError
from app.models import Meeting

router = APIRouter(tags=["system"])


@router.get("/health")
def health():
    return {"ok": True, "mock": settings.AI_PROVIDER == settings.MAPS_PROVIDER == "mock",
            "ai_provider": settings.AI_PROVIDER, "maps_provider": settings.MAPS_PROVIDER}


@router.get("/health/ready")
def ready(db: Session = Depends(get_session)):
    try:
        db.execute(select(Meeting.id).limit(1))
    except SQLAlchemyError:
        raise ApiError(503, "DATABASE_UNAVAILABLE", "DB 연결 또는 마이그레이션 상태를 확인해 주세요.")
    return {"ok": True, "database": "ready"}


if settings.APP_ENV == "development":
    @router.get("/api/usage")
    def usage():
        return {**USAGE, "scope": "current_process"}
