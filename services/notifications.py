"""Notification fan-out (WhatsApp bridge + queue).

`enqueue` inserts a row in ``notification_queue``. `flush_pending` is an
opportunistic dispatcher that posts to the WhatsApp bridge — failures are
recorded but never raised so the calling endpoint is never broken by an
outbound delivery hiccup.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any, Mapping, Optional

logger = logging.getLogger(__name__)

WHATSAPP_BRIDGE_URL = os.getenv("WHATSAPP_BRIDGE_URL", "http://whatsapp-bridge:3000").rstrip("/")


def enqueue(
    conn,
    *,
    channel: str,
    recipient: str,
    template: str,
    payload: Mapping[str, Any] | None = None,
) -> Optional[int]:
    """Persist a notification request. Returns row id, or None on failure."""
    payload_json = json.dumps(payload or {}, ensure_ascii=False, default=str)
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO notification_queue (channel, recipient, template, payload)
                VALUES (%s, %s, %s, %s::jsonb)
                RETURNING id
                """,
                (channel, recipient, template, payload_json),
            )
            row = cur.fetchone()
            if not row:
                return None
            # cursor uses dict_row in this app
            return row["id"] if isinstance(row, dict) else row[0]
    except Exception as exc:  # noqa: BLE001
        logger.warning("enqueue notification failed: %s", exc)
        return None


def flush_pending(conn, *, limit: int = 25) -> int:
    """Try to send pending notifications via the WhatsApp bridge.

    Returns the count of rows that were marked ``sent``. Never raises.
    """
    try:
        import requests  # type: ignore
    except Exception:  # noqa: BLE001
        logger.info("`requests` not available; skipping flush_pending")
        return 0

    sent = 0
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, channel, recipient, template, payload, attempts
                  FROM notification_queue
                 WHERE status = 'pending'
                 ORDER BY id
                 LIMIT %s
                """,
                (limit,),
            )
            rows = cur.fetchall()

        for row in rows:
            row_id = row["id"] if isinstance(row, dict) else row[0]
            channel = row["channel"] if isinstance(row, dict) else row[1]
            recipient = row["recipient"] if isinstance(row, dict) else row[2]
            template = row["template"] if isinstance(row, dict) else row[3]
            payload = row["payload"] if isinstance(row, dict) else row[4]

            if channel != "whatsapp":
                # Other channels not implemented yet; leave pending.
                continue

            try:
                resp = requests.post(
                    f"{WHATSAPP_BRIDGE_URL}/send",
                    json={"to": recipient, "template": template, "payload": payload},
                    timeout=5,
                )
                resp.raise_for_status()
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        UPDATE notification_queue
                           SET status = 'sent', sent_at = NOW(), attempts = attempts + 1
                         WHERE id = %s
                        """,
                        (row_id,),
                    )
                sent += 1
            except Exception as exc:  # noqa: BLE001
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        UPDATE notification_queue
                           SET attempts = attempts + 1,
                               last_error = %s
                         WHERE id = %s
                        """,
                        (str(exc)[:500], row_id),
                    )
    except Exception as exc:  # noqa: BLE001
        logger.warning("flush_pending failed: %s", exc)

    return sent
