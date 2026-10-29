"""FastAPI application. Start with python -m app (runs DB migrations first)."""
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from app.config import settings
from app.routers import meetings, misc


@asynccontextmanager
async def lifespan(app):
    settings.validate()
    yield


development = settings.APP_ENV == "development"
app = FastAPI(title="MeetFlow", version="3.0.0", lifespan=lifespan,
              docs_url="/docs" if development else None,
              redoc_url="/redoc" if development else None,
              openapi_url="/openapi.json" if development else None)
app.add_middleware(CORSMiddleware, allow_origins=settings.CORS_ORIGINS,
                   allow_methods=["GET", "POST", "PUT"], allow_headers=["Authorization", "Content-Type"])


@app.exception_handler(RequestValidationError)
async def validation_handler(request: Request, exc: RequestValidationError):
    first = exc.errors()[0] if exc.errors() else {}
    where = ".".join(str(x) for x in first.get("loc", []) if x != "body")
    return JSONResponse(status_code=400, content={"detail": {
        "code": "VALIDATION_ERROR",
        "message": f"입력값을 다시 확인해 주세요 ({where}: {first.get('msg', '')})",
    }})


app.include_router(meetings.router)
app.include_router(misc.router)
