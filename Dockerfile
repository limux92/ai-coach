FROM python:3.14.7-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PYTHONPATH=/app/src
WORKDIR /app
COPY requirements.lock .
RUN pip install --no-cache-dir -r requirements.lock && useradd --uid 10001 --create-home coach
COPY src ./src
USER coach
CMD ["sh", "-c", "exec uvicorn ai_coach.main:app --host 0.0.0.0 --port ${PORT:-8080}"]
