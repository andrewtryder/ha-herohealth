"""Mock-only tests of the Hero dispense WebSocket state machine."""

import asyncio
import json

import aiohttp
import pytest

from custom_components.hero_health.api.client import HeroCloudClient
from custom_components.hero_health.api.exceptions import HeroDispenseError


class FakeWebSocket:
    def __init__(self, messages):
        self.messages = list(messages)
        self.sent = []
        self.closed = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        self.closed += 1

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self.messages:
            raise StopAsyncIteration
        return self.messages.pop(0)

    async def send_json(self, payload):
        self.sent.append(payload)


class FakeSession:
    def __init__(self, ws):
        self.ws = ws

    def ws_connect(self, *_args, **_kwargs):
        return self.ws


def message(payload):
    return type(
        "Message", (), {"type": aiohttp.WSMsgType.TEXT, "data": json.dumps(payload)}
    )()


@pytest.mark.asyncio
async def test_dispense_waits_for_completed_and_answers_ping():
    ws = FakeWebSocket(
        [
            message(
                {"type": "response_authorization", "payload": {"status": "success"}}
            ),
            message({"type": "request_ping", "payload": {}}),
            message(
                {
                    "type": "dispense_frontend_preflight_status",
                    "payload": {"status": True},
                }
            ),
            message({"type": "dispense_frontend_started", "payload": {}}),
            message(
                {"type": "dispense_frontend_completed", "payload": {"status": True}}
            ),
        ]
    )
    client = HeroCloudClient(FakeSession(ws), "token", "account")
    assert (await client.dispense_scheduled_dose("2026-01-01T10:00:00+00:00"))[
        "status"
    ] == "completed"
    assert [item["type"] for item in ws.sent] == [
        "request_authorization",
        "dispense_frontend_preflight_check",
        "response_ping",
        "dispense_frontend_start",
    ]
    assert ws.closed == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [
        {"type": "response_authorization", "payload": {"status": "failed"}},
        {"type": "dispense_frontend_preflight_status", "payload": {"status": False}},
        {"type": "dispense_frontend_completed", "payload": {"status": False}},
    ],
)
async def test_dispense_failure_events_raise(payload):
    ws = FakeWebSocket([message(payload)])
    client = HeroCloudClient(FakeSession(ws), "token", "account")
    with pytest.raises(HeroDispenseError):
        await client.dispense_scheduled_dose("2026-01-01T10:00:00+00:00")
    assert ws.closed == 1


@pytest.mark.asyncio
async def test_malformed_or_closed_socket_raises():
    ws = FakeWebSocket(
        [type("Message", (), {"type": aiohttp.WSMsgType.TEXT, "data": "{"})()]
    )
    client = HeroCloudClient(FakeSession(ws), "token")
    with pytest.raises(HeroDispenseError):
        await client.dispense_scheduled_dose("time")
    ws = FakeWebSocket([])
    client = HeroCloudClient(FakeSession(ws), "token")
    with pytest.raises(HeroDispenseError):
        await client.dispense_scheduled_dose("time")


@pytest.mark.asyncio
async def test_concurrent_dispenses_are_serialized():
    first = FakeWebSocket(
        [message({"type": "dispense_frontend_completed", "payload": {"status": True}})]
    )
    second = FakeWebSocket(
        [message({"type": "dispense_frontend_completed", "payload": {"status": True}})]
    )
    session = FakeSession(first)
    client = HeroCloudClient(session, "token")
    one = asyncio.create_task(client.dispense_scheduled_dose("one"))
    await asyncio.sleep(0)
    session.ws = second
    two = asyncio.create_task(client.dispense_scheduled_dose("two"))
    await asyncio.gather(one, two)
