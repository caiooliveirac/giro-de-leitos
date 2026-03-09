from __future__ import annotations

import json
import os
import time
from datetime import date, datetime
from typing import Any

import psycopg
from psycopg.rows import dict_row

from parser_service import parse_whatsapp_message
from units import normalize_unit_text, resolve_unit_from_text, resolve_unit_name, seed_units

DATABASE_URL = os.getenv("DATABASE_URL", "").strip()


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS registered_units (
    code TEXT PRIMARY KEY,
    canonical_name TEXT NOT NULL UNIQUE,
    aliases JSONB NOT NULL DEFAULT '[]'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS parsed_events (
    id BIGSERIAL PRIMARY KEY,
    source TEXT NOT NULL,
    received_at TIMESTAMPTZ NOT NULL,
    upa_name TEXT,
    reported_upa_name TEXT,
    unit_code TEXT,
    canonical_unit_name TEXT,
    unit_match_confidence DOUBLE PRECISION,
    unit_match_method TEXT,
    unit_matched_alias TEXT,
    raw_text TEXT NOT NULL,
    is_critical BOOLEAN NOT NULL DEFAULT FALSE,
    red_occupied INTEGER,
    red_capacity INTEGER,
    yellow_occupied INTEGER,
    yellow_capacity INTEGER,
    isolation_mode TEXT,
    isolation_total_occupied INTEGER,
    isolation_total_capacity INTEGER,
    isolation_female_occupied INTEGER,
    isolation_female_capacity INTEGER,
    isolation_male_occupied INTEGER,
    isolation_male_capacity INTEGER,
    isolation_pediatric_occupied INTEGER,
    isolation_pediatric_capacity INTEGER,
    has_orthopedist BOOLEAN NOT NULL DEFAULT FALSE,
    has_surgeon BOOLEAN NOT NULL DEFAULT FALSE,
    has_psychiatrist BOOLEAN NOT NULL DEFAULT FALSE,
    payload JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

ALTER TABLE parsed_events ADD COLUMN IF NOT EXISTS reported_upa_name TEXT;
ALTER TABLE parsed_events ADD COLUMN IF NOT EXISTS unit_code TEXT;
ALTER TABLE parsed_events ADD COLUMN IF NOT EXISTS canonical_unit_name TEXT;
ALTER TABLE parsed_events ADD COLUMN IF NOT EXISTS unit_match_confidence DOUBLE PRECISION;
ALTER TABLE parsed_events ADD COLUMN IF NOT EXISTS unit_match_method TEXT;
ALTER TABLE parsed_events ADD COLUMN IF NOT EXISTS unit_matched_alias TEXT;

CREATE INDEX IF NOT EXISTS idx_parsed_events_upa_received_at
    ON parsed_events (upa_name, received_at DESC);

CREATE INDEX IF NOT EXISTS idx_parsed_events_unit_code_received_at
    ON parsed_events (unit_code, received_at DESC);

CREATE TABLE IF NOT EXISTS current_unit_status (
    unit_key TEXT PRIMARY KEY,
    unit_code TEXT,
    canonical_name TEXT,
    displayed_name TEXT NOT NULL,
    source TEXT NOT NULL,
    received_at TIMESTAMPTZ NOT NULL,
    last_event_id BIGINT REFERENCES parsed_events(id) ON DELETE SET NULL,
    is_critical BOOLEAN NOT NULL DEFAULT FALSE,
    red_occupied INTEGER,
    red_capacity INTEGER,
    yellow_occupied INTEGER,
    yellow_capacity INTEGER,
    isolation_mode TEXT,
    isolation_total_occupied INTEGER,
    isolation_total_capacity INTEGER,
    isolation_female_occupied INTEGER,
    isolation_female_capacity INTEGER,
    isolation_male_occupied INTEGER,
    isolation_male_capacity INTEGER,
    isolation_pediatric_occupied INTEGER,
    isolation_pediatric_capacity INTEGER,
    has_orthopedist BOOLEAN NOT NULL DEFAULT FALSE,
    has_surgeon BOOLEAN NOT NULL DEFAULT FALSE,
    has_psychiatrist BOOLEAN NOT NULL DEFAULT FALSE,
    payload JSONB NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_current_unit_status_received_at
    ON current_unit_status (received_at DESC);

CREATE TABLE IF NOT EXISTS alert_events (
    id BIGSERIAL PRIMARY KEY,
    unit_key TEXT NOT NULL,
    unit_code TEXT,
    unit_name TEXT NOT NULL,
    event_id BIGINT REFERENCES parsed_events(id) ON DELETE CASCADE,
    alert_type TEXT NOT NULL,
    severity TEXT NOT NULL,
    title TEXT NOT NULL,
    message TEXT NOT NULL,
    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_alert_events_created_at
    ON alert_events (created_at DESC);

CREATE INDEX IF NOT EXISTS idx_alert_events_unit_key_created_at
    ON alert_events (unit_key, created_at DESC);
"""


def is_database_configured() -> bool:
    return bool(DATABASE_URL)


def _connect() -> psycopg.Connection[Any]:
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL não configurada.")
    return psycopg.connect(DATABASE_URL, row_factory=dict_row)


def _json_default(value: Any) -> str:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return str(value)


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=_json_default)


def init_db() -> None:
    if not DATABASE_URL:
        return

    attempts = 0
    while True:
        attempts += 1
        try:
            with _connect() as conn:
                with conn.cursor() as cur:
                    cur.execute(SCHEMA_SQL)
                    for unit in seed_units():
                        cur.execute(
                            """
                            INSERT INTO registered_units (code, canonical_name, aliases)
                            VALUES (%s, %s, %s::jsonb)
                            ON CONFLICT (code) DO UPDATE SET
                                canonical_name = EXCLUDED.canonical_name,
                                aliases = EXCLUDED.aliases,
                                updated_at = NOW()
                            """,
                            (unit["code"], unit["canonical_name"], _json_dumps(unit["aliases"])),
                        )
                conn.commit()
            return
        except psycopg.OperationalError:
            if attempts >= 15:
                raise
            time.sleep(2)


def _room_values(room: dict[str, Any] | None) -> tuple[int | None, int | None]:
    if not room:
        return None, None
    return room.get("occupied"), room.get("capacity")


def _has_vacancy_from_values(occupied: int | None, capacity: int | None) -> bool:
    return occupied is not None and capacity is not None and capacity > 0 and occupied < capacity


def _status_has_other_vacancy(status: dict[str, Any] | None) -> bool:
    if not status:
        return False
    payload = status.get("payload") if isinstance(status, dict) else None
    if isinstance(payload, dict):
        rooms = ((payload.get("data") or {}).get("rooms") or {}) if isinstance(payload.get("data"), dict) else {}
        other_beds = rooms.get("other_beds") or []
        if any(_has_vacancy_from_values(room.get("occupied"), room.get("capacity")) for room in other_beds if isinstance(room, dict)):
            return True
    candidates = [
        (status.get("isolation_total_occupied"), status.get("isolation_total_capacity")),
        (status.get("isolation_female_occupied"), status.get("isolation_female_capacity")),
        (status.get("isolation_male_occupied"), status.get("isolation_male_capacity")),
    ]
    return any(_has_vacancy_from_values(occupied, capacity) for occupied, capacity in candidates)


def _room_from_payload(status: dict[str, Any] | None, room_key: str) -> dict[str, Any] | None:
    if not status:
        return None
    payload = status.get("payload")
    if not isinstance(payload, dict):
        return None
    data = payload.get("data")
    if not isinstance(data, dict):
        return None
    rooms = data.get("rooms")
    if not isinstance(rooms, dict):
        return None
    room = rooms.get(room_key)
    return room if isinstance(room, dict) else None


def _other_beds_from_payload(status: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not status:
        return []
    payload = status.get("payload")
    if not isinstance(payload, dict):
        return []
    data = payload.get("data")
    if not isinstance(data, dict):
        return []
    rooms = data.get("rooms")
    if not isinstance(rooms, dict):
        return []
    other_beds = rooms.get("other_beds")
    if not isinstance(other_beds, list):
        return []
    return [room for room in other_beds if isinstance(room, dict)]


def _empty_payload_for_unit(canonical_name: str, unit_code: str) -> dict[str, Any]:
    return {
        "type": "upa_update",
        "source": "registry",
        "received_at": None,
        "data": {
            "upa_name": canonical_name,
            "reported_upa_name": canonical_name,
            "unit_code": unit_code,
            "unit_match": {
                "unit_code": unit_code,
                "canonical_name": canonical_name,
                "matched_alias": canonical_name,
                "confidence": 1.0,
                "method": "registry",
            },
            "is_critical": False,
            "warnings": ["Nenhum giro recente recebido para esta unidade."],
            "parsed_at": None,
            "reported_at": None,
            "raw_text": "",
            "corridor_patients": [],
            "specialists": {
                "has_orthopedist": False,
                "has_surgeon": False,
                "has_psychiatrist": False,
            },
            "rooms": {
                "red_room": None,
                "yellow_room": None,
                "yellow_male": None,
                "yellow_female": None,
                "isolation_total": None,
                "isolation_female": None,
                "isolation_male": None,
                "isolation_pediatric": None,
                "isolation_mode": None,
                "other_beds": [],
            },
        },
    }


def _merge_enriched_payload(payload: dict[str, Any]) -> dict[str, Any]:
    data = payload.get("data")
    if not isinstance(data, dict):
        return payload

    raw_text = data.get("raw_text")
    if not isinstance(raw_text, str) or not raw_text.strip():
        return payload

    reparsed = parse_whatsapp_message(raw_text)
    rooms = data.get("rooms") if isinstance(data.get("rooms"), dict) else {}
    reparsed_rooms = reparsed.get("rooms") if isinstance(reparsed.get("rooms"), dict) else {}
    specialists = data.get("specialists") if isinstance(data.get("specialists"), dict) else {}
    reparsed_specialists = reparsed.get("specialists") if isinstance(reparsed.get("specialists"), dict) else {}

    merged_data = dict(data)
    merged_data["rooms"] = {
        **rooms,
        **reparsed_rooms,
    }
    merged_data["specialists"] = {
        **specialists,
        **reparsed_specialists,
    }
    merged_data["corridor_patients"] = reparsed.get("corridor_patients") or data.get("corridor_patients") or []
    merged_data["warnings"] = reparsed.get("warnings") or data.get("warnings") or []
    merged_data["is_critical"] = bool(reparsed.get("is_critical", data.get("is_critical")))
    merged_data["parsed_at"] = reparsed.get("parsed_at") or data.get("parsed_at")
    merged_data["reported_at"] = data.get("reported_at") or reparsed.get("reported_at")
    merged_data["reported_upa_name"] = data.get("reported_upa_name") or reparsed.get("upa_name")

    merged_payload = dict(payload)
    merged_payload["data"] = merged_data
    return merged_payload


def _enrich_status_row_from_payload(row: dict[str, Any]) -> dict[str, Any]:
    payload = row.get("payload")
    if not isinstance(payload, dict):
        return row

    enriched_payload = _merge_enriched_payload(payload)
    row["payload"] = enriched_payload

    data = enriched_payload.get("data") if isinstance(enriched_payload.get("data"), dict) else {}
    rooms = data.get("rooms") if isinstance(data.get("rooms"), dict) else {}
    specialists = data.get("specialists") if isinstance(data.get("specialists"), dict) else {}

    red_room = rooms.get("red_room") if isinstance(rooms.get("red_room"), dict) else None
    yellow_room = rooms.get("yellow_room") if isinstance(rooms.get("yellow_room"), dict) else None
    isolation_total = rooms.get("isolation_total") if isinstance(rooms.get("isolation_total"), dict) else None
    isolation_female = rooms.get("isolation_female") if isinstance(rooms.get("isolation_female"), dict) else None
    isolation_male = rooms.get("isolation_male") if isinstance(rooms.get("isolation_male"), dict) else None
    isolation_pediatric = rooms.get("isolation_pediatric") if isinstance(rooms.get("isolation_pediatric"), dict) else None

    row["red_occupied"], row["red_capacity"] = _room_values(red_room)
    row["yellow_occupied"], row["yellow_capacity"] = _room_values(yellow_room)
    row["isolation_total_occupied"], row["isolation_total_capacity"] = _room_values(isolation_total)
    row["isolation_female_occupied"], row["isolation_female_capacity"] = _room_values(isolation_female)
    row["isolation_male_occupied"], row["isolation_male_capacity"] = _room_values(isolation_male)
    row["isolation_pediatric_occupied"], row["isolation_pediatric_capacity"] = _room_values(isolation_pediatric)
    row["isolation_mode"] = rooms.get("isolation_mode")
    row["has_orthopedist"] = bool(specialists.get("has_orthopedist"))
    row["has_surgeon"] = bool(specialists.get("has_surgeon"))
    row["has_psychiatrist"] = bool(specialists.get("has_psychiatrist"))
    row["is_critical"] = bool(data.get("is_critical"))
    return row


def _insert_alert(
    cur: psycopg.Cursor[Any],
    *,
    unit_key: str,
    unit_code: str | None,
    unit_name: str,
    event_id: int | None,
    alert_type: str,
    severity: str,
    title: str,
    message: str,
    payload: dict[str, Any],
) -> None:
    cur.execute(
        """
        INSERT INTO alert_events (
            unit_key,
            unit_code,
            unit_name,
            event_id,
            alert_type,
            severity,
            title,
            message,
            payload
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)
        """,
        (unit_key, unit_code, unit_name, event_id, alert_type, severity, title, message, _json_dumps(payload)),
    )


def _emit_transition_alerts(
    cur: psycopg.Cursor[Any],
    *,
    previous_status: dict[str, Any] | None,
    unit_key: str,
    unit_code: str | None,
    unit_name: str,
    event_id: int | None,
    red_occupied: int | None,
    red_capacity: int | None,
    yellow_occupied: int | None,
    yellow_capacity: int | None,
    isolation_total_occupied: int | None,
    isolation_total_capacity: int | None,
    isolation_female_occupied: int | None,
    isolation_female_capacity: int | None,
    isolation_male_occupied: int | None,
    isolation_male_capacity: int | None,
    has_orthopedist: bool,
    has_psychiatrist: bool,
    current_rooms: dict[str, Any] | None = None,
) -> None:
    if not previous_status:
        return

    previous_red = _has_vacancy_from_values(previous_status.get("red_occupied"), previous_status.get("red_capacity"))
    current_red = _has_vacancy_from_values(red_occupied, red_capacity)
    if not previous_red and current_red:
        _insert_alert(
            cur,
            unit_key=unit_key,
            unit_code=unit_code,
            unit_name=unit_name,
            event_id=event_id,
            alert_type="red_vacancy_opened",
            severity="critical",
            title="Vaga aberta na vermelha",
            message=f"{unit_name} passou a ter vaga na Sala Vermelha ({red_occupied:02d}/{red_capacity:02d}).",
            payload={"previous": {"occupied": previous_status.get("red_occupied"), "capacity": previous_status.get("red_capacity")}, "current": {"occupied": red_occupied, "capacity": red_capacity}},
        )

    previous_yellow_male = _room_from_payload(previous_status, "yellow_male")
    current_yellow_male = (current_rooms or {}).get("yellow_male") if isinstance(current_rooms, dict) else None
    if not _has_vacancy_from_values(
        previous_yellow_male.get("occupied") if previous_yellow_male else None,
        previous_yellow_male.get("capacity") if previous_yellow_male else None,
    ) and _has_vacancy_from_values(
        current_yellow_male.get("occupied") if isinstance(current_yellow_male, dict) else None,
        current_yellow_male.get("capacity") if isinstance(current_yellow_male, dict) else None,
    ):
        _insert_alert(
            cur,
            unit_key=unit_key,
            unit_code=unit_code,
            unit_name=unit_name,
            event_id=event_id,
            alert_type="yellow_male_vacancy_opened",
            severity="high",
            title="Vaga masculina na amarela/observação",
            message=f"{unit_name} passou a ter vaga masculina na Amarela/Observação ({current_yellow_male['ratio']}).",
            payload={"previous": previous_yellow_male, "current": current_yellow_male},
        )

    previous_yellow_female = _room_from_payload(previous_status, "yellow_female")
    current_yellow_female = (current_rooms or {}).get("yellow_female") if isinstance(current_rooms, dict) else None
    if not _has_vacancy_from_values(
        previous_yellow_female.get("occupied") if previous_yellow_female else None,
        previous_yellow_female.get("capacity") if previous_yellow_female else None,
    ) and _has_vacancy_from_values(
        current_yellow_female.get("occupied") if isinstance(current_yellow_female, dict) else None,
        current_yellow_female.get("capacity") if isinstance(current_yellow_female, dict) else None,
    ):
        _insert_alert(
            cur,
            unit_key=unit_key,
            unit_code=unit_code,
            unit_name=unit_name,
            event_id=event_id,
            alert_type="yellow_female_vacancy_opened",
            severity="high",
            title="Vaga feminina na amarela/observação",
            message=f"{unit_name} passou a ter vaga feminina na Amarela/Observação ({current_yellow_female['ratio']}).",
            payload={"previous": previous_yellow_female, "current": current_yellow_female},
        )

    previous_yellow = _has_vacancy_from_values(previous_status.get("yellow_occupied"), previous_status.get("yellow_capacity"))
    current_yellow = _has_vacancy_from_values(yellow_occupied, yellow_capacity)
    if not previous_yellow and current_yellow and not (current_yellow_male or current_yellow_female):
        _insert_alert(
            cur,
            unit_key=unit_key,
            unit_code=unit_code,
            unit_name=unit_name,
            event_id=event_id,
            alert_type="yellow_vacancy_opened",
            severity="high",
            title="Vaga aberta na amarela/observação",
            message=f"{unit_name} passou a ter vaga na Amarela/Observação ({yellow_occupied:02d}/{yellow_capacity:02d}).",
            payload={"previous": {"occupied": previous_status.get("yellow_occupied"), "capacity": previous_status.get("yellow_capacity")}, "current": {"occupied": yellow_occupied, "capacity": yellow_capacity}},
        )

    previous_other = any(_has_vacancy_from_values(room.get("occupied"), room.get("capacity")) for room in _other_beds_from_payload(previous_status))
    current_other_beds = [room for room in ((current_rooms or {}).get("other_beds") or []) if isinstance(room, dict)] if isinstance(current_rooms, dict) else []
    current_other = any(_has_vacancy_from_values(room.get("occupied"), room.get("capacity")) for room in current_other_beds)
    if not previous_other and current_other:
        available_rooms = [room for room in current_other_beds if _has_vacancy_from_values(room.get("occupied"), room.get("capacity"))]
        room_descriptions = ", ".join(f"{room.get('label', 'leito')} {room.get('ratio', 'n/i')}" for room in available_rooms) or "outros leitos"
        _insert_alert(
            cur,
            unit_key=unit_key,
            unit_code=unit_code,
            unit_name=unit_name,
            event_id=event_id,
            alert_type="other_vacancy_opened",
            severity="medium",
            title="Outro leito disponível",
            message=f"{unit_name} passou a ter vaga em leitos de internamento/apoio: {room_descriptions}.",
            payload={"previous": _other_beds_from_payload(previous_status), "current": current_other_beds},
        )

    previous_orthopedist = bool(previous_status.get("has_orthopedist"))
    if previous_orthopedist != has_orthopedist:
        _insert_alert(
            cur,
            unit_key=unit_key,
            unit_code=unit_code,
            unit_name=unit_name,
            event_id=event_id,
            alert_type="orthopedist_changed",
            severity="critical" if not has_orthopedist else "high",
            title="Mudança em ortopedia",
            message=f"{unit_name} agora está {'COM' if has_orthopedist else 'SEM'} ortopedista.",
            payload={"previous": previous_orthopedist, "current": has_orthopedist},
        )

    previous_psychiatrist = bool(previous_status.get("has_psychiatrist"))
    if previous_psychiatrist != has_psychiatrist:
        _insert_alert(
            cur,
            unit_key=unit_key,
            unit_code=unit_code,
            unit_name=unit_name,
            event_id=event_id,
            alert_type="psychiatrist_changed",
            severity="medium" if has_psychiatrist else "low",
            title="Mudança em psiquiatria",
            message=f"{unit_name} agora está {'COM' if has_psychiatrist else 'SEM'} psiquiatria.",
            payload={"previous": previous_psychiatrist, "current": has_psychiatrist},
        )


def save_event(event: dict[str, Any]) -> dict[str, Any] | None:
    """Salva evento e retorna info de anomalias detectadas (ou None)."""
    if not DATABASE_URL:
        return None

    data = event.get("data", {})
    rooms = data.get("rooms", {})
    specialists = data.get("specialists", {})
    unit_match = data.get("unit_match") or {}
    unit_code = data.get("unit_code")
    canonical_unit_name = data.get("upa_name") if unit_code else None
    reported_upa_name = data.get("reported_upa_name") or data.get("upa_name")
    unit_key = unit_code or f"raw:{normalize_unit_text(reported_upa_name or '')}"
    displayed_name = canonical_unit_name or reported_upa_name or str(event.get("source") or "unidade-desconhecida")

    red_occupied, red_capacity = _room_values(rooms.get("red_room"))
    yellow_occupied, yellow_capacity = _room_values(rooms.get("yellow_room"))
    isolation_total_occupied, isolation_total_capacity = _room_values(rooms.get("isolation_total"))
    isolation_female_occupied, isolation_female_capacity = _room_values(rooms.get("isolation_female"))
    isolation_male_occupied, isolation_male_capacity = _room_values(rooms.get("isolation_male"))
    isolation_pediatric_occupied, isolation_pediatric_capacity = _room_values(rooms.get("isolation_pediatric"))

    with _connect() as conn:
        with conn.cursor() as cur:
            previous_status: dict[str, Any] | None = None
            if unit_code and displayed_name:
                cur.execute("SELECT * FROM current_unit_status WHERE unit_key = %s", (unit_key,))
                previous_status = cur.fetchone()

            cur.execute(
                """
                INSERT INTO parsed_events (
                    source,
                    received_at,
                    upa_name,
                    reported_upa_name,
                    unit_code,
                    canonical_unit_name,
                    unit_match_confidence,
                    unit_match_method,
                    unit_matched_alias,
                    raw_text,
                    is_critical,
                    red_occupied,
                    red_capacity,
                    yellow_occupied,
                    yellow_capacity,
                    isolation_mode,
                    isolation_total_occupied,
                    isolation_total_capacity,
                    isolation_female_occupied,
                    isolation_female_capacity,
                    isolation_male_occupied,
                    isolation_male_capacity,
                    isolation_pediatric_occupied,
                    isolation_pediatric_capacity,
                    has_orthopedist,
                    has_surgeon,
                    has_psychiatrist,
                    payload
                ) VALUES (
                    %(source)s,
                    %(received_at)s,
                    %(upa_name)s,
                    %(reported_upa_name)s,
                    %(unit_code)s,
                    %(canonical_unit_name)s,
                    %(unit_match_confidence)s,
                    %(unit_match_method)s,
                    %(unit_matched_alias)s,
                    %(raw_text)s,
                    %(is_critical)s,
                    %(red_occupied)s,
                    %(red_capacity)s,
                    %(yellow_occupied)s,
                    %(yellow_capacity)s,
                    %(isolation_mode)s,
                    %(isolation_total_occupied)s,
                    %(isolation_total_capacity)s,
                    %(isolation_female_occupied)s,
                    %(isolation_female_capacity)s,
                    %(isolation_male_occupied)s,
                    %(isolation_male_capacity)s,
                    %(isolation_pediatric_occupied)s,
                    %(isolation_pediatric_capacity)s,
                    %(has_orthopedist)s,
                    %(has_surgeon)s,
                    %(has_psychiatrist)s,
                    %(payload)s::jsonb
                )
                RETURNING id
                """,
                {
                    "source": event.get("source"),
                    "received_at": event.get("received_at"),
                    "upa_name": data.get("upa_name"),
                    "reported_upa_name": reported_upa_name,
                    "unit_code": unit_code,
                    "canonical_unit_name": canonical_unit_name,
                    "unit_match_confidence": unit_match.get("confidence"),
                    "unit_match_method": unit_match.get("method"),
                    "unit_matched_alias": unit_match.get("matched_alias"),
                    "raw_text": data.get("raw_text"),
                    "is_critical": bool(data.get("is_critical")),
                    "red_occupied": red_occupied,
                    "red_capacity": red_capacity,
                    "yellow_occupied": yellow_occupied,
                    "yellow_capacity": yellow_capacity,
                    "isolation_mode": rooms.get("isolation_mode"),
                    "isolation_total_occupied": isolation_total_occupied,
                    "isolation_total_capacity": isolation_total_capacity,
                    "isolation_female_occupied": isolation_female_occupied,
                    "isolation_female_capacity": isolation_female_capacity,
                    "isolation_male_occupied": isolation_male_occupied,
                    "isolation_male_capacity": isolation_male_capacity,
                    "isolation_pediatric_occupied": isolation_pediatric_occupied,
                    "isolation_pediatric_capacity": isolation_pediatric_capacity,
                    "has_orthopedist": bool(specialists.get("has_orthopedist")),
                    "has_surgeon": bool(specialists.get("has_surgeon")),
                    "has_psychiatrist": bool(specialists.get("has_psychiatrist")),
                    "payload": _json_dumps(event),
                },
            )
            event_id_row = cur.fetchone()
            event_id = event_id_row["id"] if event_id_row else None

            if unit_code and displayed_name:
                cur.execute(
                    """
                    INSERT INTO current_unit_status (
                        unit_key,
                        unit_code,
                        canonical_name,
                        displayed_name,
                        source,
                        received_at,
                        last_event_id,
                        is_critical,
                        red_occupied,
                        red_capacity,
                        yellow_occupied,
                        yellow_capacity,
                        isolation_mode,
                        isolation_total_occupied,
                        isolation_total_capacity,
                        isolation_female_occupied,
                        isolation_female_capacity,
                        isolation_male_occupied,
                        isolation_male_capacity,
                        isolation_pediatric_occupied,
                        isolation_pediatric_capacity,
                        has_orthopedist,
                        has_surgeon,
                        has_psychiatrist,
                        payload,
                        updated_at
                    ) VALUES (
                        %(unit_key)s,
                        %(unit_code)s,
                        %(canonical_name)s,
                        %(displayed_name)s,
                        %(source)s,
                        %(received_at)s,
                        %(last_event_id)s,
                        %(is_critical)s,
                        %(red_occupied)s,
                        %(red_capacity)s,
                        %(yellow_occupied)s,
                        %(yellow_capacity)s,
                        %(isolation_mode)s,
                        %(isolation_total_occupied)s,
                        %(isolation_total_capacity)s,
                        %(isolation_female_occupied)s,
                        %(isolation_female_capacity)s,
                        %(isolation_male_occupied)s,
                        %(isolation_male_capacity)s,
                        %(isolation_pediatric_occupied)s,
                        %(isolation_pediatric_capacity)s,
                        %(has_orthopedist)s,
                        %(has_surgeon)s,
                        %(has_psychiatrist)s,
                        %(payload)s::jsonb,
                        NOW()
                    )
                    ON CONFLICT (unit_key) DO UPDATE SET
                        unit_code = EXCLUDED.unit_code,
                        canonical_name = EXCLUDED.canonical_name,
                        displayed_name = EXCLUDED.displayed_name,
                        source = EXCLUDED.source,
                        received_at = EXCLUDED.received_at,
                        last_event_id = EXCLUDED.last_event_id,
                        is_critical = EXCLUDED.is_critical,
                        red_occupied = EXCLUDED.red_occupied,
                        red_capacity = EXCLUDED.red_capacity,
                        yellow_occupied = EXCLUDED.yellow_occupied,
                        yellow_capacity = EXCLUDED.yellow_capacity,
                        isolation_mode = EXCLUDED.isolation_mode,
                        isolation_total_occupied = EXCLUDED.isolation_total_occupied,
                        isolation_total_capacity = EXCLUDED.isolation_total_capacity,
                        isolation_female_occupied = EXCLUDED.isolation_female_occupied,
                        isolation_female_capacity = EXCLUDED.isolation_female_capacity,
                        isolation_male_occupied = EXCLUDED.isolation_male_occupied,
                        isolation_male_capacity = EXCLUDED.isolation_male_capacity,
                        isolation_pediatric_occupied = EXCLUDED.isolation_pediatric_occupied,
                        isolation_pediatric_capacity = EXCLUDED.isolation_pediatric_capacity,
                        has_orthopedist = EXCLUDED.has_orthopedist,
                        has_surgeon = EXCLUDED.has_surgeon,
                        has_psychiatrist = EXCLUDED.has_psychiatrist,
                        payload = EXCLUDED.payload,
                        updated_at = NOW()
                    """,
                    {
                        "unit_key": unit_key,
                        "unit_code": unit_code,
                        "canonical_name": canonical_unit_name,
                        "displayed_name": displayed_name,
                        "source": event.get("source"),
                        "received_at": event.get("received_at"),
                        "last_event_id": event_id,
                        "is_critical": bool(data.get("is_critical")),
                        "red_occupied": red_occupied,
                        "red_capacity": red_capacity,
                        "yellow_occupied": yellow_occupied,
                        "yellow_capacity": yellow_capacity,
                        "isolation_mode": rooms.get("isolation_mode"),
                        "isolation_total_occupied": isolation_total_occupied,
                        "isolation_total_capacity": isolation_total_capacity,
                        "isolation_female_occupied": isolation_female_occupied,
                        "isolation_female_capacity": isolation_female_capacity,
                        "isolation_male_occupied": isolation_male_occupied,
                        "isolation_male_capacity": isolation_male_capacity,
                        "isolation_pediatric_occupied": isolation_pediatric_occupied,
                        "isolation_pediatric_capacity": isolation_pediatric_capacity,
                        "has_orthopedist": bool(specialists.get("has_orthopedist")),
                        "has_surgeon": bool(specialists.get("has_surgeon")),
                        "has_psychiatrist": bool(specialists.get("has_psychiatrist")),
                        "payload": _json_dumps(event),
                    },
                )

                _emit_transition_alerts(
                    cur,
                    previous_status=previous_status,
                    unit_key=unit_key,
                    unit_code=unit_code,
                    unit_name=displayed_name,
                    event_id=event_id,
                    red_occupied=red_occupied,
                    red_capacity=red_capacity,
                    yellow_occupied=yellow_occupied,
                    yellow_capacity=yellow_capacity,
                    isolation_total_occupied=isolation_total_occupied,
                    isolation_total_capacity=isolation_total_capacity,
                    isolation_female_occupied=isolation_female_occupied,
                    isolation_female_capacity=isolation_female_capacity,
                    isolation_male_occupied=isolation_male_occupied,
                    isolation_male_capacity=isolation_male_capacity,
                    has_orthopedist=bool(specialists.get("has_orthopedist")),
                    has_psychiatrist=bool(specialists.get("has_psychiatrist")),
                    current_rooms=rooms,
                )

            # Detectar regressão temporal
            save_result: dict[str, Any] | None = None
            if previous_status and unit_code:
                prev_received = previous_status.get("received_at")
                new_received = event.get("received_at")
                if prev_received and new_received:
                    prev_str = str(prev_received)
                    new_str = str(new_received)
                    if new_str < prev_str:
                        save_result = {
                            "time_regression": True,
                            "unit_code": unit_code,
                            "unit_name": displayed_name,
                            "previous_received_at": prev_str,
                            "new_received_at": new_str,
                            "previous_event_id": previous_status.get("last_event_id"),
                            "new_event_id": event_id,
                        }

        conn.commit()
    return save_result


def get_latest_events(limit: int = 50) -> list[dict[str, Any]]:
    if not DATABASE_URL:
        return []

    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, source, received_at, upa_name, reported_upa_name, unit_code, canonical_unit_name, is_critical, payload
                FROM parsed_events
                ORDER BY received_at DESC
                LIMIT %s
                """,
                (limit,),
            )
            rows = cur.fetchall()

    for row in rows:
        row["payload"] = row["payload"] if isinstance(row["payload"], dict) else json.loads(row["payload"])
    return rows


def get_registered_units() -> list[dict[str, Any]]:
    if not DATABASE_URL:
        return seed_units()

    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT code, canonical_name, aliases
                FROM registered_units
                ORDER BY canonical_name ASC
                """
            )
            rows = cur.fetchall()

    for row in rows:
        row["aliases"] = row["aliases"] if isinstance(row["aliases"], list) else json.loads(row["aliases"])
    return rows


def get_recent_alerts(limit: int = 50) -> list[dict[str, Any]]:
    if not DATABASE_URL:
        return []

    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, unit_key, unit_code, unit_name, event_id, alert_type, severity, title, message, payload, created_at
                FROM alert_events
                ORDER BY created_at DESC
                LIMIT %s
                """,
                (limit,),
            )
            rows = cur.fetchall()

    for row in rows:
        row["payload"] = row["payload"] if isinstance(row["payload"], dict) else json.loads(row["payload"])
    return rows


def get_parsed_event(event_id: int) -> dict[str, Any] | None:
    if not DATABASE_URL:
        return None

    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, source, received_at, raw_text, payload
                FROM parsed_events
                WHERE id = %s
                """,
                (event_id,),
            )
            row = cur.fetchone()

    if not row:
        return None
    row["payload"] = row["payload"] if isinstance(row["payload"], dict) else json.loads(row["payload"])
    return row


def get_pending_unit_confirmations(limit: int = 20) -> list[dict[str, Any]]:
    if not DATABASE_URL:
        return []

    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, source, received_at, upa_name, reported_upa_name, payload
                FROM parsed_events
                WHERE unit_code IS NULL
                ORDER BY received_at DESC
                LIMIT %s
                """,
                (limit,),
            )
            rows = cur.fetchall()

    pending_rows: list[dict[str, Any]] = []
    for row in rows:
        payload = row["payload"] if isinstance(row["payload"], dict) else json.loads(row["payload"])
        data = payload.get("data", {}) if isinstance(payload, dict) else {}
        raw_text = data.get("raw_text") or ""
        normalized_raw_text = raw_text.strip().casefold()
        if normalized_raw_text.startswith("/"):
            continue

        reported_upa_name = row.get("reported_upa_name") or row.get("upa_name")
        resolved = resolve_unit_name(reported_upa_name) or resolve_unit_from_text(raw_text)
        if resolved:
            continue

        rooms = data.get("rooms", {}) if isinstance(data, dict) else {}
        pending_rows.append(
            {
                "id": row.get("id"),
                "source": row.get("source"),
                "received_at": row.get("received_at"),
                "reported_upa_name": reported_upa_name,
                "warnings": data.get("warnings") or [],
                "raw_text": raw_text,
                "red_room": rooms.get("red_room") if isinstance(rooms.get("red_room"), dict) else None,
                "yellow_room": rooms.get("yellow_room") if isinstance(rooms.get("yellow_room"), dict) else None,
            }
        )

    return pending_rows


def resolve_pending_unit_confirmation(event_id: int, event: dict[str, Any]) -> dict[str, Any] | None:
    if not DATABASE_URL:
        return None

    data = event.get("data", {}) if isinstance(event, dict) else {}
    if not isinstance(data, dict) or not data.get("unit_code"):
        return None

    rooms = data.get("rooms", {}) if isinstance(data.get("rooms"), dict) else {}
    specialists = data.get("specialists", {}) if isinstance(data.get("specialists"), dict) else {}
    unit_match = data.get("unit_match") or {}
    unit_code = data.get("unit_code")
    canonical_unit_name = data.get("upa_name") if unit_code else None
    reported_upa_name = data.get("reported_upa_name") or data.get("upa_name")
    unit_key = unit_code
    displayed_name = canonical_unit_name or reported_upa_name or str(event.get("source") or "unidade-desconhecida")

    red_occupied, red_capacity = _room_values(rooms.get("red_room"))
    yellow_occupied, yellow_capacity = _room_values(rooms.get("yellow_room"))
    isolation_total_occupied, isolation_total_capacity = _room_values(rooms.get("isolation_total"))
    isolation_female_occupied, isolation_female_capacity = _room_values(rooms.get("isolation_female"))
    isolation_male_occupied, isolation_male_capacity = _room_values(rooms.get("isolation_male"))
    isolation_pediatric_occupied, isolation_pediatric_capacity = _room_values(rooms.get("isolation_pediatric"))

    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM current_unit_status WHERE unit_key = %s", (unit_key,))
            previous_status = cur.fetchone()

            cur.execute(
                """
                UPDATE parsed_events
                SET upa_name = %(upa_name)s,
                    reported_upa_name = %(reported_upa_name)s,
                    unit_code = %(unit_code)s,
                    canonical_unit_name = %(canonical_unit_name)s,
                    unit_match_confidence = %(unit_match_confidence)s,
                    unit_match_method = %(unit_match_method)s,
                    unit_matched_alias = %(unit_matched_alias)s,
                    is_critical = %(is_critical)s,
                    red_occupied = %(red_occupied)s,
                    red_capacity = %(red_capacity)s,
                    yellow_occupied = %(yellow_occupied)s,
                    yellow_capacity = %(yellow_capacity)s,
                    isolation_mode = %(isolation_mode)s,
                    isolation_total_occupied = %(isolation_total_occupied)s,
                    isolation_total_capacity = %(isolation_total_capacity)s,
                    isolation_female_occupied = %(isolation_female_occupied)s,
                    isolation_female_capacity = %(isolation_female_capacity)s,
                    isolation_male_occupied = %(isolation_male_occupied)s,
                    isolation_male_capacity = %(isolation_male_capacity)s,
                    isolation_pediatric_occupied = %(isolation_pediatric_occupied)s,
                    isolation_pediatric_capacity = %(isolation_pediatric_capacity)s,
                    has_orthopedist = %(has_orthopedist)s,
                    has_surgeon = %(has_surgeon)s,
                    has_psychiatrist = %(has_psychiatrist)s,
                    payload = %(payload)s::jsonb
                WHERE id = %(event_id)s
                """,
                {
                    "event_id": event_id,
                    "upa_name": data.get("upa_name"),
                    "reported_upa_name": reported_upa_name,
                    "unit_code": unit_code,
                    "canonical_unit_name": canonical_unit_name,
                    "unit_match_confidence": unit_match.get("confidence"),
                    "unit_match_method": unit_match.get("method"),
                    "unit_matched_alias": unit_match.get("matched_alias"),
                    "is_critical": bool(data.get("is_critical")),
                    "red_occupied": red_occupied,
                    "red_capacity": red_capacity,
                    "yellow_occupied": yellow_occupied,
                    "yellow_capacity": yellow_capacity,
                    "isolation_mode": rooms.get("isolation_mode"),
                    "isolation_total_occupied": isolation_total_occupied,
                    "isolation_total_capacity": isolation_total_capacity,
                    "isolation_female_occupied": isolation_female_occupied,
                    "isolation_female_capacity": isolation_female_capacity,
                    "isolation_male_occupied": isolation_male_occupied,
                    "isolation_male_capacity": isolation_male_capacity,
                    "isolation_pediatric_occupied": isolation_pediatric_occupied,
                    "isolation_pediatric_capacity": isolation_pediatric_capacity,
                    "has_orthopedist": bool(specialists.get("has_orthopedist")),
                    "has_surgeon": bool(specialists.get("has_surgeon")),
                    "has_psychiatrist": bool(specialists.get("has_psychiatrist")),
                    "payload": _json_dumps(event),
                },
            )

            cur.execute(
                """
                INSERT INTO current_unit_status (
                    unit_key, unit_code, canonical_name, displayed_name, source, received_at, last_event_id,
                    is_critical, red_occupied, red_capacity, yellow_occupied, yellow_capacity, isolation_mode,
                    isolation_total_occupied, isolation_total_capacity, isolation_female_occupied, isolation_female_capacity,
                    isolation_male_occupied, isolation_male_capacity, isolation_pediatric_occupied, isolation_pediatric_capacity,
                    has_orthopedist, has_surgeon, has_psychiatrist, payload, updated_at
                ) VALUES (
                    %(unit_key)s, %(unit_code)s, %(canonical_name)s, %(displayed_name)s, %(source)s, %(received_at)s, %(last_event_id)s,
                    %(is_critical)s, %(red_occupied)s, %(red_capacity)s, %(yellow_occupied)s, %(yellow_capacity)s, %(isolation_mode)s,
                    %(isolation_total_occupied)s, %(isolation_total_capacity)s, %(isolation_female_occupied)s, %(isolation_female_capacity)s,
                    %(isolation_male_occupied)s, %(isolation_male_capacity)s, %(isolation_pediatric_occupied)s, %(isolation_pediatric_capacity)s,
                    %(has_orthopedist)s, %(has_surgeon)s, %(has_psychiatrist)s, %(payload)s::jsonb, NOW()
                )
                ON CONFLICT (unit_key) DO UPDATE SET
                    unit_code = EXCLUDED.unit_code,
                    canonical_name = EXCLUDED.canonical_name,
                    displayed_name = EXCLUDED.displayed_name,
                    source = EXCLUDED.source,
                    received_at = EXCLUDED.received_at,
                    last_event_id = EXCLUDED.last_event_id,
                    is_critical = EXCLUDED.is_critical,
                    red_occupied = EXCLUDED.red_occupied,
                    red_capacity = EXCLUDED.red_capacity,
                    yellow_occupied = EXCLUDED.yellow_occupied,
                    yellow_capacity = EXCLUDED.yellow_capacity,
                    isolation_mode = EXCLUDED.isolation_mode,
                    isolation_total_occupied = EXCLUDED.isolation_total_occupied,
                    isolation_total_capacity = EXCLUDED.isolation_total_capacity,
                    isolation_female_occupied = EXCLUDED.isolation_female_occupied,
                    isolation_female_capacity = EXCLUDED.isolation_female_capacity,
                    isolation_male_occupied = EXCLUDED.isolation_male_occupied,
                    isolation_male_capacity = EXCLUDED.isolation_male_capacity,
                    isolation_pediatric_occupied = EXCLUDED.isolation_pediatric_occupied,
                    isolation_pediatric_capacity = EXCLUDED.isolation_pediatric_capacity,
                    has_orthopedist = EXCLUDED.has_orthopedist,
                    has_surgeon = EXCLUDED.has_surgeon,
                    has_psychiatrist = EXCLUDED.has_psychiatrist,
                    payload = EXCLUDED.payload,
                    updated_at = NOW()
                """,
                {
                    "unit_key": unit_key,
                    "unit_code": unit_code,
                    "canonical_name": canonical_unit_name,
                    "displayed_name": displayed_name,
                    "source": event.get("source"),
                    "received_at": event.get("received_at"),
                    "last_event_id": event_id,
                    "is_critical": bool(data.get("is_critical")),
                    "red_occupied": red_occupied,
                    "red_capacity": red_capacity,
                    "yellow_occupied": yellow_occupied,
                    "yellow_capacity": yellow_capacity,
                    "isolation_mode": rooms.get("isolation_mode"),
                    "isolation_total_occupied": isolation_total_occupied,
                    "isolation_total_capacity": isolation_total_capacity,
                    "isolation_female_occupied": isolation_female_occupied,
                    "isolation_female_capacity": isolation_female_capacity,
                    "isolation_male_occupied": isolation_male_occupied,
                    "isolation_male_capacity": isolation_male_capacity,
                    "isolation_pediatric_occupied": isolation_pediatric_occupied,
                    "isolation_pediatric_capacity": isolation_pediatric_capacity,
                    "has_orthopedist": bool(specialists.get("has_orthopedist")),
                    "has_surgeon": bool(specialists.get("has_surgeon")),
                    "has_psychiatrist": bool(specialists.get("has_psychiatrist")),
                    "payload": _json_dumps(event),
                },
            )

            _emit_transition_alerts(
                cur,
                previous_status=previous_status,
                unit_key=unit_key,
                unit_code=unit_code,
                unit_name=displayed_name,
                event_id=event_id,
                red_occupied=red_occupied,
                red_capacity=red_capacity,
                yellow_occupied=yellow_occupied,
                yellow_capacity=yellow_capacity,
                isolation_total_occupied=isolation_total_occupied,
                isolation_total_capacity=isolation_total_capacity,
                isolation_female_occupied=isolation_female_occupied,
                isolation_female_capacity=isolation_female_capacity,
                isolation_male_occupied=isolation_male_occupied,
                isolation_male_capacity=isolation_male_capacity,
                has_orthopedist=bool(specialists.get("has_orthopedist")),
                has_psychiatrist=bool(specialists.get("has_psychiatrist")),
                current_rooms=rooms,
            )
        conn.commit()

    rows = get_latest_status_by_unit()
    for current_row in rows:
        if current_row.get("unit_key") == unit_key:
            return current_row
    return None


def get_latest_status_by_unit() -> list[dict[str, Any]]:
    if not DATABASE_URL:
        return []

    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    unit_key,
                    unit_code,
                    canonical_name,
                    displayed_name,
                    source,
                    received_at,
                    updated_at,
                    last_event_id AS id,
                    canonical_name AS upa_name,
                    is_critical,
                    red_occupied,
                    red_capacity,
                    yellow_occupied,
                    yellow_capacity,
                    isolation_mode,
                    isolation_total_occupied,
                    isolation_total_capacity,
                    isolation_female_occupied,
                    isolation_female_capacity,
                    isolation_male_occupied,
                    isolation_male_capacity,
                    isolation_pediatric_occupied,
                    isolation_pediatric_capacity,
                    has_orthopedist,
                    has_surgeon,
                    has_psychiatrist,
                    payload
                FROM current_unit_status
                ORDER BY displayed_name ASC, received_at DESC
                """
            )
            rows = cur.fetchall()
            cur.execute(
                """
                SELECT code, canonical_name
                FROM registered_units
                ORDER BY canonical_name ASC
                """
            )
            registered_units = cur.fetchall()

    normalized_rows: list[dict[str, Any]] = []
    for row in rows:
        row["payload"] = row["payload"] if isinstance(row["payload"], dict) else json.loads(row["payload"])
        row = _enrich_status_row_from_payload(row)

        if not row.get("unit_code"):
            payload = row.get("payload", {})
            data = payload.get("data", {}) if isinstance(payload, dict) else {}
            candidate_name = row.get("displayed_name") or data.get("reported_upa_name") or data.get("upa_name")
            resolved = resolve_unit_name(candidate_name)
            if resolved:
                row["unit_code"] = resolved["unit_code"]
                row["canonical_name"] = resolved["canonical_name"]
                row["displayed_name"] = resolved["canonical_name"]
                row["upa_name"] = resolved["canonical_name"]

        normalized_rows.append(row)

    deduped_by_unit: dict[str, dict[str, Any]] = {}
    fallback_rows: list[dict[str, Any]] = []

    for row in normalized_rows:
        effective_key = row.get("unit_code") or row.get("unit_key")
        if not effective_key:
            fallback_rows.append(row)
            continue

        existing = deduped_by_unit.get(effective_key)
        if not existing or str(row.get("received_at") or "") > str(existing.get("received_at") or ""):
            deduped_by_unit[effective_key] = row

    for registered in registered_units:
        code = registered.get("code")
        canonical_name = registered.get("canonical_name")
        if code in deduped_by_unit:
            continue
        deduped_by_unit[code] = {
            "unit_key": code,
            "unit_code": code,
            "canonical_name": canonical_name,
            "displayed_name": canonical_name,
            "source": "registry",
            "received_at": None,
            "id": 0,
            "upa_name": canonical_name,
            "is_critical": False,
            "red_occupied": None,
            "red_capacity": None,
            "yellow_occupied": None,
            "yellow_capacity": None,
            "isolation_mode": None,
            "isolation_total_occupied": None,
            "isolation_total_capacity": None,
            "isolation_female_occupied": None,
            "isolation_female_capacity": None,
            "isolation_male_occupied": None,
            "isolation_male_capacity": None,
            "isolation_pediatric_occupied": None,
            "isolation_pediatric_capacity": None,
            "has_orthopedist": False,
            "has_surgeon": False,
            "has_psychiatrist": False,
            "payload": _empty_payload_for_unit(canonical_name, code),
        }

    result = list(deduped_by_unit.values()) + fallback_rows
    result.sort(key=lambda item: ((item.get("displayed_name") or "").casefold(), str(item.get("received_at") or "")), reverse=False)
    return result


def update_unit_reported_at(unit_key: str, reported_at_iso: str) -> dict[str, Any] | None:
    if not DATABASE_URL:
        return None

    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM current_unit_status WHERE unit_key = %s", (unit_key,))
            row = cur.fetchone()
            if not row:
                return None

            payload = row["payload"] if isinstance(row["payload"], dict) else json.loads(row["payload"])
            payload["received_at"] = reported_at_iso
            data = payload.setdefault("data", {})
            data["reported_at"] = reported_at_iso

            cur.execute(
                """
                UPDATE current_unit_status
                SET received_at = %s,
                    payload = %s::jsonb,
                    updated_at = NOW()
                WHERE unit_key = %s
                """,
                (reported_at_iso, _json_dumps(payload), unit_key),
            )

            last_event_id = row.get("last_event_id")
            if last_event_id:
                cur.execute("SELECT payload FROM parsed_events WHERE id = %s", (last_event_id,))
                event_row = cur.fetchone()
                if event_row:
                    event_payload = event_row["payload"] if isinstance(event_row["payload"], dict) else json.loads(event_row["payload"])
                    event_payload["received_at"] = reported_at_iso
                    event_data = event_payload.setdefault("data", {})
                    event_data["reported_at"] = reported_at_iso
                    cur.execute(
                        """
                        UPDATE parsed_events
                        SET received_at = %s,
                            payload = %s::jsonb
                        WHERE id = %s
                        """,
                        (reported_at_iso, _json_dumps(event_payload), last_event_id),
                    )

        conn.commit()

    rows = get_latest_status_by_unit()
    for current_row in rows:
        if current_row.get("unit_key") == unit_key:
            return current_row
    return None


# ── Admin CRUD ───────────────────────────────────────────────────────────

def get_event_detail(event_id: int) -> dict[str, Any] | None:
    """Retorna todos os campos de um parsed_event pelo id."""
    if not DATABASE_URL:
        return None
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, source, received_at, upa_name, reported_upa_name,
                       unit_code, canonical_unit_name, raw_text,
                       is_critical, red_occupied, red_capacity,
                       yellow_occupied, yellow_capacity,
                       isolation_mode,
                       isolation_total_occupied, isolation_total_capacity,
                       isolation_female_occupied, isolation_female_capacity,
                       isolation_male_occupied, isolation_male_capacity,
                       isolation_pediatric_occupied, isolation_pediatric_capacity,
                       has_orthopedist, has_surgeon, has_psychiatrist,
                       payload, created_at
                FROM parsed_events
                WHERE id = %s
                """,
                (event_id,),
            )
            row = cur.fetchone()
    if not row:
        return None
    row["payload"] = row["payload"] if isinstance(row["payload"], dict) else json.loads(row["payload"])
    return row


