from __future__ import annotations

import json
import os
import time
from datetime import date, datetime, timezone
from typing import Any

import psycopg
from psycopg.rows import dict_row

from parser_service import parse_whatsapp_message
from services.whatsapp_alerts import normalize_phone
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

-- Autoria do giro: quem mandou a mensagem (ver
-- migrations/009_parsed_events_sender.sql). Opcionais: ingestão manual e
-- adapter antigo continuam gravando NULL.
ALTER TABLE parsed_events ADD COLUMN IF NOT EXISTS sender_phone TEXT;
ALTER TABLE parsed_events ADD COLUMN IF NOT EXISTS sender_name TEXT;

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

CREATE TABLE IF NOT EXISTS telegram_alert_chats (
    chat_id BIGINT PRIMARY KEY,
    chat_type TEXT NOT NULL,
    title TEXT,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    last_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Cooldown por unidade do alerta de silêncio por WhatsApp (ver
-- migrations/008_whatsapp_alert_state.sql).
CREATE TABLE IF NOT EXISTS whatsapp_alert_state (
    unit_key TEXT PRIMARY KEY,
    last_sent_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Quem posta o giro de cada unidade, derivado da ingestão (ver
-- migrations/010_unit_contacts.sql).
CREATE TABLE IF NOT EXISTS unit_contacts (
    unit_code TEXT NOT NULL,
    sender_phone TEXT NOT NULL,
    sender_name TEXT,
    giro_count BIGINT NOT NULL DEFAULT 1,
    first_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (unit_code, sender_phone)
);
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
            apply_migrations()
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
) -> dict[str, Any]:
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
        RETURNING id, created_at
        """,
        (unit_key, unit_code, unit_name, event_id, alert_type, severity, title, message, _json_dumps(payload)),
    )
    inserted = cur.fetchone() or {}
    return {
        "id": inserted.get("id"),
        "unit_key": unit_key,
        "unit_code": unit_code,
        "unit_name": unit_name,
        "event_id": event_id,
        "alert_type": alert_type,
        "severity": severity,
        "title": title,
        "message": message,
        "payload": payload,
        "created_at": inserted.get("created_at"),
    }


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
    has_surgeon: bool,
    has_psychiatrist: bool,
    current_rooms: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    if not previous_status:
        return []

    alerts: list[dict[str, Any]] = []
    room_config = (
        ("red_room", "Sala Vermelha", "red", "critical"),
        ("yellow_room", "Amarela/Observação", "yellow", "high"),
        ("yellow_male", "Amarela/Observação masculina", "yellow_male", "high"),
        ("yellow_female", "Amarela/Observação feminina", "yellow_female", "high"),
        ("isolation_total", "Isolamento", "isolation", "medium"),
        ("isolation_female", "Isolamento feminino", "isolation_female", "medium"),
        ("isolation_male", "Isolamento masculino", "isolation_male", "medium"),
        ("isolation_pediatric", "Isolamento pediátrico", "isolation_pediatric", "medium"),
    )

    current_room_map = dict(current_rooms or {}) if isinstance(current_rooms, dict) else {}
    current_room_map["red_room"] = {"occupied": red_occupied, "capacity": red_capacity}
    current_room_map["yellow_room"] = {"occupied": yellow_occupied, "capacity": yellow_capacity}
    if isolation_total_capacity is not None:
        current_room_map["isolation_total"] = {"occupied": isolation_total_occupied, "capacity": isolation_total_capacity}
    if isolation_female_capacity is not None:
        current_room_map["isolation_female"] = {"occupied": isolation_female_occupied, "capacity": isolation_female_capacity}
    if isolation_male_capacity is not None:
        current_room_map["isolation_male"] = {"occupied": isolation_male_occupied, "capacity": isolation_male_capacity}

    def vacancy_state(room: Any) -> bool | None:
        if not isinstance(room, dict):
            return None
        occupied = room.get("occupied")
        capacity = room.get("capacity")
        if not isinstance(occupied, int) or not isinstance(capacity, int):
            return None
        return capacity > occupied

    def ratio(room: dict[str, Any]) -> str:
        return str(room.get("ratio") or f"{room.get('occupied', 0):02d}/{room.get('capacity', 0):02d}")

    for room_key, room_label, type_prefix, severity in room_config:
        if room_key == "yellow_room" and (current_room_map.get("yellow_male") or current_room_map.get("yellow_female")):
            continue
        if room_key == "isolation_total" and current_room_map.get("isolation_mode") == "split":
            continue
        previous_room = _room_from_payload(previous_status, room_key)
        current_room = current_room_map.get(room_key)
        previous_vacancy = vacancy_state(previous_room)
        current_vacancy = vacancy_state(current_room)
        if previous_vacancy is None or current_vacancy is None or previous_vacancy == current_vacancy:
            continue
        opened = current_vacancy
        alerts.append(
            _insert_alert(
                cur,
                unit_key=unit_key,
                unit_code=unit_code,
                unit_name=unit_name,
                event_id=event_id,
                alert_type=f"{type_prefix}_vacancy_{'opened' if opened else 'closed'}",
                severity=severity if opened else ("critical" if type_prefix == "red" else "medium"),
                title=f"Vaga {'aberta' if opened else 'encerrada'} — {room_label}",
                message=f"{unit_name} passou a ficar {'COM' if opened else 'SEM'} vaga em {room_label} ({ratio(current_room)}).",
                payload={"previous": previous_room, "current": current_room},
            )
        )

    previous_other_by_label = {
        normalize_unit_text(str(room.get("label") or "leito")): room
        for room in _other_beds_from_payload(previous_status)
    }
    current_other_beds = [
        room for room in (current_room_map.get("other_beds") or []) if isinstance(room, dict)
    ]
    for current_room in current_other_beds:
        label = str(current_room.get("label") or "Leito de internamento/apoio")
        key = normalize_unit_text(label)
        previous_room = previous_other_by_label.get(key)
        previous_vacancy = vacancy_state(previous_room)
        current_vacancy = vacancy_state(current_room)
        if previous_vacancy is None or current_vacancy is None or previous_vacancy == current_vacancy:
            continue
        opened = current_vacancy
        alerts.append(
            _insert_alert(
                cur,
                unit_key=unit_key,
                unit_code=unit_code,
                unit_name=unit_name,
                event_id=event_id,
                alert_type=f"other_vacancy_{'opened' if opened else 'closed'}",
                severity="medium",
                title=f"Vaga {'aberta' if opened else 'encerrada'} — {label}",
                message=f"{unit_name} passou a ficar {'COM' if opened else 'SEM'} vaga em {label} ({ratio(current_room)}).",
                payload={"previous": previous_room, "current": current_room},
            )
        )

    specialist_config = (
        ("has_orthopedist", has_orthopedist, "ortopedista", "orthopedist"),
        ("has_surgeon", has_surgeon, "cirurgião", "surgeon"),
        ("has_psychiatrist", has_psychiatrist, "psiquiatra", "psychiatrist"),
    )
    for field, current_value, label, type_prefix in specialist_config:
        previous_value = bool(previous_status.get(field))
        if previous_value == current_value:
            continue
        alerts.append(
            _insert_alert(
                cur,
                unit_key=unit_key,
                unit_code=unit_code,
                unit_name=unit_name,
                event_id=event_id,
                alert_type=f"{type_prefix}_changed",
                severity="high" if current_value else "critical",
                title=f"Mudança — {label.capitalize()}",
                message=f"{unit_name} agora está {'COM' if current_value else 'SEM'} {label}.",
                payload={"previous": previous_value, "current": current_value},
            )
        )

    return alerts


def _touch_unit_contact(
    cur: psycopg.Cursor[Any],
    unit_code: str,
    sender_phone: str,
    sender_name: str | None,
) -> None:
    """Registra/incrementa quem postou este giro nesta unidade.

    Roda dentro da MESMA transação do evento: contato aprendido sem giro
    gravado (ou o contrário) seria uma contagem que não bate com o histórico.

    O nome é atualizado só quando vem preenchido — o push_name do WhatsApp
    falta com frequência e não pode apagar o que já sabíamos.
    """
    cur.execute(
        """
        INSERT INTO unit_contacts (unit_code, sender_phone, sender_name, giro_count)
        VALUES (%s, %s, %s, 1)
        ON CONFLICT (unit_code, sender_phone) DO UPDATE SET
            giro_count = unit_contacts.giro_count + 1,
            sender_name = COALESCE(EXCLUDED.sender_name, unit_contacts.sender_name),
            last_seen_at = NOW()
        """,
        (unit_code, sender_phone, sender_name),
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
    # Autoria: só existe quando o adapter conseguiu extrair o remetente do
    # webhook. Ausente = NULL nas colunas e nenhum contato aprendido — nunca
    # um erro (a maior parte do histórico não tem autor).
    sender_phone = normalize_phone(data.get("sender_phone"))
    sender_name = (data.get("sender_name") or "").strip() or None

    red_occupied, red_capacity = _room_values(rooms.get("red_room"))
    yellow_occupied, yellow_capacity = _room_values(rooms.get("yellow_room"))
    isolation_total_occupied, isolation_total_capacity = _room_values(rooms.get("isolation_total"))
    isolation_female_occupied, isolation_female_capacity = _room_values(rooms.get("isolation_female"))
    isolation_male_occupied, isolation_male_capacity = _room_values(rooms.get("isolation_male"))
    isolation_pediatric_occupied, isolation_pediatric_capacity = _room_values(rooms.get("isolation_pediatric"))

    with _connect() as conn:
        with conn.cursor() as cur:
            previous_status: dict[str, Any] | None = None
            transition_alerts: list[dict[str, Any]] = []
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
                    sender_phone,
                    sender_name,
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
                    %(sender_phone)s,
                    %(sender_name)s,
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
                    "sender_phone": sender_phone or None,
                    "sender_name": sender_name,
                    "payload": _json_dumps(event),
                },
            )
            event_id_row = cur.fetchone()
            event_id = event_id_row["id"] if event_id_row else None

            if unit_code and sender_phone:
                _touch_unit_contact(cur, unit_code, sender_phone, sender_name)

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

                transition_alerts = _emit_transition_alerts(
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
                    has_surgeon=bool(specialists.get("has_surgeon")),
                    has_psychiatrist=bool(specialists.get("has_psychiatrist")),
                    current_rooms=rooms,
                )

            # Detectar regressão temporal
            save_result: dict[str, Any] | None = {"transition_alerts": transition_alerts} if transition_alerts else None
            if previous_status and unit_code:
                prev_received = previous_status.get("received_at")
                new_received = event.get("received_at")
                if prev_received and new_received:
                    prev_str = str(prev_received)
                    new_str = str(new_received)
                    if new_str < prev_str:
                        save_result = {
                            **(save_result or {}),
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


# ---------------------------------------------------------------------------
# Dashboard / indicadores (agregação do histórico já preservado)
# ---------------------------------------------------------------------------
def _period_clause(period: str, column: str) -> str:
    """Fragmento SQL seguro (period é whitelist) filtrando ``column`` por janela."""
    if period == "today":
        return f" AND {column} >= date_trunc('day', now())"
    if period == "7d":
        return f" AND {column} >= now() - interval '7 days'"
    if period == "30d":
        return f" AND {column} >= now() - interval '30 days'"
    return ""  # all


def _round1(value: Any) -> float | None:
    return round(float(value), 1) if value is not None else None


def get_dashboard_metrics(period: str = "all", unit_code: str | None = None) -> dict[str, Any]:
    """Indicadores agregados dos giros. Deriva de parsed_events + audit_log,
    sem tabela nova. Filtra por janela de tempo e (opcional) por unidade."""
    if period not in ("today", "7d", "30d", "all"):
        period = "all"
    if not DATABASE_URL:
        return {"period": period, "unit": unit_code, "totals": {}, "by_unit": []}

    p_created = _period_clause(period, "created_at")
    p_received = _period_clause(period, "received_at")

    with _connect() as conn, conn.cursor() as cur:
        # Mapa unidade: id -> (code, name) e code -> name
        cur.execute("SELECT id, code, canonical_name FROM units")
        units = cur.fetchall()
        id_to_unit = {str(u["id"]): u for u in units}
        code_to_id = {u["code"]: str(u["id"]) for u in units}
        code_to_name = {u["code"]: u["canonical_name"] for u in units}

        unit_id = code_to_id.get(unit_code) if unit_code else None
        # Cláusulas/params por filtro de unidade
        bed_unit_sql, bed_unit_params = ("", [])
        pe_unit_sql, pe_unit_params = ("", [])
        xray_unit_sql, xray_unit_params = ("", [])
        cs_unit_sql, cs_unit_params = ("", [])
        if unit_code:
            bed_unit_sql, bed_unit_params = (" AND entity_id LIKE %s", [f"{unit_id}:%"])
            pe_unit_sql, pe_unit_params = (" AND unit_code = %s", [unit_code])
            xray_unit_sql, xray_unit_params = (" AND entity_id = %s", [f"{unit_id}:xray"])
            cs_unit_sql, cs_unit_params = (" AND unit_code = %s", [unit_code])

        # --- Eventos de leito (audit_log): óbitos/altas/transferências ---
        cur.execute(
            f"""
            SELECT
              COUNT(*) FILTER (WHERE action='bed.death')     AS deaths,
              COUNT(*) FILTER (WHERE action='bed.discharge') AS discharges,
              COUNT(*) FILTER (WHERE action='bed.transfer')  AS transfers
            FROM audit_log
            WHERE entity_type='bed'{bed_unit_sql}{p_created}
            """,
            bed_unit_params,
        )
        bed = cur.fetchone() or {}

        # por unidade
        cur.execute(
            f"""
            SELECT split_part(entity_id, ':', 1) AS unit_id,
              COUNT(*) FILTER (WHERE action='bed.death')     AS deaths,
              COUNT(*) FILTER (WHERE action='bed.discharge') AS discharges,
              COUNT(*) FILTER (WHERE action='bed.transfer')  AS transfers
            FROM audit_log
            WHERE entity_type='bed'{bed_unit_sql}{p_created}
            GROUP BY 1
            """,
            bed_unit_params,
        )
        bed_by_unit = {r["unit_id"]: r for r in cur.fetchall()}

        # --- Pacientes distintos que estiveram na sala vermelha ---
        # Fonte real: lista de pacientes do giro (parser do WhatsApp), em
        # payload.data.rooms.red_room.patients[].sigla. Conta siglas distintas
        # por unidade no período (aproxima "pacientes diferentes que passaram").
        red_patients_sql = """
            FROM parsed_events pe
            CROSS JOIN LATERAL jsonb_array_elements(
                CASE WHEN jsonb_typeof(pe.payload->'data'->'rooms'->'red_room'->'patients')='array'
                     THEN pe.payload->'data'->'rooms'->'red_room'->'patients'
                     ELSE '[]'::jsonb END) AS pat
            WHERE COALESCE(pat->>'sigla','') <> ''
        """
        cur.execute(
            f"SELECT COUNT(DISTINCT (pe.unit_code, pat->>'sigla')) AS red_patients "
            f"{red_patients_sql}{pe_unit_sql}{p_received}",
            pe_unit_params,
        )
        red_total = cur.fetchone() or {}

        cur.execute(
            f"SELECT pe.unit_code, COUNT(DISTINCT pat->>'sigla') AS red_patients "
            f"{red_patients_sql}{pe_unit_sql}{p_received} GROUP BY pe.unit_code",
            pe_unit_params,
        )
        red_by_unit = {r["unit_code"]: r for r in cur.fetchall()}

        # --- Ocupação (parsed_events): amarela / isolamento + nº de giros ---
        cur.execute(
            f"""
            SELECT AVG(yellow_occupied) AS yellow_avg, MAX(yellow_occupied) AS yellow_max,
                   AVG(isolation_total_occupied) AS iso_avg, COUNT(*) AS giros
            FROM parsed_events
            WHERE TRUE{pe_unit_sql}{p_received}
            """,
            pe_unit_params,
        )
        occ = cur.fetchone() or {}

        cur.execute(
            f"""
            SELECT unit_code,
                   AVG(yellow_occupied) AS yellow_avg, MAX(yellow_occupied) AS yellow_max,
                   AVG(isolation_total_occupied) AS iso_avg, COUNT(*) AS giros
            FROM parsed_events
            WHERE TRUE{pe_unit_sql}{p_received}
            GROUP BY unit_code
            """,
            pe_unit_params,
        )
        occ_by_unit = {r["unit_code"]: r for r in cur.fetchall()}

        # --- Dias com / sem ortopedista (último giro de cada dia) ---
        cur.execute(
            f"""
            WITH per_day AS (
              SELECT DISTINCT ON (unit_code, date(received_at))
                     unit_code, date(received_at) AS d, has_orthopedist
              FROM parsed_events
              WHERE TRUE{pe_unit_sql}{p_received}
              ORDER BY unit_code, date(received_at), received_at DESC
            )
            SELECT
              COUNT(*) FILTER (WHERE has_orthopedist IS FALSE) AS days_no_ortho,
              COUNT(*) FILTER (WHERE has_orthopedist IS TRUE)  AS days_with_ortho
            FROM per_day
            """,
            pe_unit_params,
        )
        ortho = cur.fetchone() or {}

        cur.execute(
            f"""
            WITH per_day AS (
              SELECT DISTINCT ON (unit_code, date(received_at))
                     unit_code, date(received_at) AS d, has_orthopedist
              FROM parsed_events
              WHERE TRUE{pe_unit_sql}{p_received}
              ORDER BY unit_code, date(received_at), received_at DESC
            )
            SELECT unit_code,
              COUNT(*) FILTER (WHERE has_orthopedist IS FALSE) AS days_no_ortho,
              COUNT(*) FILTER (WHERE has_orthopedist IS TRUE)  AS days_with_ortho
            FROM per_day GROUP BY unit_code
            """,
            pe_unit_params,
        )
        ortho_by_unit = {r["unit_code"]: r for r in cur.fetchall()}

        # --- Dias com radiografia fora (best-effort: exam.update xray indisponível) ---
        cur.execute(
            f"""
            SELECT COUNT(DISTINCT date(created_at)) AS days_xray_out
            FROM audit_log
            WHERE action='exam.update' AND entity_id LIKE '%%:xray'
              AND new_value->>'status' = 'unavailable'{xray_unit_sql}{p_created}
            """,
            xray_unit_params,
        )
        xray = cur.fetchone() or {}

        cur.execute(
            f"""
            SELECT split_part(entity_id, ':', 1) AS unit_id,
                   COUNT(DISTINCT date(created_at)) AS days_xray_out
            FROM audit_log
            WHERE action='exam.update' AND entity_id LIKE '%%:xray'
              AND new_value->>'status' = 'unavailable'{xray_unit_sql}{p_created}
            GROUP BY 1
            """,
            xray_unit_params,
        )
        xray_by_unit = {r["unit_id"]: r for r in cur.fetchall()}

        # --- Ocupação atual (current_unit_status) ---
        cur.execute(
            f"""
            SELECT unit_code, yellow_occupied, isolation_total_occupied, red_occupied
            FROM current_unit_status
            WHERE TRUE{cs_unit_sql}
            """,
            cs_unit_params,
        )
        cur_status = {r["unit_code"]: r for r in cur.fetchall()}

    # ---- monta totals ----
    yellow_current = sum((r["yellow_occupied"] or 0) for r in cur_status.values()) if cur_status else None
    iso_current = sum((r["isolation_total_occupied"] or 0) for r in cur_status.values()) if cur_status else None
    totals = {
        "red_patients": int(red_total.get("red_patients") or 0),
        "deaths": int(bed.get("deaths") or 0),
        "discharges": int(bed.get("discharges") or 0),
        "transfers": int(bed.get("transfers") or 0),
        "yellow_avg": _round1(occ.get("yellow_avg")),
        "yellow_max": int(occ["yellow_max"]) if occ.get("yellow_max") is not None else None,
        "yellow_current": yellow_current,
        "isolation_avg": _round1(occ.get("iso_avg")),
        "isolation_current": iso_current,
        "days_no_ortho": int(ortho.get("days_no_ortho") or 0),
        "days_with_ortho": int(ortho.get("days_with_ortho") or 0),
        "days_xray_out": int(xray.get("days_xray_out") or 0),
        "giros_count": int(occ.get("giros") or 0),
    }

    # ---- monta breakdown por unidade (une todas as fontes por code) ----
    codes = set(occ_by_unit) | set(ortho_by_unit) | set(cur_status) | set(red_by_unit)
    codes |= {id_to_unit[uid]["code"] for uid in bed_by_unit if uid in id_to_unit}
    codes |= {id_to_unit[uid]["code"] for uid in xray_by_unit if uid in id_to_unit}
    by_unit = []
    for code in sorted(codes):
        uid = code_to_id.get(code)
        b = bed_by_unit.get(uid, {}) if uid else {}
        o = occ_by_unit.get(code, {})
        ort = ortho_by_unit.get(code, {})
        xr = xray_by_unit.get(uid, {}) if uid else {}
        cs = cur_status.get(code, {})
        by_unit.append({
            "unit_code": code,
            "unit_name": code_to_name.get(code, code),
            "red_patients": int(red_by_unit.get(code, {}).get("red_patients") or 0),
            "deaths": int(b.get("deaths") or 0),
            "discharges": int(b.get("discharges") or 0),
            "transfers": int(b.get("transfers") or 0),
            "yellow_avg": _round1(o.get("yellow_avg")),
            "yellow_current": cs.get("yellow_occupied"),
            "isolation_current": cs.get("isolation_total_occupied"),
            "days_no_ortho": int(ort.get("days_no_ortho") or 0),
            "days_with_ortho": int(ort.get("days_with_ortho") or 0),
            "days_xray_out": int(xr.get("days_xray_out") or 0),
            "giros_count": int(o.get("giros") or 0),
        })

    return {"period": period, "unit": unit_code, "totals": totals, "by_unit": by_unit}


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


def register_telegram_alert_chat(
    chat_id: int | str,
    chat_type: str,
    title: str | None = None,
    is_active: bool = True,
) -> None:
    """Registra grupos que interagiram com o bot para receber alertas operacionais."""
    if not DATABASE_URL or chat_type not in {"group", "supergroup"}:
        return
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO telegram_alert_chats (chat_id, chat_type, title, is_active, last_seen_at)
                VALUES (%s, %s, %s, %s, NOW())
                ON CONFLICT (chat_id) DO UPDATE SET
                    chat_type = EXCLUDED.chat_type,
                    title = EXCLUDED.title,
                    is_active = EXCLUDED.is_active,
                    last_seen_at = NOW()
                """,
                (int(chat_id), chat_type, title, is_active),
            )
        conn.commit()


