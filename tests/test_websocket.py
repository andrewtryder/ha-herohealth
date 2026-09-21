"""Mock-only tests of the Hero dispense WebSocket state machine."""

import asyncio
import json
import logging

import aiohttp
import pytest

from custom_components.hero_health.api.client import HeroCloudClient
from custom_components.hero_health.api.exceptions import (
    HeroDispenseError,
    HeroDispenseOutcomeUnknown,
)


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


class StartSendFailWebSocket(FakeWebSocket):
    async def send_json(self, payload):
        self.sent.append(payload)
        if payload["type"] == "dispense_frontend_start":
            raise aiohttp.ClientConnectionError("closed")


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
async def test_dispense_extends_timeout_after_started_event():
    class SlowCompletionWebSocket(FakeWebSocket):
        async def __anext__(self):
            if not self.messages:
                raise StopAsyncIteration
            next_message = self.messages[0]
            payload = json.loads(next_message.data)
            if payload.get("type") == "dispense_frontend_completed":
                await asyncio.sleep(0.3)
            return self.messages.pop(0)

    ws = SlowCompletionWebSocket(
        [
            message(
                {"type": "response_authorization", "payload": {"status": "success"}}
            ),
            message(
                {
                    "type": "dispense_frontend_preflight_status",
                    "payload": {"status": True},
                }
            ),
            message({"type": "dispense_frontend_started", "payload": {}}),
            message(
                {
                    "type": "dispense_frontend_message",
                    "payload": {"message": "Dispensing"},
                }
            ),
            message(
                {"type": "dispense_frontend_completed", "payload": {"status": True}}
            ),
        ]
    )
    client = HeroCloudClient(FakeSession(ws), "token", "account")

    result = await client.dispense_scheduled_dose(
        "2026-01-01T10:00:00+00:00",
        timeout_seconds=0.2,
        completion_timeout_seconds=0.6,
    )

    assert result["status"] == "completed"
    assert result["messages"] == ["Dispensing"]


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


@pytest.mark.asyncio
async def test_failure_after_start_is_ambiguous_and_notified():
    ws = FakeWebSocket(
        [
            message(
                {"type": "response_authorization", "payload": {"status": "success"}}
            ),
            message(
                {
                    "type": "dispense_frontend_preflight_status",
                    "payload": {"status": True},
                }
            ),
        ]
    )
    marked = []

    async def mark_start():
        marked.append(True)

    client = HeroCloudClient(FakeSession(ws), "token")
    with pytest.raises(HeroDispenseOutcomeUnknown):
        await client.dispense_scheduled_dose("time", on_start_sent=mark_start)
    assert marked == [True]
    assert [item["type"] for item in ws.sent][-1] == "dispense_frontend_start"
    ws = FakeWebSocket([])
    client = HeroCloudClient(FakeSession(ws), "token")
    with pytest.raises(HeroDispenseError):
        await client.dispense_scheduled_dose("time")


@pytest.mark.asyncio
async def test_start_marker_failure_prevents_start_frame():
    ws = FakeWebSocket(
        [
            message(
                {"type": "response_authorization", "payload": {"status": "success"}}
            ),
            message(
                {
                    "type": "dispense_frontend_preflight_status",
                    "payload": {"status": True},
                }
            ),
        ]
    )

    async def persist_failure():
        raise RuntimeError("storage unavailable")

    with pytest.raises(RuntimeError, match="storage unavailable"):
        await HeroCloudClient(FakeSession(ws), "token").dispense_scheduled_dose(
            "time", on_start_sent=persist_failure
        )
    assert "dispense_frontend_start" not in [item["type"] for item in ws.sent]


@pytest.mark.asyncio
async def test_start_send_failure_is_ambiguous_after_marker():
    ws = StartSendFailWebSocket(
        [
            message(
                {"type": "response_authorization", "payload": {"status": "success"}}
            ),
            message(
                {
                    "type": "dispense_frontend_preflight_status",
                    "payload": {"status": True},
                }
            ),
        ]
    )
    marked = []

    async def mark_start():
        marked.append(True)

    with pytest.raises(HeroDispenseOutcomeUnknown):
        await HeroCloudClient(FakeSession(ws), "token").dispense_scheduled_dose(
            "time", on_start_sent=mark_start
        )
    assert marked == [True]


