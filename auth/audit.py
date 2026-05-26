"""Audit log helper."""

from __future__ import annotations

import json
from typing import Any, Optional
from uuid import UUID


def _serialize(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, default=str)


def record_audit(
    conn,
    *,
    actor_user_id: Optional[UUID | str] = None,
    session_id: Optional[UUID | str] = None,
    device_id: Optional[str] = None,
    action: str,
    entity_type: str,
    entity_id: Optional[str] = None,
    previous_value: Any = None,
    new_value: Any = None,
    client_ip: Optional[str] = None,
    user_agent: Optional[str] = None,
) -> None:
    """Insert a row into ``audit_log``. Never raises — audit must not break flow."""
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO audit_log
                    (actor_user_id, session_id, device_id, action, entity_type,
                     entity_id, previous_value, new_value, client_ip, user_agent)
                VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb, %s, %s)
                """,
                (
                    str(actor_user_id) if actor_user_id else None,
                    str(session_id) if session_id else None,
                    device_id,
                    action,
                    entity_type,
                    str(entity_id) if entity_id is not None else None,
                    _serialize(previous_value),
                    _serialize(new_value),
                    client_ip,
                    user_agent,
                ),
            )
    except Exception:  # noqa: BLE001
        # Audit failures are swallowed to avoid masking the underlying op.
        pass
