"""format_drop_report — group all drops by bucket, chunk to <=4000-char Telegram messages."""

from cvflow.cron import format_drop_report
from cvflow.discovery import DropRecord


def test_empty_returns_no_messages() -> None:
    assert format_drop_report([]) == []


def test_groups_in_bucket_order_with_headers() -> None:
    records = [
        DropRecord("wrong stack", "React Dev @ A", "missing react"),
        DropRecord("too senior", "Lead Eng @ B", "seniority_signal senior"),
        DropRecord("wrong stack", "Vue Dev @ C", "missing vue"),
    ]
    msgs = format_drop_report(records)
    text = "\n".join(msgs)
    # too senior precedes wrong stack (DROP_BUCKET_ORDER)
    assert text.index("too senior") < text.index("wrong stack")
    assert "🚫 Dropped 1 — too senior" in text
    assert "🚫 Dropped 2 — wrong stack" in text
    assert "• Lead Eng @ B — seniority_signal senior" in text
    assert "• React Dev @ A — missing react" in text


def test_every_message_under_4000_chars_and_splits_keep_header() -> None:
    records = [
        DropRecord("wrong stack", f"Job {i} @ Co", "missing react node go") for i in range(400)
    ]
    msgs = format_drop_report(records)
    assert len(msgs) > 1  # 400 rows can't fit one 4000-char message
    assert all(len(m) <= 4000 for m in msgs)
    # every chunk carries the bucket header context
    assert all("wrong stack" in m for m in msgs)