@pytest.mark.asyncio
async def test_concurrent_dispenses_are_serialized():
    def handshake():
        return [
            message(
                {"type": "response_authorization", "payload": {"status": "success"}}
            ),
            message(
                {
                    "type": "dispense_frontend_preflight_status",
                    "payload": {"status": True},
                }
            ),
            message(
                {"type": "dispense_frontend_completed", "payload": {"status": True}}
            ),
        ]

    first = FakeWebSocket(handshake())
    second = FakeWebSocket(handshake())
    session = FakeSession(first)
    client = HeroCloudClient(session, "token")
    one = asyncio.create_task(client.dispense_scheduled_dose("one"))
    await asyncio.sleep(0)
    session.ws = second
    two = asyncio.create_task(client.dispense_scheduled_dose("two"))
    await asyncio.gather(one, two)


@pytest.mark.asyncio
async def test_websocket_correlation_mismatches():
    # Preflight account mismatch
    ws = FakeWebSocket(
        [
            message(
                {"type": "response_authorization", "payload": {"status": "success"}}
            ),
            message(
                {
                    "type": "dispense_frontend_preflight_status",
                    "payload": {"status": True, "account_id": "different-account"},
                }
            ),
        ]
    )
    client = HeroCloudClient(FakeSession(ws), "token", "my-account")
    with pytest.raises(HeroDispenseError, match="account ID mismatch"):
        await client.dispense_scheduled_dose("2026-01-01T10:00:00+00:00")

    # Preflight scheduled datetime mismatch
    ws = FakeWebSocket(
        [
            message(
                {"type": "response_authorization", "payload": {"status": "success"}}
            ),
            message(
                {
                    "type": "dispense_frontend_preflight_status",
                    "payload": {"status": True, "scheduled_datetime": "wrong-time"},
                }
            ),
        ]
    )
    client = HeroCloudClient(FakeSession(ws), "token", "my-account")
    with pytest.raises(HeroDispenseError, match="scheduled datetime mismatch"):
        await client.dispense_scheduled_dose("2026-01-01T10:00:00+00:00")


@pytest.mark.asyncio
async def test_websocket_started_and_completed_correlation_and_order():
    # Unexpected completed before start sent
    ws = FakeWebSocket(
        [
            message(
                {"type": "response_authorization", "payload": {"status": "success"}}
            ),
            message(
                {"type": "dispense_frontend_completed", "payload": {"status": True}}
            ),
        ]
    )
    client = HeroCloudClient(FakeSession(ws), "token", "my-account")
    with pytest.raises(HeroDispenseError, match="Unexpected"):
        await client.dispense_scheduled_dose("2026-01-01T10:00:00+00:00")

    # Started account mismatch
    ws = FakeWebSocket(
        [
            message(
                {"type": "response_authorization", "payload": {"status": "success"}}
            ),
            message(
                {
                    "type": "dispense_frontend_preflight_status",
                    "payload": {"status": True},
                }
            ),
            message(
                {
                    "type": "dispense_frontend_started",
                    "payload": {"account_id": "wrong-account"},
                }
            ),
        ]
    )
    client = HeroCloudClient(FakeSession(ws), "token", "my-account")
    with pytest.raises(HeroDispenseOutcomeUnknown) as exc:
        await client.dispense_scheduled_dose("2026-01-01T10:00:00+00:00")
    assert "account ID mismatch" in str(exc.value.__cause__)

    # Completed account mismatch
    ws = FakeWebSocket(
        [
            message(
                {"type": "response_authorization", "payload": {"status": "success"}}
            ),
            message(
                {
                    "type": "dispense_frontend_preflight_status",
                    "payload": {"status": True},
                }
            ),
            message({"type": "dispense_frontend_started", "payload": {}}),
            message(
                {
                    "type": "dispense_frontend_completed",
                    "payload": {"status": True, "account_id": "wrong-account"},
                }
            ),
        ]
    )
    client = HeroCloudClient(FakeSession(ws), "token", "my-account")
    with pytest.raises(HeroDispenseOutcomeUnknown) as exc:
        await client.dispense_scheduled_dose("2026-01-01T10:00:00+00:00")
    assert "account ID mismatch" in str(exc.value.__cause__)

    # Completed scheduled datetime mismatch
    ws = FakeWebSocket(
        [
            message(
                {"type": "response_authorization", "payload": {"status": "success"}}
            ),
            message(
                {
                    "type": "dispense_frontend_preflight_status",
                    "payload": {"status": True},
                }
            ),
            message({"type": "dispense_frontend_started", "payload": {}}),
            message(
                {
                    "type": "dispense_frontend_completed",
                    "payload": {"status": True, "scheduled_datetime": "wrong-time"},
                }
            ),
        ]
    )
    client = HeroCloudClient(FakeSession(ws), "token", "my-account")
    with pytest.raises(HeroDispenseOutcomeUnknown) as exc:
        await client.dispense_scheduled_dose("2026-01-01T10:00:00+00:00")
    assert "scheduled datetime mismatch" in str(exc.value.__cause__)


