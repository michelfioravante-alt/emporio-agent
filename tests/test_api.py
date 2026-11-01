import pytest
from fastapi.testclient import TestClient

import app.main as main
from app.security import RateLimiter


@pytest.fixture()
def client(monkeypatch):
    calls = []

    def fake_chat(message, history=None):
        calls.append({"message": message, "history": history})
        return {
            "reply": "Olá!",
            "tool_calls": [
                {
                    "name": "get_order_status",
                    "arguments": {"order_id": 4, "customer_name": "Lucas Mendes da Silva"},
                    "result": {"authorized": True},
                }
            ],
        }

    monkeypatch.setattr(main, "chat", fake_chat)
    monkeypatch.setattr(main, "rate_limiter", RateLimiter(1000, 60))
    with TestClient(main.app) as test_client:
        test_client.calls = calls
        yield test_client


def test_long_history_is_trimmed_not_rejected(client):
    history = [
        {"role": "user" if i % 2 == 0 else "assistant", "content": "x" * 3000}
        for i in range(30)
    ]
    response = client.post("/api/chat", json={"message": "oi", "history": history})
    assert response.status_code == 200
    assert len(client.calls[-1]["history"]) == 12


def test_validation_error_is_a_readable_string(client):
    response = client.post("/api/chat", json={"message": "", "history": []})
    assert response.status_code == 422
    assert isinstance(response.json()["detail"], str)


def test_body_size_limit(client):
    response = client.post("/api/chat", json={"message": "oi", "history": [{"role": "user", "content": "y" * 200_000}]})
    assert response.status_code == 413


def test_security_headers(client):
    response = client.get("/")
    assert response.headers["x-frame-options"] == "DENY"
    assert "default-src 'self'" in response.headers["content-security-policy"]


def test_activity_feed_has_no_personal_data(client):
    body = client.post("/api/chat", json={"message": "meu pedido", "history": []}).json()
    assert "Lucas" not in str(body["activity"])
    assert body["debug"] is None


def test_debug_trace_is_redacted(client):
    body = client.post("/api/chat", json={"message": "meu pedido", "history": [], "include_debug": True}).json()
    assert body["debug"][0]["arguments"]["customer_name"] == "Lucas ***"


def test_rate_limit(monkeypatch, client):
    monkeypatch.setattr(main, "rate_limiter", RateLimiter(2, 60))
    codes = [client.post("/api/chat", json={"message": "oi", "history": []}).status_code for _ in range(3)]
    assert codes == [200, 200, 429]


def test_spoofed_forwarded_for_is_ignored_locally(monkeypatch, client):
    monkeypatch.setattr(main, "rate_limiter", RateLimiter(1, 60))
    first = client.post("/api/chat", json={"message": "oi", "history": []}, headers={"X-Forwarded-For": "1.1.1.1"})
    second = client.post("/api/chat", json={"message": "oi", "history": []}, headers={"X-Forwarded-For": "2.2.2.2"})
    assert (first.status_code, second.status_code) == (200, 429)
