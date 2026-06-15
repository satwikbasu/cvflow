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
      country_indeed: india
      linkedin_fetch_description: true
      max_distill_per_cohort: 60
      top_n_per_cohort: 5
      reconsider_discovered: false
    preferences:
      yoe_have: 1
      min_ctc_lpa: 7
      exclude_title_keywords: ["senior", "lead"]
      yoe_buffer: 2
      top_ctc_lpa: 40
      fit_weight: 0.7
      comp_weight: 0.3
      prefer_roles:
        devops: 1.0
        backend: 0.8
      exclude_when:
        - {field: night_shift_only, equals: true}
        - {field: min_years_required, greater_than: 3}
    llm:
      brain:
        provider: "nvidia_nim"
        api_key: " nvapi-xxx"
        base_url: "https://integrate.api.nvidia.com/v1"
        model: "meta/llama-3.3-70b-instruct"
        max_requests_per_minute: 40
      distillation:
        provider: "mistral"
        api_key: "mistral-key"
        base_url: "https://api.mistral.ai/v1"
        model: "mistral-small-2506"
        max_requests_per_minute: 300
      tailoring:
        provider: "cerebras"
        api_key: "csk-yyy"
        base_url: "https://api.cerebras.ai/v1"
        model: "gpt-oss-120b"
        max_requests_per_minute: 5
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
    assert cfg.llm.brain.model == "meta/llama-3.3-70b-instruct"
    assert cfg.llm.distillation.model == "mistral-small-2506"
    assert cfg.llm.tailoring.model == "gpt-oss-120b"
    assert cfg.llm.tailoring.max_requests_per_minute == 5
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
          country_indeed: india
          linkedin_fetch_description: true
          max_distill_per_cohort: 60
          top_n_per_cohort: 5
          reconsider_discovered: true
        preferences:
          yoe_have: 1
          min_ctc_lpa: 7
          exclude_title_keywords: [senior, lead]
          yoe_buffer: 2
          top_ctc_lpa: 40
          fit_weight: 0.7
          comp_weight: 0.3
          prefer_roles:
            devops: 1.0
            backend: 0.8
          exclude_when:
            - {field: night_shift_only, equals: true}
        llm:
          brain:
            provider: nvidia
            api_key: k
            base_url: u
            model: m
            max_requests_per_minute: 40
          distillation:
            provider: mistral
            api_key: k
            base_url: u
            model: m
            max_requests_per_minute: 300
          tailoring:
            provider: cerebras
            api_key: k
            base_url: u
            model: m
            max_requests_per_minute: 5
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
    assert cfg.preferences.exclude_title_keywords == ["senior", "lead"]


def test_phase14_preferences_and_discovery_fields(tmp_path):
    from cvflow.config import load_config
    cfg = load_config(_write(tmp_path, VALID_YAML))
    assert cfg.preferences.yoe_buffer == 2
    assert cfg.preferences.top_ctc_lpa == 40
    assert cfg.preferences.fit_weight == 0.7
    assert cfg.preferences.comp_weight == 0.3
    assert cfg.preferences.prefer_roles == {"devops": 1.0, "backend": 0.8}
    assert cfg.preferences.exclude_when == [
        {"field": "night_shift_only", "equals": True},
        {"field": "min_years_required", "greater_than": 3},
    ]
    assert cfg.discovery.country_indeed == "india"
    assert cfg.discovery.linkedin_fetch_description is True
    assert cfg.discovery.max_distill_per_cohort == 60
    assert cfg.discovery.top_n_per_cohort == 5
    assert cfg.discovery.reconsider_discovered is False


def test_phase14_weights_must_sum_to_one(tmp_path):
    import pytest

    from cvflow.config import ConfigError, load_config
    bad = VALID_YAML.replace("fit_weight: 0.7", "fit_weight: 0.8")
    with pytest.raises(ConfigError) as exc:
        load_config(_write(tmp_path, bad))
    assert "fit_weight" in str(exc.value) and "sum" in str(exc.value).lower()


def test_discovery_drop_summary_defaults(tmp_path: Path) -> None:
    """Discovery section without the six new keys → load succeeds with defaults."""
    cfg = load_config(_write(tmp_path, VALID_YAML))
    assert cfg.discovery.log_dir == "logs/discover"
    assert cfg.discovery.summarize_drops is True
    assert cfg.discovery.drop_summary_provider == "tailoring"
    assert cfg.discovery.drop_summary_max_chars == 700
    assert cfg.discovery.drop_summary_samples_per_bucket == 3
    assert cfg.discovery.cron_sends_drops is False


