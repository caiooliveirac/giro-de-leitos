"""Service layer for Fase 3 — operates on a psycopg connection (dict_row).

All functions are pure with respect to the DB connection: the caller owns
commit/rollback. Optimistic locking uses the ``version`` column on each
resource. When the caller passes ``expected_version`` and it does not match
the current row, ``VersionConflict`` is raised carrying the current state.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional
from uuid import UUID

from auth.audit import record_audit

from beds.schemas import (
    SECTOR_TYPE_A_BEDS,
    SECTOR_TYPE_B_COUNTERS,
    SECTOR_TYPE_C_SPECIALISTS,
    SECTOR_TYPE_D_EXAMS,
    VALID_SECTOR_KEYS,
)


class VersionConflict(Exception):
    """Raised when ``expected_version`` does not match the current row."""

    def __init__(self, current: dict[str, Any]):
        super().__init__("version conflict")
        self.current = current


class NotFound(Exception):
    pass


# ---------------------------------------------------------------------------
# State read
# ---------------------------------------------------------------------------
def _get_unit(conn, unit_id: str) -> dict[str, Any]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id, code, canonical_name, slug, active FROM units WHERE id = %s",
            (unit_id,),
        )
        row = cur.fetchone()
    if not row:
        raise NotFound(f"unit {unit_id} not found")
    return {
        "id": str(row["id"]),
        "code": row["code"],
        "canonical_name": row["canonical_name"],
        "slug": row["slug"],
        "active": row["active"],
    }


def _serialize_bed(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "bed_number": row["bed_number"],
        "patient_sigla": row.get("patient_sigla"),
        "clinical_summary": row.get("clinical_summary"),
        "occupied_since": row.get("occupied_since"),
        "version": row["version"],
        "last_updated_at": row["last_updated_at"],
        "last_updated_by": row.get("last_updated_by"),
    }


def _serialize_counter(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "sector_key": row["sector_key"],
        "occupancy": row["occupancy"],
        "capacity": row["capacity"],
        "version": row["version"],
        "last_updated_at": row["last_updated_at"],
        "last_updated_by": row.get("last_updated_by"),
    }


def _serialize_specialist(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "sector_key": row["sector_key"],
        "status": row["status"],
        "version": row["version"],
        "last_updated_at": row["last_updated_at"],
        "last_updated_by": row.get("last_updated_by"),
    }


def _serialize_exam(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "sector_key": row["sector_key"],
        "status": row["status"],
        "unavailable_reason": row.get("unavailable_reason"),
        "version": row["version"],
        "last_updated_at": row["last_updated_at"],
        "last_updated_by": row.get("last_updated_by"),
    }


def get_unit_state(conn, unit_id: str) -> dict[str, Any]:
    """Return the full state of a unit — sectors, beds, counters, specs, exams."""
    unit = _get_unit(conn, unit_id)

    # sectors_config — fill defaults for every known key.
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT sector_key, enabled, capacity
              FROM unit_sectors_config
             WHERE unit_id = %s
            """,
            (unit_id,),
        )
        by_key = {r["sector_key"]: r for r in cur.fetchall()}

    sectors_config: list[dict[str, Any]] = []
    for key in VALID_SECTOR_KEYS:
        row = by_key.get(key)
        sectors_config.append(
            {
                "sector_key": key,
                "enabled": bool(row["enabled"]) if row else False,
                "capacity": row.get("capacity") if row else None,
            }
        )

    # beds (red_room only — Type A)
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT bed_number, patient_sigla, clinical_summary, occupied_since,
                   last_updated_at, last_updated_by, version
              FROM beds
             WHERE unit_id = %s
             ORDER BY bed_number
            """,
            (unit_id,),
        )
        beds = [_serialize_bed(r) for r in cur.fetchall()]

    # counters / specialists / exams — only rows for enabled sectors of each type.
    enabled_counter_keys = [k for k in SECTOR_TYPE_B_COUNTERS if by_key.get(k) and by_key[k]["enabled"]]
    enabled_spec_keys = [k for k in SECTOR_TYPE_C_SPECIALISTS if by_key.get(k) and by_key[k]["enabled"]]
    enabled_exam_keys = [k for k in SECTOR_TYPE_D_EXAMS if by_key.get(k) and by_key[k]["enabled"]]

    counters: list[dict[str, Any]] = []
    specialists: list[dict[str, Any]] = []
    exams: list[dict[str, Any]] = []

    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT sector_key, occupancy, capacity, last_updated_at, last_updated_by, version
              FROM counters WHERE unit_id = %s
            """,
            (unit_id,),
        )
        counters = [_serialize_counter(r) for r in cur.fetchall()]

        cur.execute(
            """
            SELECT sector_key, status, last_updated_at, last_updated_by, version
              FROM specialists WHERE unit_id = %s
            """,
            (unit_id,),
        )
        specialists = [_serialize_specialist(r) for r in cur.fetchall()]

        cur.execute(
            """
            SELECT sector_key, status, unavailable_reason, last_updated_at, last_updated_by, version
              FROM exams WHERE unit_id = %s
            """,
            (unit_id,),
        )
        exams = [_serialize_exam(r) for r in cur.fetchall()]

    # Filter to enabled sectors only.
    counters = [c for c in counters if c["sector_key"] in enabled_counter_keys]
    specialists = [s for s in specialists if s["sector_key"] in enabled_spec_keys]
    exams = [e for e in exams if e["sector_key"] in enabled_exam_keys]

    return {
        "unit": unit,
        "sectors_config": sectors_config,
        "beds": beds,
        "counters": counters,
        "specialists": specialists,
        "exams": exams,
    }


