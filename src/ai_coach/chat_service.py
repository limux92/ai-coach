"""In-App Conversational Coach service with Gemini streaming, goal persistence, and token metering."""
from __future__ import annotations

import json
import logging
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, AsyncGenerator

import httpx

from .coach_context import build_context
from .storage import Store, safe_id, utcnow

logger = logging.getLogger("ai_coach.chat")

MAX_GOAL_WORDS = 100
MAX_MESSAGE_LENGTH = 4000
DEFAULT_GEMINI_MODEL = "gemini-2.5-flash"


def get_vertex_access_token() -> str | None:
    """Obtain a Google Cloud OAuth access token via dev env, ADC, or metadata server."""
    dev_token = os.environ.get("GOOGLE_ACCESS_TOKEN")
    if dev_token:
        return dev_token

    try:
        import google.auth
        from google.auth.transport.requests import Request

        creds, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
        if not creds.valid:
            creds.refresh(Request())
        if creds.token:
            return creds.token
    except Exception:
        pass

    try:
        with httpx.Client(timeout=1.5) as client:
            resp = client.get(
                "http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/token",
                headers={"Metadata-Flavor": "Google"},
            )
            if resp.status_code == 200:
                return resp.json().get("access_token")
    except Exception:
        pass

    return None


def count_words(text: str) -> int:
    """Count whitespace-separated words."""
    if not text or not isinstance(text, str):
        return 0
    return len(text.strip().split())


def validate_goal(goal: str) -> int:
    """Validate athlete goal note length (max 100 words)."""
    words = count_words(goal)
    if words > MAX_GOAL_WORDS:
        raise ValueError(f"Athlete goal exceeds maximum of {MAX_GOAL_WORDS} words (got {words} words)")
    return words


def load_system_prompt() -> str:
    """Load core endurance coaching prompt from repo or fallback."""
    candidate_paths = [
        Path(__file__).resolve().parents[2] / "docs" / "AI_COACH_SYSTEM_PROMPT.md",
        Path.cwd() / "docs" / "AI_COACH_SYSTEM_PROMPT.md",
    ]
    for path in candidate_paths:
        if path.is_file():
            try:
                return path.read_text(encoding="utf-8")
            except Exception:
                pass
    return (
        "You are an elite collaborative endurance coach. Help athletes understand their training "
        "and agree on a realistic plan through conversation. Be practical, concise, and ground recommendations "
        "in physiological metrics (Critical Power, CTL/ATL/TSB, and recovery markers)."
    )


def assemble_system_instruction(store: Store, user_id: str | None = None) -> str:
    """Assemble system prompt with athlete goal, English directive, and pre-injected physiological context."""
    base_prompt = load_system_prompt()
    goal_doc = store.get_athlete_goal()
    athlete_goal = goal_doc.get("goal", "").strip() if goal_doc else ""

    try:
        settings_obj = getattr(store, "settings", None)
        if settings_obj is None:
            from .config import Settings

            settings_obj = Settings()
        sync_status = {}
        try:
            from .main import status

            sync_status = status()
        except Exception:
            sync_status = {}
        context_data = build_context(store, settings_obj, days=42, upcoming=14, sync_status=sync_status)
        context_json = json.dumps(context_data, default=str, ensure_ascii=False)
    except Exception as exc:
        logger.warning(f"Failed to build full physiological context for chat: {exc}")
        context_json = "{}"

    sections = [
        base_prompt,
        "\n\n## Communication Style & Language Requirements:\n"
        "Always communicate with the athlete in natural, encouraging, and clear English unless explicitly instructed otherwise by the athlete. "
        "Keep recommendations practical and grounded in the athlete's physiological data.",
        "\n\n## Current Athlete Coaching Focus & Goals (Max 100 words):\n"
        + (athlete_goal or "No active goal specified yet."),
        "\n\n## Pre-Injected Athlete Physiological Context (/v1/context):\n" + context_json,
    ]
    return "".join(sections)


