FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY pyproject.toml requirements.lock ./
COPY src ./src
COPY static ./static
ENV DASHBOARD_STATIC_DIR=/app/static/dashboard
RUN pip install --no-cache-dir -r requirements.lock && pip install --no-cache-dir --no-deps .
RUN useradd --uid 10001 --create-home appuser
USER 10001
CMD ["sh", "-c", "exec uvicorn ai_coach_mcp.app:create_app --factory --host 0.0.0.0 --port ${PORT:-8080} --no-access-log"]
