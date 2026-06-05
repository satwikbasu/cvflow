"""FormFiller against the local fixture form. Skips if Playwright unavailable."""

from pathlib import Path

import pytest

playwright_api = pytest.importorskip("playwright.sync_api")

from cvflow.automation import FormFiller, SessionManager  # noqa: E402

FIXTURE = (Path(__file__).parent / "fixtures" / "form" / "page1.html").resolve()
FIXTURE_URL = FIXTURE.as_uri()


@pytest.fixture
def session(tmp_path):
    try:
        mgr = SessionManager(user_data_root=str(tmp_path / "profiles"), headless=True)
    except Exception as exc:  # browser binary missing
        pytest.skip(f"playwright chromium unavailable: {exc}")
    yield mgr
    mgr.close_all()


def test_discover_fields_reads_the_form(session):
    page = session.open("job1", FIXTURE_URL)
    filler = FormFiller(page)
    specs = {s.name: s for s in filler.discover_fields()}
    assert specs["full_name"].field_type == "text"
    assert specs["full_name"].required is True
    assert specs["experience_level"].field_type == "select"
    assert "senior" in specs["experience_level"].options
    assert specs["relocate"].field_type == "checkbox"
    assert specs["why_us"].field_type == "textarea"
    assert specs["resume"].field_type == "file"


def test_apply_fills_each_field_type(session, tmp_path):
    page = session.open("job1", FIXTURE_URL)
    filler = FormFiller(page)
    resume = tmp_path / "cv.pdf"
    resume.write_bytes(b"%PDF-1.4 fake")
    filler.apply("full_name", "text", "Ada Lovelace")
    filler.apply("experience_level", "select", "senior")
    filler.apply("relocate", "checkbox", "true")
    filler.apply("why_us", "textarea", "I love backends")
    filler.apply("resume", "file", str(resume))
    assert page.input_value("#full_name") == "Ada Lovelace"
    assert page.input_value("#experience_level") == "senior"
    assert page.is_checked("#relocate") is True
    assert page.input_value("#why_us") == "I love backends"


def test_capture_proof_returns_url_title_and_screenshot(session, tmp_path):
    page2 = (Path(__file__).parent / "fixtures" / "form" / "page2.html").resolve()
    page = session.open("job2", page2.as_uri())
    page.click("#submit")
    filler = FormFiller(page)
    proof = filler.capture_proof(str(tmp_path / "proof.png"))
    assert proof.page_title == "Application received"
    assert "ABC-12345" in (proof.confirmation_ref or "")
    assert proof.url.endswith("page2.html")
    assert Path(proof.screenshot_path).exists()
