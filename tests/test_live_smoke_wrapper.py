"""Offline checks for the live-smoke shell wrapper's safe failure paths."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

_WRAPPER = Path(__file__).parents[1] / "scripts" / "live-smoke"


def _temporary_wrapper(tmp_path: Path) -> Path:
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    wrapper = scripts / "live-smoke"
    shutil.copy(_WRAPPER, wrapper)
    return wrapper


def test_live_smoke_requires_local_env_file(tmp_path: Path):
    result = subprocess.run(
        ["sh", str(_temporary_wrapper(tmp_path))],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 2
    assert ".env.local" in result.stderr
    assert "HERO_EMAIL" not in result.stderr


def test_live_smoke_rejects_empty_credentials_without_running_python(tmp_path: Path):
    wrapper = _temporary_wrapper(tmp_path)
    (tmp_path / ".env.local").write_text("HERO_EMAIL=\nHERO_PASSWORD=\n")
    result = subprocess.run(
        ["sh", str(wrapper)], check=False, capture_output=True, text=True
    )
    assert result.returncode == 2
    assert "non-empty HERO_EMAIL and HERO_PASSWORD" in result.stderr
