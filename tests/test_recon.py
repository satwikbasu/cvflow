import pytest

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