def get_telegram_alert_chats() -> list[dict[str, Any]]:
    if not DATABASE_URL:
        return []
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT chat_id, chat_type, title, last_seen_at
                FROM telegram_alert_chats
                WHERE is_active = TRUE
                ORDER BY last_seen_at DESC
                """
            )
            return list(cur.fetchall())


def get_whatsapp_alert_state() -> dict[str, datetime]:
    """Último aviso de silêncio enviado por WhatsApp, por unidade.

    Base do cooldown: o watcher varre a cada 30min e sem isto o mesmo aviso
    sairia em toda varredura. Persistido porque deploy/restart não pode zerar
    o anti-spam.
    """
    if not DATABASE_URL:
        return {}
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT unit_key, last_sent_at FROM whatsapp_alert_state")
            return {row["unit_key"]: row["last_sent_at"] for row in cur.fetchall()}


def record_whatsapp_alert_sent(unit_keys: list[str], sent_at: datetime | None = None) -> None:
    """Marca as unidades citadas no aviso que ACABOU de ser entregue.

    Só é chamada depois do gateway aceitar a mensagem — dry-run não grava, ou
    ligar o canal depois começaria já em cooldown.
    """
    if not DATABASE_URL or not unit_keys:
        return
    moment = sent_at or datetime.now(timezone.utc)
    with _connect() as conn:
        with conn.cursor() as cur:
            for unit_key in unit_keys:
                cur.execute(
                    """
                    INSERT INTO whatsapp_alert_state (unit_key, last_sent_at)
                    VALUES (%s, %s)
                    ON CONFLICT (unit_key) DO UPDATE SET last_sent_at = EXCLUDED.last_sent_at
                    """,
                    (unit_key, moment),
                )
        conn.commit()


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
                has_surgeon=bool(specialists.get("has_surgeon")),
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


# ---------------------------------------------------------------------------
# Relatórios de gestão (bot Telegram — ver reports.py)
# ---------------------------------------------------------------------------


def get_giro_activity(days: int = 7) -> dict[str, Any]:
    """Giros da janela + último giro de cada unidade, para o ranking de gaps.

    Usa `created_at` (chegada real no sistema), nunca `received_at` (horário
    digitado no texto, que tem drift documentado e correção heurística em
    main.build_dashboard_event). Cobrar alguém pelo horário digitado geraria
    injustiça; o watcher de unidades sem giro já tomou a mesma decisão.
    """
    if not DATABASE_URL:
        return {"events": [], "last_by_unit": {}}

    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT unit_code, created_at
                FROM parsed_events
                WHERE unit_code IS NOT NULL
                  AND created_at >= NOW() - make_interval(days => %s)
                ORDER BY unit_code ASC, created_at ASC
                """,
                (days,),
            )
            events = cur.fetchall()

            cur.execute(
                """
                SELECT unit_code, MAX(created_at) AS last_created_at
                FROM parsed_events
                WHERE unit_code IS NOT NULL
                GROUP BY unit_code
                """
            )
            last_rows = cur.fetchall()

    return {
        "events": events,
        "last_by_unit": {row["unit_code"]: row["last_created_at"] for row in last_rows},
    }