def test_discovery_drop_summary_overrides(tmp_path: Path) -> None:
    """Overriding each new key is reflected on the loaded config."""
    overrides = (
        '  log_dir: "logs/custom"\n'
        "  summarize_drops: false\n"
        '  drop_summary_provider: "brain"\n'
        "  drop_summary_max_chars: 400\n"
        "  drop_summary_samples_per_bucket: 5\n"
        "  cron_sends_drops: true\n"
    )
    # Inject the new keys into the discovery section.
    yaml_text = VALID_YAML.replace(
        "  reconsider_discovered: false\n",
        "  reconsider_discovered: false\n" + overrides,
    )
    cfg = load_config(_write(tmp_path, yaml_text))
    assert cfg.discovery.log_dir == "logs/custom"
    assert cfg.discovery.summarize_drops is False
    assert cfg.discovery.drop_summary_provider == "brain"
    assert cfg.discovery.drop_summary_max_chars == 400
    assert cfg.discovery.drop_summary_samples_per_bucket == 5
    assert cfg.discovery.cron_sends_drops is True


def test_discovery_drop_summary_max_chars_wrong_type(tmp_path: Path) -> None:
    """drop_summary_max_chars present but wrong type → ConfigError."""
    bad = VALID_YAML.replace(
        "  reconsider_discovered: false\n",
        '  reconsider_discovered: false\n  drop_summary_max_chars: "not-an-int"\n',
    )
    with pytest.raises(ConfigError):
        load_config(_write(tmp_path, bad))


def test_example_config_includes_naukri_site():
    # Phase 14A §2/§8: Naukri is the only India-native source populating INR salary
    # + experience_range. The committed example must enable it (secret-free file).
    from pathlib import Path

    from cvflow.config import load_config
    cfg = load_config(Path(__file__).resolve().parent.parent / "config.example.yaml")
    assert "naukri" in cfg.discovery.sites


def test_use_jd_analysis_defaults_false_when_absent(tmp_path: Path) -> None:
    # VALID_YAML omits use_jd_analysis; the absent-key default must be False.
    cfg = load_config(_write(tmp_path, VALID_YAML))
    assert cfg.resume.use_jd_analysis is False


def test_use_jd_analysis_reads_true(tmp_path: Path) -> None:
    # Setting use_jd_analysis: true in the resume block must be reflected on load.
    yaml_text = VALID_YAML.replace(
        '  latex_compiler: "latexmk"\n',
        '  latex_compiler: "latexmk"\n  use_jd_analysis: true\n',
    )
    cfg = load_config(_write(tmp_path, yaml_text))
    assert cfg.resume.use_jd_analysis is True


def test_max_projects_defaults_two_when_absent(tmp_path: Path) -> None:
    # VALID_YAML omits max_projects; the absent-key default must be 2.
    cfg = load_config(_write(tmp_path, VALID_YAML))
    assert cfg.resume.max_projects == 2


def test_max_projects_reads_override(tmp_path: Path) -> None:
    yaml_text = VALID_YAML.replace(
        '  latex_compiler: "latexmk"\n',
        '  latex_compiler: "latexmk"\n  max_projects: 3\n',
    )
    cfg = load_config(_write(tmp_path, yaml_text))
    assert cfg.resume.max_projects == 3


def test_rephrase_defaults_true_when_absent(tmp_path: Path) -> None:
    cfg = load_config(_write(tmp_path, VALID_YAML))
    assert cfg.resume.rephrase is True


def test_rephrase_reads_false_override(tmp_path: Path) -> None:
    yaml_text = VALID_YAML.replace(
        '  latex_compiler: "latexmk"\n',
        '  latex_compiler: "latexmk"\n  rephrase: false\n',
    )
    cfg = load_config(_write(tmp_path, yaml_text))
    assert cfg.resume.rephrase is False


def test_disabled_sections_empty_when_absent(tmp_path: Path) -> None:
    cfg = load_config(_write(tmp_path, VALID_YAML))
    assert cfg.resume.disabled_sections == frozenset()


def test_disabled_sections_reads_false_entries(tmp_path: Path) -> None:
    yaml_text = VALID_YAML.replace(
        '  latex_compiler: "latexmk"\n',
        '  latex_compiler: "latexmk"\n  sections:\n'
        "    projects: false\n    skills: true\n    education: false\n",
    )
    cfg = load_config(_write(tmp_path, yaml_text))
    assert cfg.resume.disabled_sections == frozenset({"projects", "education"})
