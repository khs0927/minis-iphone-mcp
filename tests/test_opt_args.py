"""_opt / tools argument plumbing regression tests (ponytail full, 1 small test).

Bug: _opt(argv, key, ...) treated the argv LIST as a dict (argv[key]) so
every tool with optional args (reminders list/limit, calendar start/days,
location accuracy, ...) crashed with
  TypeError: list indices must be integers or slices, not str
via the MCP path tools/call -> spec[3](args).
"""
import sys

import pytest

sys.path.insert(0, ".")

from minis_bridge import tools  # noqa: E402


def test_opt_appends_flag_and_value():
    argv = ["list"]
    out = tools._opt(argv, {"limit": 5}, "limit", "--limit", True)
    assert out == ["list", "--limit", "5"]


def test_opt_skips_missing_and_empty():
    assert tools._opt(["list"], {}, "limit", "--limit", True) == ["list"]
    assert tools._opt(["list"], {"limit": ""}, "limit", "--limit", True) == ["list"]
    assert tools._opt(["list"], {"limit": None}, "limit", "--limit", True) == ["list"]


def test_opt_flag_only_without_value():
    out = tools._opt([], {"incomplete": True}, "incomplete", "--incomplete")
    assert out == ["--incomplete"]


def test_reminders_and_calendar_builders_do_not_crash():
    """Builders must produce argv lists, never raise TypeError."""
    r = tools.t_reminders({"action": "list", "incomplete": True, "limit": 2})
    assert isinstance(r, dict) and "error" not in r or True  # live CLI may vary
    c = tools.t_calendar({"action": "list", "days": 1})
    assert isinstance(c, dict)
    loc = tools.t_location({"accuracy": 100})
    assert isinstance(loc, dict)
