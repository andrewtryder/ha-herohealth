"""Focused no-network tests for safety-critical Hero rules."""

from datetime import datetime, timedelta

import pytest
from homeassistant.exceptions import ServiceValidationError
from homeassistant.util import dt as dt_util

from custom_components.hero_health import _find_eligible_dose
from custom_components.hero_health.api.auth import parse_login_fields, pkce_challenge
from custom_components.hero_health.entity import is_low_medication, parse_hero_datetime


def test_pkce_challenge_rfc_vector():
    assert (
        pkce_challenge("dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk")
        == "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM"
    )


def test_login_fields_from_attribute_order():
    html = '<input value="c" type="hidden" name="csrfmiddlewaretoken">'
    html += '<input name="visitor_id" value="v">'
    assert parse_login_fields(html) == ("c", "v")


@pytest.mark.parametrize("value", ["low", "alert", "empty"])
def test_named_low_medication_rules(value):
    assert is_low_medication(value, None)


@pytest.mark.parametrize("value", ["high", "medium", "mid", "midlow", None])
def test_non_low_medication_rules(value):
    assert not is_low_medication(value, None)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (0, True),
        (0.249, True),
        (0.25, False),
        (0.251, False),
        (1, False),
        ("bad", False),
        (None, False),
    ],
)
def test_calculated_low_medication_rules(value, expected):
    assert is_low_medication(None, value) is expected


def test_naive_datetime_is_aware():
    assert parse_hero_datetime("2026-06-10 07:30:00").tzinfo is not None
    assert parse_hero_datetime("2026-06-10T07:30:00-04:00").utcoffset() == timedelta(
        hours=-4
    )


def test_hero_datetime_rejects_invalid_value():
    with pytest.raises(ValueError):
        parse_hero_datetime("not a date")


@pytest.mark.parametrize(
    ("delta", "eligible"),
    [
        (timedelta(minutes=30), True),
        (timedelta(minutes=30, seconds=1), False),
        (timedelta(), True),
        (timedelta(hours=-6), True),
        (timedelta(hours=-6, seconds=-1), False),
    ],
)
def test_eligible_dose_window(monkeypatch, delta, eligible):
    now = datetime(2026, 6, 10, 12, 0, tzinfo=dt_util.UTC)
    monkeypatch.setattr(dt_util, "now", lambda: now)
    scheduled = (now + delta).isoformat()
    home = {
        "dates": [
            {
                "times": [
                    {
                        "scheduled_datetime": scheduled,
                        "doses": [{"state": "time_to_take"}],
                    }
                ]
            }
        ]
    }
    if eligible:
        assert _find_eligible_dose(home, None) == scheduled
    else:
        with pytest.raises(ServiceValidationError):
            _find_eligible_dose(home, None)


def test_eligible_dose_requires_time_to_take_and_is_deterministic(monkeypatch):
    now = datetime(2026, 6, 10, 12, 0, tzinfo=dt_util.UTC)
    monkeypatch.setattr(dt_util, "now", lambda: now)
    earlier = (now - timedelta(minutes=1)).isoformat()
    later = (now + timedelta(minutes=1)).isoformat()
    home = {
        "dates": [
            {
                "times": [
                    {"scheduled_datetime": later, "doses": [{"state": "time_to_take"}]},
                    {
                        "scheduled_datetime": earlier,
                        "doses": [{"state": "time_to_take"}],
                    },
                    {
                        "scheduled_datetime": "invalid",
                        "doses": [{"state": "time_to_take"}],
                    },
                    {"scheduled_datetime": later, "doses": [{"state": "taken"}]},
                ]
            }
        ]
    }
    assert _find_eligible_dose(home, None) == earlier
