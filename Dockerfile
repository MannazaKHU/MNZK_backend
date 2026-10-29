FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 HOST=0.0.0.0 APP_ENV=production
WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt && useradd --create-home --uid 10001 app
COPY --chown=app:app app ./app
COPY --chown=app:app migrations ./migrations
COPY --chown=app:app alembic.ini ./alembic.ini
COPY --chown=app:app scripts/smoke_test.py ./scripts/smoke_test.py
COPY --chown=app:app scripts/check_database.py ./scripts/check_database.py
RUN mkdir -p /app/data && chown app:app /app/data
USER app
EXPOSE 8000
CMD ["python", "-m", "app"]