async def call_gemini_stream(
    api_key: str | None,
    model: str,
    system_instruction: str,
    history: list[dict[str, Any]],
    message: str,
    project_id: str | None = None,
    vertex_location: str = "europe-west1",
) -> AsyncGenerator[tuple[str, dict[str, int] | None], None]:
    """Call Google Gemini streaming endpoint via Vertex AI or Google AI Studio, falling back to mock stream if unauthenticated."""
    access_token = get_vertex_access_token() if not api_key else None

    if not api_key and not access_token:
        # Graceful development / offline mock stream
        mock_chunks = [
            "Hello! I am your AI Endurance Coach. ",
            "I've reviewed your current physiological data and athlete profile. ",
            f"Regarding your query: '{message[:80]}...' — let's keep your training load balanced ",
            "and ensure you maintain adequate recovery before your next key workout.",
        ]
        for chunk in mock_chunks:
            yield chunk, None
        yield "", {"prompt_tokens": 120, "completion_tokens": 45}
        return

    # Build contents array
    contents = []
    for msg in history:
        role = "user" if msg.get("role") == "user" else "model"
        content_text = str(msg.get("content", ""))
        if content_text.strip():
            contents.append({"role": role, "parts": [{"text": content_text}]})
    contents.append({"role": "user", "parts": [{"text": message}]})

    clean_model = model.replace("models/", "")
    headers = {"Content-Type": "application/json"}

    if access_token:
        proj = project_id or os.environ.get("GCP_PROJECT_ID", "magne-ai-coach-20260915")
        loc = vertex_location or "europe-west1"
        url = f"https://{loc}-aiplatform.googleapis.com/v1/projects/{proj}/locations/{loc}/publishers/google/models/{clean_model}:streamGenerateContent?alt=sse"
        headers["Authorization"] = f"Bearer {access_token}"
        payload = {
            "systemInstruction": {"parts": [{"text": system_instruction}]},
            "contents": contents,
            "generationConfig": {
                "temperature": 0.4,
                "maxOutputTokens": 2048,
            },
        }
    else:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{clean_model}:streamGenerateContent?alt=sse&key={api_key}"
        payload = {
            "system_instruction": {"parts": [{"text": system_instruction}]},
            "contents": contents,
            "generationConfig": {
                "temperature": 0.4,
                "maxOutputTokens": 2048,
            },
        }

    prompt_tokens = 0
    completion_tokens = 0

    async with httpx.AsyncClient(timeout=httpx.Timeout(60.0, connect=10.0)) as client:
        async with client.stream("POST", url, json=payload, headers={"Content-Type": "application/json"}) as response:
            if response.status_code != 200:
                error_body = await response.aread()
                logger.error(f"Gemini API returned status {response.status_code}: {error_body.decode(errors='replace')}")
                yield f"I encountered an error communicating with the coaching model ({response.status_code}). Please try again shortly.", None
                yield "", {"prompt_tokens": 0, "completion_tokens": 0}
                return

            async for line in response.aiter_lines():
                line = line.strip()
                if not line or not line.startswith("data: "):
                    continue
                data_str = line[6:].strip()
                if not data_str or data_str == "[DONE]":
                    continue
                try:
                    event = json.loads(data_str)
                    candidates = event.get("candidates", [])
                    if candidates:
                        parts = candidates[0].get("content", {}).get("parts", [])
                        for part in parts:
                            text = part.get("text", "")
                            if text:
                                yield text, None
                    usage = event.get("usageMetadata", {})
                    if usage:
                        prompt_tokens = usage.get("promptTokenCount", prompt_tokens)
                        completion_tokens = usage.get("candidatesTokenCount", completion_tokens)
                except Exception as parse_err:
                    logger.debug(f"Error parsing Gemini SSE chunk: {parse_err}")

    yield "", {"prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens}