@pytest.mark.asyncio
async def test_websocket_timeout_and_disconnect_after_start():
    class TimeoutWebSocket(FakeWebSocket):
        def __init__(self):
            super().__init__(
                [
                    message(
                        {
                            "type": "response_authorization",
                            "payload": {"status": "success"},
                        }
                    ),
                    message(
                        {
                            "type": "dispense_frontend_preflight_status",
                            "payload": {"status": True},
                        }
                    ),
                ]
            )

        async def __anext__(self):
            if not self.messages:
                raise TimeoutError()
            return self.messages.pop(0)

    ws = TimeoutWebSocket()
    client = HeroCloudClient(FakeSession(ws), "token", "account")
    with pytest.raises(HeroDispenseOutcomeUnknown, match="timed out"):
        await client.dispense_scheduled_dose("2026-01-01T10:00:00+00:00")

    class DisconnectWebSocket(FakeWebSocket):
        def __init__(self):
            super().__init__(
                [
                    message(
                        {
                            "type": "response_authorization",
                            "payload": {"status": "success"},
                        }
                    ),
                    message(
                        {
                            "type": "dispense_frontend_preflight_status",
                            "payload": {"status": True},
                        }
                    ),
                ]
            )

        async def __anext__(self):
            if not self.messages:
                raise aiohttp.ClientConnectionError("lost")
            return self.messages.pop(0)

    ws = DisconnectWebSocket()
    client = HeroCloudClient(FakeSession(ws), "token", "account")
    with pytest.raises(
        HeroDispenseOutcomeUnknown, match="closed after dispense started"
    ):
        await client.dispense_scheduled_dose("2026-01-01T10:00:00+00:00")


@pytest.mark.asyncio
async def test_websocket_debug_events_exclude_sensitive_payload_values(caplog):
    ws = FakeWebSocket(
        [
            message(
                {"type": "response_authorization", "payload": {"status": "success"}}
            ),
            message(
                {
                    "type": "dispense_frontend_preflight_status",
                    "payload": {"status": True},
                }
            ),
            message(
                {
                    "type": "unrecognized_terminal_event",
                    "payload": {
                        "account_id": "sensitive-account",
                        "scheduled_datetime": "sensitive-scheduled-time",
                        "error": "sensitive-error",
                    },
                }
            ),
        ]
    )
    client = HeroCloudClient(FakeSession(ws), "token", "account")

    with caplog.at_level(
        logging.DEBUG, logger="custom_components.hero_health.api.client"
    ):
        with pytest.raises(HeroDispenseOutcomeUnknown):
            await client.dispense_scheduled_dose("scheduled")

    assert "unrecognized_terminal_event" in caplog.text
    assert "has_nonempty_error=True" in caplog.text
    assert "status_success=True" in caplog.text
    assert "sensitive-account" not in caplog.text
    assert "sensitive-scheduled-time" not in caplog.text
    assert "sensitive-error" not in caplog.text
