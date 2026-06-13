import pytest

from cvflow import recon
from cvflow.recon import ATS_LABELS, classify_ats


@pytest.mark.parametrize(
    "url, expected",
    [
        ("https://boards.greenhouse.io/acme/jobs/123", "greenhouse"),
        ("https://job-boards.greenhouse.io/acme/jobs/123", "greenhouse"),
        ("https://acme.greenhouse.io/jobs/123", "greenhouse"),
        ("https://jobs.lever.co/acme/abc-def", "lever"),
        ("https://jobs.ashbyhq.com/acme/uuid", "ashby"),
        ("https://acme.ashbyhq.com/uuid", "ashby"),
        ("https://acme.wd5.myworkdayjobs.com/en-US/careers/job/x", "workday"),
        ("https://acme.taleo.net/careersection/x", "taleo"),
        ("https://career.successfactors.com/x", "successfactors"),
        ("https://acme.successfactors.eu/x", "successfactors"),
        ("https://acme.icims.com/jobs/123/role/job", "icims"),
        ("https://www.naukri.com/job-listings-x-123", "naukri"),
        ("https://www.linkedin.com/jobs/view/123", "linkedin"),
        ("https://in.indeed.com/viewjob?jk=abc", "indeed"),
        ("https://careers.acme.com/job/123", "other"),
        ("https://greenhouse.acme.com/jobs", "other"),
        ("not a url", "other"),
        ("", "other"),
    ],
)
def test_classify_ats_by_pattern(url, expected):
    assert classify_ats(url) == expected


def test_every_returned_label_is_known():
    for url in ["https://boards.greenhouse.io/x", "https://careers.acme.com/x"]:
        assert classify_ats(url) in ATS_LABELS


def test_fetch_redirect_resolves_to_ats(monkeypatch):
    monkeypatch.setattr(
        recon, "_resolve_one_hop",
        lambda url: "https://boards.greenhouse.io/acme/jobs/9",
    )
    assert classify_ats("https://t.co/short", fetch_redirect=True) == "greenhouse"


def test_fetch_redirect_failure_falls_back_to_original(monkeypatch):
    monkeypatch.setattr(recon, "_resolve_one_hop", lambda url: None)
    assert classify_ats("https://jobs.lever.co/acme/x", fetch_redirect=True) == "lever"
    assert classify_ats("https://careers.acme.com/x", fetch_redirect=True) == "other"


def test_fetch_redirect_not_called_when_already_classified(monkeypatch):
    def _boom(url):
        raise AssertionError("must not resolve when host already matched")

    monkeypatch.setattr(recon, "_resolve_one_hop", _boom)
    assert classify_ats("https://boards.greenhouse.io/x", fetch_redirect=True) == "greenhouse"


def test_persist_tagging_contract() -> None:
    # documents the contract discovery uses: classify (no network) then set_ats
    from cvflow.storage import ApplicationStore

    store = ApplicationStore(":memory:")
    url = "https://jobs.lever.co/acme/x"
    store.add("j", "Acme", "Eng", url)
    store.set_ats("j", classify_ats(url))
    assert store.get("j").ats == "lever"
