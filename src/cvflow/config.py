"""Typed loading & validation of ``config.yaml``.

The config holds all secrets (gitignored). This module turns it into a frozen,
typed :class:`Config` object and fails loudly — never silently — on any missing
key, wrong type, or malformed value.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

_TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


class ConfigError(Exception):
    """Raised when config.yaml is missing, malformed, or fails validation."""


@dataclass(frozen=True)
class TelegramConfig:
    bot_token: str
    authorized_user_id: int


@dataclass(frozen=True)
class ScheduleConfig:
    daily_discovery_time: str
    timezone: str
    heartbeat_interval_minutes: int


@dataclass(frozen=True)
class DiscoveryConfig:
    search_terms: list[str]
    locations: list[str]
    sites: list[str]
    results_wanted_per_site: int
    hours_old: int
    top_n_to_present: int


@dataclass(frozen=True)
class BrainConfig:
    provider: str
    api_key: str
    base_url: str
    model: str
    max_requests_per_minute: int


@dataclass(frozen=True)
class TailoringConfig:
    provider: str
    api_key: str
    model: str
    max_requests_per_day: int


@dataclass(frozen=True)
class LLMConfig:
    brain: BrainConfig
    tailoring: TailoringConfig


@dataclass(frozen=True)
class ResumeConfig:
    master_tex_path: str
    output_dir: str
    latex_compiler: str


@dataclass(frozen=True)
class AutomationConfig:
    headless: bool
    use_stealth: bool
    storage_state_dir: str
    form_timeout_minutes: int


@dataclass(frozen=True)
class AuthConfig:
    google_account_email: str
    application_email: str
    otp_timeout_minutes: int


@dataclass(frozen=True)
class StorageConfig:
    db_path: str
    form_fields_path: str


@dataclass(frozen=True)
class SecurityConfig:
    fernet_key_path: str


@dataclass(frozen=True)
class ProfileConfig:
    knowledge_base_dir: str


@dataclass(frozen=True)
class Config:
    telegram: TelegramConfig
    schedule: ScheduleConfig
    discovery: DiscoveryConfig
    llm: LLMConfig
    resume: ResumeConfig
    automation: AutomationConfig
    auth: AuthConfig
    storage: StorageConfig
    security: SecurityConfig
    profile: ProfileConfig


def _section(data: dict[str, Any], key: str, path: str) -> dict[str, Any]:
    if key not in data:
        raise ConfigError(f"missing required section: {path}{key}")
    value = data[key]
    if not isinstance(value, dict):
        raise ConfigError(f"section {path}{key} must be a mapping, got {type(value).__name__}")
    return value


def _get(data: dict[str, Any], key: str, typ: type, path: str) -> Any:
    if key not in data:
        raise ConfigError(f"missing required key: {path}{key}")
    value = data[key]
    # bool is a subclass of int; guard against silent coercion both ways.
    if typ is int and isinstance(value, bool):
        raise ConfigError(f"key {path}{key} must be int, got bool")
    if typ is not bool and isinstance(value, bool):
        raise ConfigError(f"key {path}{key} must be {typ.__name__}, got bool")
    if not isinstance(value, typ):
        raise ConfigError(f"key {path}{key} must be {typ.__name__}, got {type(value).__name__}")
    return value


def _get_str(data: dict[str, Any], key: str, path: str) -> str:
    return str(_get(data, key, str, path))


def _get_int(data: dict[str, Any], key: str, path: str) -> int:
    return int(_get(data, key, int, path))


def _get_bool(data: dict[str, Any], key: str, path: str) -> bool:
    return bool(_get(data, key, bool, path))


def _get_str_list(data: dict[str, Any], key: str, path: str) -> list[str]:
    value = _get(data, key, list, path)
    if not all(isinstance(item, str) for item in value):
        raise ConfigError(f"key {path}{key} must be a list of strings")
    return list(value)


def load_config(path: str | Path) -> Config:
    """Load, validate, and return the typed config at ``path``.

    Raises :class:`ConfigError` on a missing file, malformed YAML, or any
    missing/mistyped/invalid field.
    """
    path = Path(path)
    try:
        raw = path.read_text()
    except FileNotFoundError as exc:
        raise ConfigError(f"config file not found: {path}") from exc
    try:
        data = yaml.safe_load(raw)
    except yaml.YAMLError as exc:
        raise ConfigError(f"config is not valid YAML: {exc}") from exc
    if not isinstance(data, dict):
        raise ConfigError("config root must be a mapping")

    tg = _section(data, "telegram", "")
    sch = _section(data, "schedule", "")
    disc = _section(data, "discovery", "")
    llm = _section(data, "llm", "")
    brain = _section(llm, "brain", "llm.")
    tail = _section(llm, "tailoring", "llm.")
    res = _section(data, "resume", "")
    auto = _section(data, "automation", "")
    auth = _section(data, "auth", "")
    store = _section(data, "storage", "")
    sec = _section(data, "security", "")
    prof = _section(data, "profile", "")

    daily_time = _get_str(sch, "daily_discovery_time", "schedule.")
    if not _TIME_RE.match(daily_time):
        raise ConfigError(f"schedule.daily_discovery_time must be HH:MM, got {daily_time!r}")

    return Config(
        telegram=TelegramConfig(
            bot_token=_get_str(tg, "bot_token", "telegram.").strip(),
            authorized_user_id=_get_int(tg, "authorized_user_id", "telegram."),
        ),
        schedule=ScheduleConfig(
            daily_discovery_time=daily_time,
            timezone=_get_str(sch, "timezone", "schedule."),
            heartbeat_interval_minutes=_get_int(sch, "heartbeat_interval_minutes", "schedule."),
        ),
        discovery=DiscoveryConfig(
            search_terms=_get_str_list(disc, "search_terms", "discovery."),
            locations=_get_str_list(disc, "locations", "discovery."),
            sites=_get_str_list(disc, "sites", "discovery."),
            results_wanted_per_site=_get_int(disc, "results_wanted_per_site", "discovery."),
            hours_old=_get_int(disc, "hours_old", "discovery."),
            top_n_to_present=_get_int(disc, "top_n_to_present", "discovery."),
        ),
        llm=LLMConfig(
            brain=BrainConfig(
                provider=_get_str(brain, "provider", "llm.brain."),
                api_key=_get_str(brain, "api_key", "llm.brain.").strip(),
                base_url=_get_str(brain, "base_url", "llm.brain."),
                model=_get_str(brain, "model", "llm.brain."),
                max_requests_per_minute=_get_int(brain, "max_requests_per_minute", "llm.brain."),
            ),
            tailoring=TailoringConfig(
                provider=_get_str(tail, "provider", "llm.tailoring."),
                api_key=_get_str(tail, "api_key", "llm.tailoring.").strip(),
                model=_get_str(tail, "model", "llm.tailoring."),
                max_requests_per_day=_get_int(tail, "max_requests_per_day", "llm.tailoring."),
            ),
        ),
        resume=ResumeConfig(
            master_tex_path=_get_str(res, "master_tex_path", "resume."),
            output_dir=_get_str(res, "output_dir", "resume."),
            latex_compiler=_get_str(res, "latex_compiler", "resume."),
        ),
        automation=AutomationConfig(
            headless=_get_bool(auto, "headless", "automation."),
            use_stealth=_get_bool(auto, "use_stealth", "automation."),
            storage_state_dir=_get_str(auto, "storage_state_dir", "automation."),
            form_timeout_minutes=_get_int(auto, "form_timeout_minutes", "automation."),
        ),
        auth=AuthConfig(
            google_account_email=_get_str(auth, "google_account_email", "auth."),
            application_email=_get_str(auth, "application_email", "auth."),
            otp_timeout_minutes=_get_int(auth, "otp_timeout_minutes", "auth."),
        ),
        storage=StorageConfig(
            db_path=_get_str(store, "db_path", "storage."),
            form_fields_path=_get_str(store, "form_fields_path", "storage."),
        ),
        security=SecurityConfig(
            fernet_key_path=_get_str(sec, "fernet_key_path", "security."),
        ),
        profile=ProfileConfig(
            knowledge_base_dir=_get_str(prof, "knowledge_base_dir", "profile."),
        ),
    )