def get_capacity_timeline(days: int = 7) -> list[dict[str, Any]]:
    """Linha do tempo de `is_over_capacity` por giro, para o /lotacao.

    Os flags saem do payload JSONB no próprio Postgres — trazer o payload
    inteiro (com pacientes) para a aplicação seria caro e desnecessário.
    """
    if not DATABASE_URL:
        return []

    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    unit_code,
                    created_at,
                    COALESCE(payload->'data'->'rooms'->'red_room'->>'is_over_capacity', 'false')::boolean
                        AS red_over,
                    (
                        COALESCE(payload->'data'->'rooms'->'yellow_room'->>'is_over_capacity', 'false')::boolean
                        OR COALESCE(payload->'data'->'rooms'->'yellow_male'->>'is_over_capacity', 'false')::boolean
                        OR COALESCE(payload->'data'->'rooms'->'yellow_female'->>'is_over_capacity', 'false')::boolean
                    ) AS yellow_over
                FROM parsed_events
                WHERE unit_code IS NOT NULL
                  AND created_at >= NOW() - make_interval(days => %s)
                ORDER BY unit_code ASC, created_at ASC
                """,
                (days,),
            )
            return cur.fetchall()


def get_unit_responsibles() -> dict[str, list[str]]:
    """Coordenadores ativos por unidade — apenas o NOME.

    Telefone (`users.phone`) é dado pessoal de profissional de saúde e nunca
    sai em mensagem do bot; a coluna não é sequer selecionada aqui.

    Quando a unidade tem coordenador *primário* (`users.unit_id`), só ele é
    devolvido: `coordinator_units` inclui coordenadores de rede vinculados a
    todas as unidades, que poluiriam a cobrança.
    """
    if not DATABASE_URL:
        return {}

    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    un.code AS unit_code,
                    u.name AS name,
                    (u.unit_id = un.id) AS is_primary
                FROM coordinator_units cu
                JOIN users u ON u.id = cu.user_id
                JOIN units un ON un.id = cu.unit_id
                WHERE u.status = 'active' AND u.role = 'coordinator'
                ORDER BY un.code ASC, u.name ASC
                """
            )
            rows = cur.fetchall()

    grouped: dict[str, dict[str, list[str]]] = {}
    for row in rows:
        bucket = grouped.setdefault(row["unit_code"], {"primary": [], "other": []})
        bucket["primary" if row["is_primary"] else "other"].append(row["name"])

    return {
        unit_code: (bucket["primary"] or bucket["other"])
        for unit_code, bucket in grouped.items()
    }


