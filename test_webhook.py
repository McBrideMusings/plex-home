from datetime import datetime, timezone
from unittest.mock import patch, MagicMock
import requests

import webhook


def test_posts_payload_and_returns_true():
    ts = datetime(2026, 7, 9, 12, 0, tzinfo=timezone.utc)
    with patch("webhook.requests.post") as post:
        post.return_value = MagicMock(status_code=200)
        ok = webhook.send_webhook("http://hook", ["A", "B"], ts)
    assert ok is True
    args, kwargs = post.call_args
    assert args[0] == "http://hook"
    payload = kwargs["json"]
    assert payload["pinned_collections"] == ["A", "B"]
    assert payload["count"] == 2
    assert payload["timestamp"] == ts.isoformat()


def test_empty_url_is_noop():
    with patch("webhook.requests.post") as post:
        ok = webhook.send_webhook("", ["A"], datetime.now(timezone.utc))
    assert ok is False
    post.assert_not_called()


def test_request_error_swallowed_returns_false():
    with patch("webhook.requests.post", side_effect=requests.exceptions.ConnectionError("refused")):
        ok = webhook.send_webhook("http://hook", ["A"], datetime.now(timezone.utc))
    assert ok is False


def test_http_error_status_returns_false():
    resp = MagicMock()
    resp.raise_for_status.side_effect = requests.exceptions.HTTPError("500")
    with patch("webhook.requests.post", return_value=resp):
        ok = webhook.send_webhook("http://hook", ["A"], datetime.now(timezone.utc))
    assert ok is False


def test_empty_pinned_list_posts_zero_count():
    ts = datetime(2026, 7, 9, tzinfo=timezone.utc)
    with patch("webhook.requests.post") as post:
        post.return_value = MagicMock(status_code=204)
        ok = webhook.send_webhook("http://hook", [], ts)
    assert ok is True
    assert post.call_args.kwargs["json"]["count"] == 0
