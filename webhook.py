from __future__ import annotations
import logging
from datetime import datetime

import requests

log = logging.getLogger(__name__)


def send_webhook(
    url: str,
    pinned_titles: list[str],
    timestamp: datetime,
    timeout: float = 10.0,
) -> bool:
    """POST a cycle summary to the configured webhook. Returns True on success.

    The payload is the list of pinned collection titles plus a timestamp. Never
    raises — a webhook failure is logged and swallowed so it cannot abort the
    cycle. A falsy ``url`` is a no-op returning False.
    """
    if not url:
        return False
    payload = {
        "pinned_collections": list(pinned_titles),
        "count": len(pinned_titles),
        "timestamp": timestamp.isoformat(),
    }
    try:
        resp = requests.post(url, json=payload, timeout=timeout)
        resp.raise_for_status()
        log.info("Webhook posted (status %s): %d collections", resp.status_code, len(pinned_titles))
        return True
    except requests.exceptions.RequestException as e:
        log.error("Webhook POST failed: %s", e)
        return False
    except Exception as e:
        log.error("Unexpected webhook error: %s", e)
        return False