def group_unit_contacts(
    rows: list[dict[str, Any]],
    limit_per_unit: int = 3,
) -> dict[str, list[dict[str, Any]]]:
    """Agrupa as linhas de `unit_contacts` por unidade, mantendo a ordem do SQL.

    Separada da consulta porque é a parte com regra (o corte por unidade) e a
    única que dá para testar sem banco.
    """
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        unit_code = row.get("unit_code")
        phone = str(row.get("sender_phone") or "").strip()
        if not unit_code or not phone:
            continue
        bucket = grouped.setdefault(unit_code, [])
        if len(bucket) >= limit_per_unit:
            continue
        bucket.append(
            {
                "phone": phone,
                "name": row.get("sender_name"),
                "giro_count": row.get("giro_count"),
                "last_seen_at": row.get("last_seen_at"),
            }
        )
    return grouped


def get_unit_contacts(
    limit_per_unit: int = 3,
    min_giro_count: int = 1,
) -> dict[str, list[dict[str, Any]]]:
    """Quem posta o giro, por unidade — os que mais postam primeiro.

    Derivado da ingestão (`unit_contacts`), não de cadastro: é a resposta para
    "quem eu chamo quando a UPA X para de postar?". O telefone SAI daqui de
    propósito — ele é a menção (campo `mentions` do gateway) que faz o aviso
    chegar em quem posta, diferente de `get_unit_responsibles`, onde o telefone
    do coordenador é dado pessoal que nunca vai para mensagem automática.

    Devolve {} quando não há banco ou a tabela ainda está vazia; todo consumidor
    trata isso como "cai no coordenador cadastrado".
    """
    if not DATABASE_URL:
        return {}

    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT unit_code, sender_phone, sender_name, giro_count, last_seen_at
                FROM unit_contacts
                WHERE giro_count >= %s
                ORDER BY unit_code ASC, giro_count DESC, last_seen_at DESC
                """,
                (min_giro_count,),
            )
            rows = cur.fetchall()

    return group_unit_contacts(rows, limit_per_unit)


def record_unparsed_message(
    source: str,
    raw_text: str,
    reasons: list[str] | None = None,
    unit_hint: str | None = None,
) -> None:
    """Persiste uma mensagem que parecia giro mas não pôde ser publicada."""
    if not DATABASE_URL:
        return

    first_line = next((line.strip() for line in (raw_text or "").splitlines() if line.strip()), "")

    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO unparsed_messages (source, raw_text, first_line, reasons, unit_hint)
                VALUES (%s, %s, %s, %s::jsonb, %s)
                """,
                (
                    source or "desconhecida",
                    (raw_text or "")[:4000],
                    first_line[:280],
                    _json_dumps(reasons or []),
                    unit_hint,
                ),
            )
        conn.commit()