# ---------------------------------------------------------------------------
# Sector config
# ---------------------------------------------------------------------------
def put_sector_config(
    conn,
    unit_id: str,
    items: list[dict[str, Any]],
    actor: dict[str, Any],
) -> list[dict[str, Any]]:
    """Upsert the provided sector configs. Returns the resulting list."""
    _get_unit(conn, unit_id)  # 404 if absent

    with conn.cursor() as cur:
        for item in items:
            key = item["sector_key"]
            if key not in VALID_SECTOR_KEYS:
                raise ValueError(f"sector_key inválido: {key}")
            cur.execute(
                """
                INSERT INTO unit_sectors_config (unit_id, sector_key, enabled, capacity, updated_at)
                VALUES (%s, %s, %s, %s, NOW())
                ON CONFLICT (unit_id, sector_key) DO UPDATE
                   SET enabled = EXCLUDED.enabled,
                       capacity = EXCLUDED.capacity,
                       updated_at = NOW()
                """,
                (unit_id, key, item["enabled"], item.get("capacity")),
            )

    record_audit(
        conn,
        actor_user_id=actor.get("id"),
        action="unit.sectors_config.update",
        entity_type="unit",
        entity_id=str(unit_id),
        new_value={"items": items},
    )

    # Return refreshed config.
    state = get_unit_state(conn, unit_id)
    return state["sectors_config"]


