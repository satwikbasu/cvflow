"""P1A: config-derived Hermes cron registration lines."""
from __future__ import annotations

from dataclasses import dataclass

from cvflow.cron import format_hermes_schedule, to_utc_cron


@dataclass
class _Sch:
    daily_discovery_time: str
    timezone: str
    heartbeat_interval_minutes: int


@dataclass
class _Cfg:
    schedule: _Sch


def test_to_utc_cron_kolkata_noon() -> None:
    assert to_utc_cron("12:00", "Asia/Kolkata") == "30 6 * * *"


def test_to_utc_cron_kolkata_eight_am() -> None:
    assert to_utc_cron("08:00", "Asia/Kolkata") == "30 2 * * *"


def test_to_utc_cron_utc_passthrough() -> None:
    assert to_utc_cron("09:15", "UTC") == "15 9 * * *"


def test_to_utc_cron_wraps_past_midnight() -> None:
    assert to_utc_cron("02:00", "Asia/Kolkata") == "30 20 * * *"


def test_format_hermes_schedule_emits_four_lines() -> None:
    cfg = _Cfg(_Sch(daily_discovery_time="12:00", timezone="Asia/Kolkata",
                     heartbeat_interval_minutes=360))
    lines = format_hermes_schedule(cfg)
    assert lines == [
        "hermes cron create '30 6 * * *' --no-agent --script cvflow-discover.sh --name cvflow-discover",  # noqa: E501
        "hermes cron create 'every 5m' --no-agent --script cvflow-sweep-otp.sh --name cvflow-sweep-otp",  # noqa: E501
        "hermes cron create 'every 360m' --no-agent --script cvflow-heartbeat.sh --name cvflow-heartbeat",  # noqa: E501
        "hermes cron create 'every 168h' --no-agent --script cvflow-learn.sh --name cvflow-learn",
    ]


def test_main_print_hermes_schedule(capsys, monkeypatch) -> None:
    import cvflow.cron as cron

    cfg = _Cfg(_Sch(daily_discovery_time="12:00", timezone="Asia/Kolkata",
                    heartbeat_interval_minutes=360))
    monkeypatch.setattr(cron, "load_config", lambda _p: cfg, raising=False)
    cron.main(["print-hermes-schedule"])
    out = capsys.readouterr().out
    assert "30 6 * * *" in out
    assert out.count("hermes cron create") == 4
