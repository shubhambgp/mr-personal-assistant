"""Image limits, enforced server-side.

These matter because the previous version declared the same limits in a UI
config file that turned out to be shadowed at runtime — so the limits looked set
and were not. Client-side limits are a courtesy; this is the enforcement.
"""

from __future__ import annotations

from app.bot.attachments import MAX_IMAGE_BYTES, MAX_IMAGES_PER_TURN, collect_images

PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 64


def test_accepts_a_supported_image():
    images, skipped = collect_images([("scan.png", "image/png", PNG)])
    assert not skipped
    assert images[0]["name"] == "scan.png"
    assert images[0]["data_url"].startswith("data:image/png;base64,")


def test_rejects_an_unsupported_type():
    images, skipped = collect_images([("notes.pdf", "application/pdf", b"%PDF-1.4")])
    assert not images
    assert "not a supported image" in skipped[0]


def test_falls_back_to_the_extension_when_the_browser_lies():
    images, skipped = collect_images([("scan.JPG", "application/octet-stream", PNG)])
    assert not skipped
    assert images[0]["data_url"].startswith("data:image/jpeg;base64,")


def test_rejects_an_oversized_file():
    images, skipped = collect_images([("big.png", "image/png", b"0" * (MAX_IMAGE_BYTES + 1))])
    assert not images
    assert "too large" in skipped[0]


def test_rejects_an_empty_file():
    images, skipped = collect_images([("empty.png", "image/png", b"")])
    assert not images
    assert "empty" in skipped[0]


def test_enforces_the_per_turn_count_cap():
    uploads = [(f"s{i}.png", "image/png", PNG) for i in range(MAX_IMAGES_PER_TURN + 3)]
    images, skipped = collect_images(uploads)
    assert len(images) == MAX_IMAGES_PER_TURN
    assert len(skipped) == 3
    assert all("limit" in reason for reason in skipped)


def test_one_bad_file_does_not_lose_the_good_ones():
    images, skipped = collect_images(
        [("ok.png", "image/png", PNG), ("bad.txt", "text/plain", b"hi")]
    )
    assert len(images) == 1
    assert len(skipped) == 1


# ---------------------------------------------------------------------------
# audit redaction
# ---------------------------------------------------------------------------


def test_the_audit_log_redacts_addresses_and_mobiles_from_free_text():
    """This log now sits next to a real mailbox.

    It used to hold questions about a rep's own synthetic book. A mail turn's
    answer quotes a real doctor's real words, and the drafts it records were
    addressed to real people — so the rule is applied once, in the writer, rather
    than at each call site where it would eventually be forgotten.
    """
    import json

    from app.bot.audit import redact

    out = redact(
        {
            "question": "reply to dr.sharma@clinic.test and call 9876543210",
            "answer": "Sent to dr.sharma@clinic.test.",
            "drafted": [{"args": {"to": "dr.sharma@clinic.test", "body": "call 9876543210"}}],
            "chair_id": 7100001,
            "latency_ms": 12.5,
        }
    )
    blob = json.dumps(out)
    assert "dr.sharma@clinic.test" not in blob
    assert "9876543210" not in blob
    assert "[email]" in blob and "[mobile]" in blob
    # Non-text fields must survive untouched, or the log stops being useful.
    assert out["chair_id"] == 7100001
    assert out["latency_ms"] == 12.5