# ---------------------------------------------------------------------------
# Beds (red_room)
# ---------------------------------------------------------------------------
def _fetch_bed(conn, unit_id: str, bed_number: int) -> Optional[dict[str, Any]]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT bed_number, patient_sigla, clinical_summary, occupied_since,
                   last_updated_at, last_updated_by, version
              FROM beds
             WHERE unit_id = %s AND bed_number = %s
            """,
            (unit_id, bed_number),
        )
        return cur.fetchone()


def _red_room_enabled(conn, unit_id: str) -> bool:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT enabled FROM unit_sectors_config WHERE unit_id = %s AND sector_key = 'red_room'",
            (unit_id,),
        )
        row = cur.fetchone()
    return bool(row and row["enabled"])


def _check_version(current: Optional[dict[str, Any]], expected_version: Optional[int]) -> None:
    if expected_version is None:
        return
    current_version = current["version"] if current else 0
    if current_version != expected_version:
        raise VersionConflict(current=_serialize_bed(current) if current else {})


def upsert_bed(
    conn,
    unit_id: str,
    bed_number: int,
    payload: dict[str, Any],
    actor: dict[str, Any],
    expected_version: Optional[int] = None,
) -> dict[str, Any]:
    """Create or update a red-room bed. Auto-creates the row if needed."""
    _get_unit(conn, unit_id)
    if not _red_room_enabled(conn, unit_id):
        raise ValueError("setor red_room desabilitado para esta unidade")

    current = _fetch_bed(conn, unit_id, bed_number)
    _check_version(current, expected_version)

    actor_id = str(actor["id"]) if actor.get("id") else None
    sigla = payload.get("patient_sigla")
    summary = payload.get("clinical_summary")

    was_vacant = current is None or not current.get("patient_sigla")
    occupied_since = current.get("occupied_since") if current else None
    if was_vacant and sigla:
        occupied_since = datetime.now(timezone.utc)

    with conn.cursor() as cur:
        if current is None:
            cur.execute(
                """
                INSERT INTO beds (unit_id, bed_number, patient_sigla, clinical_summary,
                                  occupied_since, last_updated_by, last_updated_at, version)
                VALUES (%s, %s, %s, %s, %s, %s, NOW(), 1)
                RETURNING bed_number, patient_sigla, clinical_summary, occupied_since,
                          last_updated_at, last_updated_by, version
                """,
                (unit_id, bed_number, sigla, summary, occupied_since, actor_id),
            )
        else:
            cur.execute(
                """
                UPDATE beds
                   SET patient_sigla = %s,
                       clinical_summary = %s,
                       occupied_since = %s,
                       last_updated_by = %s,
                       last_updated_at = NOW(),
                       version = version + 1
                 WHERE unit_id = %s AND bed_number = %s
                RETURNING bed_number, patient_sigla, clinical_summary, occupied_since,
                          last_updated_at, last_updated_by, version
                """,
                (sigla, summary, occupied_since, actor_id, unit_id, bed_number),
            )
        new_row = cur.fetchone()

    record_audit(
        conn,
        actor_user_id=actor_id,
        action="bed.update",
        entity_type="bed",
        entity_id=f"{unit_id}:{bed_number}",
        previous_value=_serialize_bed(current) if current else None,
        new_value=_serialize_bed(new_row),
    )
    return _serialize_bed(new_row)


def _clear_bed_columns(
    conn,
    unit_id: str,
    bed_number: int,
    actor_id: Optional[str],
    expected_version: Optional[int],
    action: str,
    extra_audit: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    current = _fetch_bed(conn, unit_id, bed_number)
    if current is None:
        raise NotFound(f"bed {bed_number} not found")
    _check_version(current, expected_version)

    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE beds
               SET patient_sigla = NULL,
                   clinical_summary = NULL,
                   occupied_since = NULL,
                   last_updated_by = %s,
                   last_updated_at = NOW(),
                   version = version + 1
             WHERE unit_id = %s AND bed_number = %s
            RETURNING bed_number, patient_sigla, clinical_summary, occupied_since,
                      last_updated_at, last_updated_by, version
            """,
            (actor_id, unit_id, bed_number),
        )
        new_row = cur.fetchone()

    record_audit(
        conn,
        actor_user_id=actor_id,
        action=action,
        entity_type="bed",
        entity_id=f"{unit_id}:{bed_number}",
        previous_value=_serialize_bed(current),
        new_value={**_serialize_bed(new_row), **(extra_audit or {})},
    )
    return _serialize_bed(new_row)


def discharge_bed(conn, unit_id, bed_number, actor, expected_version=None):
    return _clear_bed_columns(
        conn, unit_id, bed_number,
        str(actor["id"]) if actor.get("id") else None,
        expected_version, "bed.discharge",
    )


def bed_death(conn, unit_id, bed_number, actor, expected_version=None):
    return _clear_bed_columns(
        conn, unit_id, bed_number,
        str(actor["id"]) if actor.get("id") else None,
        expected_version, "bed.death",
    )


def bed_transfer(conn, unit_id, bed_number, actor, destination=None, expected_version=None):
    return _clear_bed_columns(
        conn, unit_id, bed_number,
        str(actor["id"]) if actor.get("id") else None,
        expected_version, "bed.transfer",
        extra_audit={"destination": destination} if destination else None,
    )


def bed_clear(conn, unit_id, bed_number, actor, expected_version=None):
    return _clear_bed_columns(
        conn, unit_id, bed_number,
        str(actor["id"]) if actor.get("id") else None,
        expected_version, "bed.clear",
    )


# ---------------------------------------------------------------------------
# Counters / specialists / exams — generic optimistic upsert
# ---------------------------------------------------------------------------
def _fetch_simple(conn, table: str, unit_id: str, sector_key: str) -> Optional[dict[str, Any]]:
    with conn.cursor() as cur:
        cur.execute(
            f"SELECT * FROM {table} WHERE unit_id = %s AND sector_key = %s",
            (unit_id, sector_key),
        )
        return cur.fetchone()


def _check_simple_version(
    current: Optional[dict[str, Any]],
    expected_version: Optional[int],
    serializer,
) -> None:
    if expected_version is None:
        return
    current_version = current["version"] if current else 0
    if current_version != expected_version:
        raise VersionConflict(current=serializer(current) if current else {})


