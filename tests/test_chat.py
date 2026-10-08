"""Comprehensive verification for Story CHAT-01: In-App Conversational Coach Drawer."""
from __future__ import annotations

import copy
import json
import pytest
from fastapi.testclient import TestClient

from ai_coach import main
from ai_coach.chat_service import (
    count_words,
    validate_goal,
    assemble_system_instruction,
    call_gemini_stream,
    MAX_GOAL_WORDS,
)
from ai_coach.config import Settings
from ai_coach.storage import safe_id


class ChatMemoryStore:
    def __init__(self, user_id: str | None = None):
        self.user_id = user_id
        self.documents = {}
        self.token_usages = []
        self.chat_messages = []
        self.goals = {}

    def for_user(self, user_id: str | None) -> "ChatMemoryStore":
        store = ChatMemoryStore(user_id=user_id)
        store.documents = self.documents
        store.token_usages = self.token_usages
        store.chat_messages = self.chat_messages
        store.goals = self.goals
        return store

    def get(self, collection, doc_id):
        safe_id(doc_id)
        key = (collection, self.user_id, doc_id) if self.user_id and collection != "users" else (collection, doc_id)
        return copy.deepcopy(self.documents.get(key))

    def put(self, collection, doc_id, data, *, merge=True):
        safe_id(doc_id)
        key = (collection, self.user_id, doc_id) if self.user_id and collection != "users" else (collection, doc_id)
        if merge:
            old = self.documents.get(key, {})
            self.documents[key] = {**old, **copy.deepcopy(data)}
        else:
            self.documents[key] = copy.deepcopy(data)

    def list(self, collection, oldest, newest, *, limit=200, after=None):
        return []

    def get_athlete_goal(self):
        user_key = self.user_id or "default"
        return copy.deepcopy(self.goals.get(user_key, {"goal": "", "word_count": 0, "updated_at": None}))

    def save_athlete_goal(self, goal: str, word_count: int):
        user_key = self.user_id or "default"
        data = {
            "goal": goal.strip(),
            "word_count": word_count,
            "updated_at": "2026-10-07T00:00:00Z",
        }
        self.goals[user_key] = copy.deepcopy(data)
        return data

    def list_chat_messages(self, limit: int = 20):
        user_key = self.user_id or "default"
        user_msgs = [m for m in self.chat_messages if m.get("user_id") == user_key]
        return copy.deepcopy(user_msgs[-limit:])

    def save_chat_message(self, message_id: str, data: dict):
        user_key = self.user_id or "default"
        msg = {**data, "id": message_id, "user_id": user_key}
        self.chat_messages.append(copy.deepcopy(msg))

    def record_token_usage(self, usage_id: str, data: dict):
        self.token_usages.append(copy.deepcopy(data))


@pytest.fixture
def test_store(monkeypatch):
    store = ChatMemoryStore()
    monkeypatch.setattr(main, "_base_store", lambda: store)
    monkeypatch.setattr(main, "settings", lambda: Settings(
        project="test-project",
        bucket="test-bucket",
        gemini_api_key=None,
    ))
    return store


def test_word_counter_and_validation():
    assert count_words("") == 0
    assert count_words("   ") == 0
    assert count_words(None) == 0
    assert count_words("Sub 3:00 marathon in Valencia this December.") == 7

    # 100 words boundary
    words_100 = " ".join(["word"] * 100)
    assert validate_goal(words_100) == 100

    # 101 words exceeds limit
    words_101 = " ".join(["word"] * 101)
    with pytest.raises(ValueError, match="exceeds maximum of 100 words"):
        validate_goal(words_101)


def test_goal_endpoints_require_auth(test_store):
    client = TestClient(main.app)
    # Without X-User-Id header
    res = client.get("/v1/user/goal")
    assert res.status_code == 401

    res = client.post("/v1/user/goal", json={"goal": "Target 300W FTP"})
    assert res.status_code == 401


def test_goal_save_and_retrieve(test_store):
    client = TestClient(main.app)
    headers = {"x-user-id": "athlete_123"}

    # Initially empty
    res = client.get("/v1/user/goal", headers=headers)
    assert res.status_code == 200
    assert res.json() == {"goal": "", "word_count": 0, "updated_at": None}

    # Save valid goal
    goal_text = "Target sub 3:00 marathon in Valencia with 85 km weekly peak."
    res = client.post("/v1/user/goal", json={"goal": goal_text}, headers=headers)
    assert res.status_code == 200
    data = res.json()
    assert data["goal"] == goal_text
    assert data["word_count"] == 11
    assert data["updated_at"] is not None

    # Retrieve again
    res = client.get("/v1/user/goal", headers=headers)
    assert res.status_code == 200
    assert res.json()["goal"] == goal_text
    assert res.json()["word_count"] == 11


def test_goal_rejects_exceeding_100_words(test_store):
    client = TestClient(main.app)
    headers = {"x-user-id": "athlete_123"}

    oversized = " ".join(["run"] * 105)
    res = client.post("/v1/user/goal", json={"goal": oversized}, headers=headers)
    assert res.status_code == 400
    assert "exceeds maximum of 100 words" in res.json()["detail"]


def test_chat_history_requires_auth(test_store):
    client = TestClient(main.app)
    res = client.get("/v1/chat/history")
    assert res.status_code == 401


def test_chat_stream_subscription_enforcement(test_store):
    client = TestClient(main.app)
    headers = {"x-user-id": "unpaid_user"}

    # User does not have active subscription
    res = client.post("/v1/chat/stream", json={"message": "Can I train today?"}, headers=headers)
    assert res.status_code == 403
    assert "Active subscription required" in res.json()["detail"]


def test_chat_stream_owner_bypass_and_zero_rating(test_store):
    client = TestClient(main.app)
    owner_uid = "N0lThhWrg4YfdoYwHjJbvl5swmk2"
    headers = {"x-user-id": owner_uid}

    # Owner bypass allows streaming without subscription check
    res = client.post("/v1/chat/stream", json={"message": "What is my fatigue level?"}, headers=headers)
    assert res.status_code == 200
    assert "text/event-stream" in res.headers["content-type"]

    body = res.text
    assert "data: " in body
    assert "AI Endurance Coach" in body
    assert '"done": true' in body

    # Zero-rated: owner must NOT accumulate token_usage billing records
    assert len(test_store.token_usages) == 0

    # User and assistant messages should be saved to history
    history_res = client.get("/v1/chat/history", headers=headers)
    assert history_res.status_code == 200
    history = history_res.json()
    assert len(history) == 2
    assert history[0]["role"] == "user"
    assert history[0]["content"] == "What is my fatigue level?"
    assert history[1]["role"] == "model"
    assert "AI Endurance Coach" in history[1]["content"]


def test_chat_stream_metering_for_paying_subscriber(test_store):
    client = TestClient(main.app)
    user_id = "subscriber_789"
    # Mark user as active subscriber in store
    test_store.put("users", user_id, {
        "id": user_id,
        "email": "athlete@example.com",
        "status": "active",
    })

    headers = {"x-user-id": user_id}
    res = client.post("/v1/chat/stream", json={"message": "How do I taper?"}, headers=headers)
    assert res.status_code == 200
    body = res.text
    assert '"done": true' in body

    # Token usage must be metered for paying non-owner subscribers
    assert len(test_store.token_usages) == 1
    usage = test_store.token_usages[0]
    assert usage["user_id"] == user_id
    assert usage["prompt_tokens"] > 0
    assert usage["completion_tokens"] > 0
