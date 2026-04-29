"""Tests for the /healthz endpoint."""

from fastapi.testclient import TestClient

from app.main import app


def test_healthz_returns_200_with_ok_body() -> None:
    """GET /healthz returns HTTP 200 and {"status": "ok"}."""
    with TestClient(app) as client:
        response = client.get("/healthz")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