def delete_event(event_id: int) -> bool:
    """
    Apaga um parsed_event e, se ele for o último de alguma unidade
    em current_unit_status, faz rollback para o evento anterior.
    Retorna True se deletou, False se não encontrou.
    """
    if not DATABASE_URL:
        return False

    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT unit_code FROM parsed_events WHERE id = %s", (event_id,))
            row = cur.fetchone()
            if not row:
                return False

            unit_code = row.get("unit_code")

            # Verificar se esse evento é o "atual" em current_unit_status
            if unit_code:
                cur.execute(
                    "SELECT unit_key FROM current_unit_status WHERE last_event_id = %s",
                    (event_id,),
                )
                status_row = cur.fetchone()
            else:
                status_row = None

            # Deletar o evento (cascade apaga alert_events referenciados)
            cur.execute("DELETE FROM parsed_events WHERE id = %s", (event_id,))

            # Se era o evento atual, fazer rollback para o anterior
            if status_row and unit_code:
                unit_key = status_row["unit_key"]
                cur.execute(
                    """
                    SELECT id, source, received_at, payload
                    FROM parsed_events
                    WHERE unit_code = %s
                    ORDER BY received_at DESC
                    LIMIT 1
                    """,
                    (unit_code,),
                )
                prev = cur.fetchone()
                if prev:
                    prev_payload = prev["payload"] if isinstance(prev["payload"], dict) else json.loads(prev["payload"])
                    prev_data = prev_payload.get("data", {}) if isinstance(prev_payload, dict) else {}
                    prev_rooms = prev_data.get("rooms", {}) if isinstance(prev_data, dict) else {}
                    prev_specialists = prev_data.get("specialists", {}) if isinstance(prev_data, dict) else {}
                    pr_occ, pr_cap = _room_values(prev_rooms.get("red_room"))
                    py_occ, py_cap = _room_values(prev_rooms.get("yellow_room"))
                    pit_occ, pit_cap = _room_values(prev_rooms.get("isolation_total"))
                    pif_occ, pif_cap = _room_values(prev_rooms.get("isolation_female"))
                    pim_occ, pim_cap = _room_values(prev_rooms.get("isolation_male"))
                    pip_occ, pip_cap = _room_values(prev_rooms.get("isolation_pediatric"))
                    cur.execute(
                        """
                        UPDATE current_unit_status SET
                            source = %s,
                            received_at = %s,
                            last_event_id = %s,
                            is_critical = %s,
                            red_occupied = %s, red_capacity = %s,
                            yellow_occupied = %s, yellow_capacity = %s,
                            isolation_mode = %s,
                            isolation_total_occupied = %s, isolation_total_capacity = %s,
                            isolation_female_occupied = %s, isolation_female_capacity = %s,
                            isolation_male_occupied = %s, isolation_male_capacity = %s,
                            isolation_pediatric_occupied = %s, isolation_pediatric_capacity = %s,
                            has_orthopedist = %s, has_surgeon = %s, has_psychiatrist = %s,
                            payload = %s::jsonb,
                            updated_at = NOW()
                        WHERE unit_key = %s
                        """,
                        (
                            prev.get("source"),
                            prev.get("received_at"),
                            prev["id"],
                            bool(prev_data.get("is_critical")),
                            pr_occ, pr_cap,
                            py_occ, py_cap,
                            prev_rooms.get("isolation_mode"),
                            pit_occ, pit_cap,
                            pif_occ, pif_cap,
                            pim_occ, pim_cap,
                            pip_occ, pip_cap,
                            bool(prev_specialists.get("has_orthopedist")),
                            bool(prev_specialists.get("has_surgeon")),
                            bool(prev_specialists.get("has_psychiatrist")),
                            _json_dumps(prev_payload),
                            unit_key,
                        ),
                    )
                else:
                    cur.execute("DELETE FROM current_unit_status WHERE unit_key = %s", (unit_key,))

        conn.commit()
    return True


