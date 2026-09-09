"""Tests for specialists API."""

from __future__ import annotations

import pytest


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.asyncio
async def test_list_specialists(client):
    response = await client.get("/api/v1/specialists/")
    assert response.status_code == 200
    data = response.json()
    assert "specialists" in data
    assert isinstance(data["specialists"], list)
    assert data["count"] > 0


@pytest.mark.asyncio
async def test_get_specialist(client):
    response = await client.get("/api/v1/specialists/sql_database_analyst")
    assert response.status_code == 200
    data = response.json()
    assert data["id"] == "sql_database_analyst"
    assert "capabilities" in data


@pytest.mark.asyncio
async def test_get_nonexistent_specialist(client):
    response = await client.get("/api/v1/specialists/nonexistent")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_invoke_only_registered_specialist_skill(client):
    response = await client.post(
        "/api/v1/specialists/nlp_text_analyst/invoke",
        json={"skill": "sentiment", "params": {"text": "This product is great"}},
    )
    assert response.status_code == 200

    blocked = await client.post(
        "/api/v1/specialists/nlp_text_analyst/invoke",
        json={"skill": "register", "params": {}},
    )
    assert blocked.status_code == 404