def update_counter(
    conn,
    unit_id: str,
    sector_key: str,
    occupancy: int,
    capacity: int,
    actor: dict[str, Any],
    expected_version: Optional[int] = None,
) -> dict[str, Any]:
    if sector_key not in SECTOR_TYPE_B_COUNTERS:
        raise ValueError(f"sector_key {sector_key} não é counter")
    _get_unit(conn, unit_id)
    current = _fetch_simple(conn, "counters", unit_id, sector_key)
    _check_simple_version(current, expected_version, _serialize_counter)
    actor_id = str(actor["id"]) if actor.get("id") else None

    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO counters (unit_id, sector_key, occupancy, capacity,
                                  last_updated_by, last_updated_at, version)
            VALUES (%s, %s, %s, %s, %s, NOW(), 1)
            ON CONFLICT (unit_id, sector_key) DO UPDATE
               SET occupancy = EXCLUDED.occupancy,
                   capacity = EXCLUDED.capacity,
                   last_updated_by = EXCLUDED.last_updated_by,
                   last_updated_at = NOW(),
                   version = counters.version + 1
            RETURNING sector_key, occupancy, capacity, last_updated_at, last_updated_by, version
            """,
            (unit_id, sector_key, occupancy, capacity, actor_id),
        )
        new_row = cur.fetchone()

    record_audit(
        conn,
        actor_user_id=actor_id,
        action="counter.update",
        entity_type="counter",
        entity_id=f"{unit_id}:{sector_key}",
        previous_value=_serialize_counter(current) if current else None,
        new_value=_serialize_counter(new_row),
    )
    return _serialize_counter(new_row)


def update_specialist(
    conn,
    unit_id: str,
    sector_key: str,
    status_value: str,
    actor: dict[str, Any],
    expected_version: Optional[int] = None,
) -> dict[str, Any]:
    if sector_key not in SECTOR_TYPE_C_SPECIALISTS:
        raise ValueError(f"sector_key {sector_key} não é specialist")
    _get_unit(conn, unit_id)
    current = _fetch_simple(conn, "specialists", unit_id, sector_key)
    _check_simple_version(current, expected_version, _serialize_specialist)
    actor_id = str(actor["id"]) if actor.get("id") else None

    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO specialists (unit_id, sector_key, status,
                                     last_updated_by, last_updated_at, version)
            VALUES (%s, %s, %s, %s, NOW(), 1)
            ON CONFLICT (unit_id, sector_key) DO UPDATE
               SET status = EXCLUDED.status,
                   last_updated_by = EXCLUDED.last_updated_by,
                   last_updated_at = NOW(),
                   version = specialists.version + 1
            RETURNING sector_key, status, last_updated_at, last_updated_by, version
            """,
            (unit_id, sector_key, status_value, actor_id),
        )
        new_row = cur.fetchone()

    record_audit(
        conn,
        actor_user_id=actor_id,
        action="specialist.update",
        entity_type="specialist",
        entity_id=f"{unit_id}:{sector_key}",
        previous_value=_serialize_specialist(current) if current else None,
        new_value=_serialize_specialist(new_row),
    )
    return _serialize_specialist(new_row)


def update_exam(
    conn,
    unit_id: str,
    sector_key: str,
    status_value: str,
    actor: dict[str, Any],
    unavailable_reason: Optional[str] = None,
    expected_version: Optional[int] = None,
) -> dict[str, Any]:
    if sector_key not in SECTOR_TYPE_D_EXAMS:
        raise ValueError(f"sector_key {sector_key} não é exam")
    _get_unit(conn, unit_id)
    current = _fetch_simple(conn, "exams", unit_id, sector_key)
    _check_simple_version(current, expected_version, _serialize_exam)
    actor_id = str(actor["id"]) if actor.get("id") else None

    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO exams (unit_id, sector_key, status, unavailable_reason,
                               last_updated_by, last_updated_at, version)
            VALUES (%s, %s, %s, %s, %s, NOW(), 1)
            ON CONFLICT (unit_id, sector_key) DO UPDATE
               SET status = EXCLUDED.status,
                   unavailable_reason = EXCLUDED.unavailable_reason,
                   last_updated_by = EXCLUDED.last_updated_by,
                   last_updated_at = NOW(),
                   version = exams.version + 1
            RETURNING sector_key, status, unavailable_reason, last_updated_at,
                      last_updated_by, version
            """,
            (unit_id, sector_key, status_value, unavailable_reason, actor_id),
        )
        new_row = cur.fetchone()

    record_audit(
        conn,
        actor_user_id=actor_id,
        action="exam.update",
        entity_type="exam",
        entity_id=f"{unit_id}:{sector_key}",
        previous_value=_serialize_exam(current) if current else None,
        new_value=_serialize_exam(new_row),
    )
    return _serialize_exam(new_row)


__all__ = [
    "VersionConflict",
    "NotFound",
    "get_unit_state",
    "put_sector_config",
    "upsert_bed",
    "discharge_bed",
    "bed_death",
    "bed_transfer",
    "bed_clear",
    "update_counter",
    "update_specialist",
    "update_exam",
]
