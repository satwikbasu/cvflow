"""Tests for cvflow.config — typed, validated loading of config.yaml.

Uses a synthetic fixture written to tmp_path. NEVER reads the real config.yaml,
which holds live secrets.
"""

import textwrap
from pathlib import Path

import pytest

from cvflow.config import Config, ConfigError, load_config

VALID_YAML = textwrap.dedent(
    """
    telegram:
      bot_token: "123:ABC"
      authorized_user_id: 42
    schedule:
      daily_discovery_time: "12:00"
      timezone: "Asia/Kolkata"
      heartbeat_interval_minutes: 360
    discovery:
      search_terms: ["go developer", "sre"]
      locations: ["Remote", "India"]
      sites: ["linkedin", "indeed"]
      results_wanted_per_site: 25
      hours_old: 72
      top_n_to_present: 8
    preferences:
      yoe_have: 1
      min_ctc_lpa: 7
      job_type: "fulltime"
      exclude_title_keywords: ["senior", "lead"]
      prefer_product_companies: true
      exclude_app_maintenance: true
      exclude_night_shift_only: true
    llm:
      brain:
        provider: "nvidia_nim"
        api_key: " nvapi-xxx"
        base_url: "https://integrate.api.nvidia.com/v1"
        model: "meta/llama-3.3-70b-instruct"
        max_requests_per_minute: 40
      tailoring:
        provider: "gemini"
        api_key: "AIza-yyy"
        model: "gemini-2.5-flash"
        max_requests_per_day: 1400
    resume:
      master_tex_path: "resume/master.tex"
      output_dir: "data/resumes"
      latex_compiler: "latexmk"
    automation:
      headless: false
      use_stealth: true
      storage_state_dir: "data/auth_state"
      form_timeout_minutes: 10
    auth:
      google_account_email: "burner@example.com"
      application_email: "apply@example.com"
      otp_timeout_minutes: 15
    storage:
      db_path: "data/cvflow.db"
      form_fields_path: "profile/form_fields.json"
    security:
      fernet_key_path: "data/.fernet_key"
    profile:
      knowledge_base_dir: "profile"
    """
)


def _write(tmp_path: Path, text: str) -> Path:
    p = tmp_path / "config.yaml"
    p.write_text(text)
    return p


def test_loads_valid_config(tmp_path: Path) -> None:
    cfg = load_config(_write(tmp_path, VALID_YAML))
    assert isinstance(cfg, Config)
    assert cfg.telegram.authorized_user_id == 42
    assert cfg.schedule.timezone == "Asia/Kolkata"
    assert cfg.discovery.top_n_to_present == 8
    assert cfg.llm.brain.model == "meta/llama-3.3-70b-instruct"
    assert cfg.llm.tailoring.max_requests_per_day == 1400
    assert cfg.auth.otp_timeout_minutes == 15
    assert cfg.storage.db_path == "data/cvflow.db"


def test_api_keys_are_stripped(tmp_path: Path) -> None:
    # The real config has a leading space in the NIM key; loader must strip it.
    cfg = load_config(_write(tmp_path, VALID_YAML))
    assert cfg.llm.brain.api_key == "nvapi-xxx"


def test_missing_file_raises_config_error(tmp_path: Path) -> None:
    with pytest.raises(ConfigError):
        load_config(tmp_path / "nope.yaml")


def test_missing_required_key_raises_naming_path(tmp_path: Path) -> None:
    broken = VALID_YAML.replace("  authorized_user_id: 42\n", "")
    with pytest.raises(ConfigError) as exc:
        load_config(_write(tmp_path, broken))
    assert "authorized_user_id" in str(exc.value)


def test_wrong_type_raises(tmp_path: Path) -> None:
    broken = VALID_YAML.replace("authorized_user_id: 42", 'authorized_user_id: "not-an-int"')
    with pytest.raises(ConfigError):
        load_config(_write(tmp_path, broken))


def test_malformed_time_raises(tmp_path: Path) -> None:
    broken = VALID_YAML.replace('daily_discovery_time: "12:00"', 'daily_discovery_time: "25:99"')
    with pytest.raises(ConfigError) as exc:
        load_config(_write(tmp_path, broken))
    assert "daily_discovery_time" in str(exc.value)


def test_preferences_block_parsed(tmp_path):
    from cvflow.config import load_config
    cfg_text = (tmp_path / "config.yaml")
    base = textwrap.dedent(
        """
        telegram:
          bot_token: x
          authorized_user_id: 1
        schedule:
          daily_discovery_time: '08:00'
          timezone: UTC
          heartbeat_interval_minutes: 30
        discovery:
          search_terms: [a]
          locations: [Remote]
          sites: [indeed]
          results_wanted_per_site: 5
          hours_old: 72
          top_n_to_present: 5
        preferences:
          yoe_have: 1
          min_ctc_lpa: 7
          job_type: fulltime
          exclude_title_keywords: [senior, lead]
          prefer_product_companies: true
          exclude_app_maintenance: true
          exclude_night_shift_only: true
        llm:
          brain:
            provider: nvidia
            api_key: k
            base_url: u
            model: m
            max_requests_per_minute: 40
          tailoring:
            provider: google
            api_key: k
            model: m
            max_requests_per_day: 50
        resume:
          master_tex_path: resume/master.tex
          output_dir: data/tailored
          latex_compiler: tectonic
        automation:
          headless: true
          use_stealth: true
          storage_state_dir: data/bw
          form_timeout_minutes: 30
        auth:
          google_account_email: a@b.c
          application_email: a@b.c
          otp_timeout_minutes: 15
        storage:
          db_path: data/db.sqlite
          form_fields_path: profile/form_fields.json
        security:
          fernet_key_path: data/.k
        profile:
          knowledge_base_dir: profile
        """
    )
    cfg_text.write_text(base)
    cfg = load_config(cfg_text)
    assert cfg.preferences.yoe_have == 1
    assert cfg.preferences.min_ctc_lpa == 7
    assert cfg.preferences.job_type == "fulltime"
    assert cfg.preferences.exclude_title_keywords == ["senior", "lead"]
    assert cfg.preferences.prefer_product_companies is True
    assert cfg.preferences.exclude_app_maintenance is True
    assert cfg.preferences.exclude_night_shift_only is True
