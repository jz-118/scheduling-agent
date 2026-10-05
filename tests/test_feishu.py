from fastapi.testclient import TestClient
from uuid import uuid4

from app.api import app


client = TestClient(app)


def test_aily_webhook_sync_response():
    schedule_id = f"F-{uuid4().hex}"
    response = client.post("/feishu/aily/webhook", json={"schedule_id": schedule_id, "query": "E07 周五晚班不能排"})
    assert response.status_code == 200
    body = response.json()
    assert body["status_code"] == "0"
    assert body["result"]["status"] == "approved"
    assert len(body["result"]["solutions"]) == 3

    replay = client.post("/feishu/aily/webhook", json={"schedule_id": schedule_id, "query": "E07 周五晚班不能排"})
    assert replay.json()["result"]["status"] == "already_generated"
    assert replay.json()["result"]["replayed"] is True


def test_feishu_url_verification():
    response = client.post("/feishu/events", json={"type": "url_verification", "challenge": "abc", "token": "x"})
    assert response.status_code == 200
    assert response.json() == {"challenge": "abc"}
