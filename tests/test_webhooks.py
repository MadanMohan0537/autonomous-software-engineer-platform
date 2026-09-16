import hashlib
import hmac

import pytest

from ase.webhooks import InvalidSignature, decode_issue_event, verify_signature


def test_signature_and_issue_decoding() -> None:
    body = b'{"ok":true}'
    signature = "sha256=" + hmac.new(b"secret", body, hashlib.sha256).hexdigest()
    verify_signature(body, signature, "secret")
    event = decode_issue_event(
        {
            "action": "opened",
            "repository": {"full_name": "owner/repo"},
            "issue": {
                "number": 2,
                "title": "Bug",
                "body": None,
                "labels": [{"name": "ase:ready"}],
            },
        }
    )
    assert event.repository == "owner/repo"
    assert event.labels == ["ase:ready"]


def test_invalid_signature_fails_closed() -> None:
    with pytest.raises(InvalidSignature):
        verify_signature(b"body", "sha256=wrong", "secret")
