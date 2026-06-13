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


def _seed(store) -> None:
    rows = [
        ("g1", "greenhouse"), ("g2", "greenhouse"),
        ("l1", "lever"), ("o1", "other"), ("o2", None),
    ]
    for jid, ats in rows:
        store.add(jid, "Co", "R", "https://x/" + jid)
        if ats:
            store.set_ats(jid, ats)
    store.add_decision(job_id="g1", decision="apply")
    store.add_decision(job_id="l1", decision="apply")
    store.add_decision(job_id="o1", decision="skip")


def test_build_report_counts_presented_and_approved() -> None:
    from cvflow.recon import build_report
    from cvflow.storage import ApplicationStore

    store = ApplicationStore(":memory:")
    _seed(store)
    rep = build_report(store)
    assert rep["presented"]["greenhouse"] == 2
    assert rep["presented"]["lever"] == 1
    assert rep["presented"]["other"] == 2  # explicit "other" + untagged
    assert rep["approved"]["greenhouse"] == 1
    assert rep["approved"]["lever"] == 1
    assert rep["approved"].get("other", 0) == 0
    assert rep["totals"] == {"presented": 5, "approved": 2}


def test_format_report_is_a_table_string() -> None:
    from cvflow.recon import build_report, format_report
    from cvflow.storage import ApplicationStore

    store = ApplicationStore(":memory:")
    _seed(store)
    text = format_report(build_report(store))
    assert "greenhouse" in text and "%" in text
    # 2 of 2 approvals are GH+Lever -> 100% share line
    assert "Greenhouse+Lever share of approved: 100%" in text


def test_healthcheck_working_when_recent_jobs_classified() -> None:
    from cvflow.recon import build_healthcheck
    from cvflow.storage import ApplicationStore

    store = ApplicationStore(":memory:")
    store.add("g1", "Co", "R", "u1")
    store.set_ats("g1", "greenhouse")
    store.add("o1", "Co", "R", "u2")
    store.set_ats("o1", "other")
    hc = build_healthcheck(store, since="2000-01-01")
    assert hc["recent_total"] == 2
    assert hc["recent_non_other"] == 1
    assert "WORKING" in hc["verdict"]  # type: ignore[operator]


def test_healthcheck_suspicious_when_all_other() -> None:
    from cvflow.recon import build_healthcheck
    from cvflow.storage import ApplicationStore

    store = ApplicationStore(":memory:")
    store.add("o1", "Co", "R", "u1")
    store.set_ats("o1", "other")
    store.add("o2", "Co", "R", "u2")  # untagged -> COALESCE to other
    hc = build_healthcheck(store, since="2000-01-01")
    assert hc["recent_total"] == 2
    assert hc["recent_non_other"] == 0
    assert "SUSPICIOUS" in hc["verdict"]  # type: ignore[operator]


def test_healthcheck_no_data_when_since_in_future() -> None:
    from cvflow.recon import build_healthcheck
    from cvflow.storage import ApplicationStore

    store = ApplicationStore(":memory:")
    store.add("g1", "Co", "R", "u1")
    store.set_ats("g1", "greenhouse")
    hc = build_healthcheck(store, since="2999-01-01")
    assert hc["recent_total"] == 0
    assert "NO DATA" in hc["verdict"]  # type: ignore[operator]


def test_format_healthcheck_includes_verdict_and_cutoff() -> None:
    from cvflow.recon import build_healthcheck, format_healthcheck
    from cvflow.storage import ApplicationStore

    store = ApplicationStore(":memory:")
    store.add("g1", "Co", "R", "u1")
    store.set_ats("g1", "greenhouse")
    text = format_healthcheck(build_healthcheck(store, since="2000-01-01"))
    assert "Verdict" in text
    assert "2000-01-01" in text
