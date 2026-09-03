"""Typed, tolerant models for the observed Hero responses."""

from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from typing import Any


@dataclass(slots=True)
class HeroTokens:
    access_token: str
    refresh_token: str
    expires_in: int
    created_at: float

    @classmethod
    def from_response(cls, data: dict[str, Any], created_at: float) -> HeroTokens:
        try:
            access_token = data["access_token"]
            if not isinstance(access_token, str) or not access_token:
                raise ValueError("access token is invalid")
            refresh_token = data.get("refresh_token", "")
            if refresh_token is None:
                refresh_token = ""
            if not isinstance(refresh_token, str):
                raise ValueError("refresh token is invalid")
            expires_in = int(data["expires_in"])
            if expires_in <= 0 or not isfinite(float(created_at)):
                raise ValueError("token timing is invalid")
            return cls(
                access_token,
                refresh_token,
                expires_in,
                created_at,
            )
        except (KeyError, TypeError, ValueError) as err:
            raise ValueError("Hero token response is incomplete") from err

    def as_dict(self) -> dict[str, Any]:
        return {
            "access_token": self.access_token,
            "refresh_token": self.refresh_token,
            "expires_in": self.expires_in,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> HeroTokens:
        return cls.from_response(data, float(data["created_at"]))


@dataclass(slots=True)
class HeroMedication:
    slot: int
    name: str | None
    pill_type: str | None
    pill_level_enum: str | None
    pill_level_calculated: str | float | None
    exact_pill_count: int | None
    updated_at: str | None

    @classmethod
    def from_api(cls, data: dict[str, Any]) -> HeroMedication:
        return cls(
            int(data.get("slot", 0)),
            data.get("name"),
            data.get("pill_type"),
            data.get("pill_level_enum"),
            data.get("pill_level_calculated"),
            data.get("exact_pill_count"),
            data.get("updated_at"),
        )


@dataclass(slots=True)
class HeroDose:
    scheduled_datetime: str
    states: list[str]
    medications: list[str]

    @property
    def has_time_to_take(self) -> bool:
        return "time_to_take" in self.states