def admin_update_event(
    event_id: int,
    *,
    upa_name: str | None = None,
    unit_code: str | None = None,
    reported_at: str | None = None,
    red_occupied: int | None = None,
    red_capacity: int | None = None,
    yellow_occupied: int | None = None,
    yellow_capacity: int | None = None,
    yellow_male_occupied: int | None = None,
    yellow_male_capacity: int | None = None,
    yellow_female_occupied: int | None = None,
    yellow_female_capacity: int | None = None,
    isolation_mode: str | None = None,
    isolation_total_occupied: int | None = None,
    isolation_total_capacity: int | None = None,
    isolation_female_occupied: int | None = None,
    isolation_female_capacity: int | None = None,
    isolation_male_occupied: int | None = None,
    isolation_male_capacity: int | None = None,
    isolation_pediatric_occupied: int | None = None,
    isolation_pediatric_capacity: int | None = None,
    has_orthopedist: bool | None = None,
    has_surgeon: bool | None = None,
    has_psychiatrist: bool | None = None,
) -> dict[str, Any] | None:
    """
    Atualiza campos editáveis de um evento (UPA, horário, quartos, especialistas).
    Propaga mudanças para current_unit_status se for o evento ativo.
    """
    if not DATABASE_URL:
        return None

    def _make_room_obj(occ: int | None, cap: int | None, existing: dict | None = None) -> dict:
        """Cria ou atualiza um room dict mantendo ratio/has_capacity/is_over_capacity consistentes."""
        obj = dict(existing) if existing else {}
        if occ is not None:
            obj["occupied"] = occ
        if cap is not None:
            obj["capacity"] = cap
        o = obj.get("occupied", 0)
        c = obj.get("capacity", 0)
        obj["ratio"] = f"{str(o).zfill(2)}/{str(c).zfill(2)}"
        obj["has_capacity"] = c > 0 and o < c
        obj["is_over_capacity"] = o > c if c > 0 else False
        return obj

    # Mapa de campos com coluna DB → caminho no payload JSON
    ROOM_FIELDS = {
        "red_occupied": ("rooms", "red_room", "occupied"),
        "red_capacity": ("rooms", "red_room", "capacity"),
        "yellow_occupied": ("rooms", "yellow_room", "occupied"),
        "yellow_capacity": ("rooms", "yellow_room", "capacity"),
        "isolation_total_occupied": ("rooms", "isolation_total", "occupied"),
        "isolation_total_capacity": ("rooms", "isolation_total", "capacity"),
        "isolation_female_occupied": ("rooms", "isolation_female", "occupied"),
        "isolation_female_capacity": ("rooms", "isolation_female", "capacity"),
        "isolation_male_occupied": ("rooms", "isolation_male", "occupied"),
        "isolation_male_capacity": ("rooms", "isolation_male", "capacity"),
        "isolation_pediatric_occupied": ("rooms", "isolation_pediatric", "occupied"),
        "isolation_pediatric_capacity": ("rooms", "isolation_pediatric", "capacity"),
    }
    # yellow_male/female ficam somente no payload JSON (não têm coluna DB)
    PAYLOAD_ONLY_ROOM_FIELDS = {
        "yellow_male_occupied": ("rooms", "yellow_male", "occupied"),
        "yellow_male_capacity": ("rooms", "yellow_male", "capacity"),
        "yellow_female_occupied": ("rooms", "yellow_female", "occupied"),
        "yellow_female_capacity": ("rooms", "yellow_female", "capacity"),
    }
    SPECIALIST_FIELDS = ("has_orthopedist", "has_surgeon", "has_psychiatrist")

    local_vars = {
        "red_occupied": red_occupied, "red_capacity": red_capacity,
        "yellow_occupied": yellow_occupied, "yellow_capacity": yellow_capacity,
        "yellow_male_occupied": yellow_male_occupied, "yellow_male_capacity": yellow_male_capacity,
        "yellow_female_occupied": yellow_female_occupied, "yellow_female_capacity": yellow_female_capacity,
        "isolation_total_occupied": isolation_total_occupied, "isolation_total_capacity": isolation_total_capacity,
        "isolation_female_occupied": isolation_female_occupied, "isolation_female_capacity": isolation_female_capacity,
        "isolation_male_occupied": isolation_male_occupied, "isolation_male_capacity": isolation_male_capacity,
        "isolation_pediatric_occupied": isolation_pediatric_occupied, "isolation_pediatric_capacity": isolation_pediatric_capacity,
        "has_orthopedist": has_orthopedist, "has_surgeon": has_surgeon, "has_psychiatrist": has_psychiatrist,
        "isolation_mode": isolation_mode,
    }

    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, unit_code, payload FROM parsed_events WHERE id = %s",
                (event_id,),
            )
            row = cur.fetchone()
            if not row:
                return None

            payload = row["payload"] if isinstance(row["payload"], dict) else json.loads(row["payload"])
            data = payload.get("data", {}) if isinstance(payload, dict) else {}
            old_unit_code = row.get("unit_code")

            sets: list[str] = []
            params: list[Any] = []

            if upa_name is not None:
                data["upa_name"] = upa_name
                sets.append("upa_name = %s")
                params.append(upa_name)
                sets.append("canonical_unit_name = %s")
                params.append(upa_name)

            if unit_code is not None:
                data["unit_code"] = unit_code
                sets.append("unit_code = %s")
                params.append(unit_code)

            if reported_at is not None:
                data["reported_at"] = reported_at
                payload["received_at"] = reported_at
                sets.append("received_at = %s")
                params.append(reported_at)

            # Campos de quartos (com coluna DB)
            rooms = data.setdefault("rooms", {})
            # Coletar quais room_keys foram tocados para recalcular ratio
            touched_rooms: set[str] = set()
            for field_name, (section, room_key, val_key) in ROOM_FIELDS.items():
                val = local_vars[field_name]
                if val is not None:
                    sets.append(f"{field_name} = %s")
                    params.append(val)
                    room_obj = rooms.setdefault(room_key, {})
                    room_obj[val_key] = val
                    touched_rooms.add(room_key)

            # Campos de quartos somente no payload (yellow_male, yellow_female)
            for field_name, (section, room_key, val_key) in PAYLOAD_ONLY_ROOM_FIELDS.items():
                val = local_vars[field_name]
                if val is not None:
                    room_obj = rooms.setdefault(room_key, {})
                    room_obj[val_key] = val
                    touched_rooms.add(room_key)

            # Recalcular ratio/has_capacity/is_over_capacity para rooms tocados
            for room_key in touched_rooms:
                room_obj = rooms.get(room_key, {})
                rooms[room_key] = _make_room_obj(None, None, room_obj)

            # isolation_mode
            if isolation_mode is not None:
                sets.append("isolation_mode = %s")
                params.append(isolation_mode)
                rooms["isolation_mode"] = isolation_mode

            # Especialistas (booleanos)
            specialists = data.setdefault("specialists", {})
            for field_name in SPECIALIST_FIELDS:
                val = local_vars[field_name]
                if val is not None:
                    sets.append(f"{field_name} = %s")
                    params.append(val)
                    specialists[field_name] = val

            payload["data"] = data
            sets.append("payload = %s::jsonb")
            params.append(_json_dumps(payload))
            params.append(event_id)

            cur.execute(
                f"UPDATE parsed_events SET {', '.join(sets)} WHERE id = %s",
                params,
            )

            # Propagar para current_unit_status se for o evento ativo
            effective_unit_code = unit_code if unit_code is not None else old_unit_code
            if effective_unit_code:
                cur.execute(
                    "SELECT unit_key FROM current_unit_status WHERE last_event_id = %s",
                    (event_id,),
                )
                status_row = cur.fetchone()
                if status_row:
                    status_sets: list[str] = []
                    status_params: list[Any] = []
                    if upa_name is not None:
                        status_sets.append("canonical_name = %s")
                        status_params.append(upa_name)
                        status_sets.append("displayed_name = %s")
                        status_params.append(upa_name)
                    if unit_code is not None:
                        status_sets.append("unit_code = %s")
                        status_params.append(unit_code)
                    if reported_at is not None:
                        status_sets.append("received_at = %s")
                        status_params.append(reported_at)
                    for field_name in list(ROOM_FIELDS.keys()) + ["isolation_mode"] + list(SPECIALIST_FIELDS):
                        val = local_vars[field_name]
                        if val is not None:
                            status_sets.append(f"{field_name} = %s")
                            status_params.append(val)
                    status_sets.append("payload = %s::jsonb")
                    status_params.append(_json_dumps(payload))
                    status_sets.append("updated_at = NOW()")
                    status_params.append(status_row["unit_key"])
                    cur.execute(
                        f"UPDATE current_unit_status SET {', '.join(status_sets)} WHERE unit_key = %s",
                        status_params,
                    )

        conn.commit()

    return get_event_detail(event_id)