def get_unparsed_summary(days: int = 7, sample_limit: int = 5) -> dict[str, Any]:
    """Contagens + amostra das mensagens não parseadas na janela."""
    if not DATABASE_URL:
        return {"total": 0, "by_source": [], "by_reason": [], "samples": []}

    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT count(*) AS total
                FROM unparsed_messages
                WHERE created_at >= NOW() - make_interval(days => %s)
                """,
                (days,),
            )
            total = (cur.fetchone() or {}).get("total", 0)

            cur.execute(
                """
                SELECT source, count(*) AS count
                FROM unparsed_messages
                WHERE created_at >= NOW() - make_interval(days => %s)
                GROUP BY source
                ORDER BY count DESC
                """,
                (days,),
            )
            by_source = cur.fetchall()

            cur.execute(
                """
                SELECT reason, count(*) AS count
                FROM unparsed_messages,
                     LATERAL jsonb_array_elements_text(reasons) AS reason
                WHERE created_at >= NOW() - make_interval(days => %s)
                GROUP BY reason
                ORDER BY count DESC
                """,
                (days,),
            )
            by_reason = cur.fetchall()

            cur.execute(
                """
                SELECT created_at, source, first_line, reasons
                FROM unparsed_messages
                WHERE created_at >= NOW() - make_interval(days => %s)
                ORDER BY created_at DESC
                LIMIT %s
                """,
                (days, sample_limit),
            )
            samples = cur.fetchall()

    for sample in samples:
        reasons = sample.get("reasons")
        if not isinstance(reasons, list):
            try:
                reasons = json.loads(reasons or "[]")
            except (TypeError, ValueError):
                reasons = []
        sample["reason"] = reasons[0] if reasons else None

    return {
        "total": total,
        "by_source": by_source,
        "by_reason": by_reason,
        "samples": samples,
    }


# ---------------------------------------------------------------------------
# SQL file-based migrations (auth/beds/etc)
# ---------------------------------------------------------------------------

_MIGRATIONS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "migrations")


def apply_migrations() -> None:
    """Apply all .sql files under migrations/ in alphabetical order.

    Each file is executed inside its own transaction. SQL files MUST be
    idempotent (use IF NOT EXISTS / IF EXISTS guards).
    """
    if not is_database_configured():
        return
    if not os.path.isdir(_MIGRATIONS_DIR):
        return

    files = sorted(
        name for name in os.listdir(_MIGRATIONS_DIR) if name.endswith(".sql")
    )
    if not files:
        return

    with _connect() as conn:
        for filename in files:
            full_path = os.path.join(_MIGRATIONS_DIR, filename)
            with open(full_path, "r", encoding="utf-8") as fh:
                sql = fh.read()
            if not sql.strip():
                continue
            with conn.cursor() as cur:
                cur.execute(sql)
            conn.commit()
