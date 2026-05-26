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


# ---------------------------------------------------------------------------
# Parser → new-app projection (pure, DB-free)
# ---------------------------------------------------------------------------
# Maps Type-B counter sector_key → (occupied_col, capacity_col) in
# ``current_unit_status``. Sectors absent from this map have no parser source
# and project to 0/0.
COUNTER_PARSER_MAP: dict[str, tuple[str, str]] = {
    "yellow_unisex": ("yellow_occupied", "yellow_capacity"),
    "yellow_male": ("yellow_male_occupied", "yellow_male_capacity"),
    "yellow_female": ("yellow_female_occupied", "yellow_female_capacity"),
    "isolation_adult_m": ("isolation_male_occupied", "isolation_male_capacity"),
    "isolation_adult_f": ("isolation_female_occupied", "isolation_female_capacity"),
    "isolation_adult_unisex": ("isolation_total_occupied", "isolation_total_capacity"),
    "isolation_pediatric": ("isolation_pediatric_occupied", "isolation_pediatric_capacity"),
}

# Type-C specialists whose presence is reflected in ``current_unit_status``.
SPECIALIST_PARSER_MAP: dict[str, str] = {
    "orthopedist": "has_orthopedist",
    "surgeon": "has_surgeon",
}


def project_parser_state(
    parser_row: Optional[dict[str, Any]],
    sectors_config: list[dict[str, Any]] | dict[str, dict[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    """Project parser state (``current_unit_status`` row) onto the new-app
    resource shape.

    Pure: no DB access. Used by ``get_unit_state`` when there is no manual
    row yet for a given resource, and exercised by unit tests.

    ``parser_row`` may be ``None`` (parser never saw this unit).
    ``sectors_config`` is either a list of {sector_key, enabled, capacity}
    dicts (as returned by ``get_unit_state``) or a dict keyed by sector_key.
    Returns a dict with keys ``beds``, ``counters``, ``specialists``, ``exams``,
    each a list of projected resource dicts ready to merge into the response.
    """
    if isinstance(sectors_config, list):
        cfg = {item["sector_key"]: item for item in sectors_config}
    else:
        cfg = sectors_config

    received_at = parser_row.get("received_at") if parser_row else None

    # --- counters --------------------------------------------------------
    counters: list[dict[str, Any]] = []
    for key in SECTOR_TYPE_B_COUNTERS:
        sec = cfg.get(key)
        if not sec or not sec.get("enabled"):
            continue
        occ, cap = 0, 0
        if parser_row and key in COUNTER_PARSER_MAP:
            occ_col, cap_col = COUNTER_PARSER_MAP[key]
            occ = parser_row.get(occ_col) or 0
            cap = parser_row.get(cap_col) or 0
        source = "parser" if (parser_row and key in COUNTER_PARSER_MAP) else "default"
        counters.append(
            {
                "sector_key": key,
                "occupancy": occ,
                "capacity": cap,
                "version": 0,
                "last_updated_at": received_at,
                "last_updated_by": None,
                "source": source,
            }
        )

    # --- specialists -----------------------------------------------------
    specialists: list[dict[str, Any]] = []
    for key in SECTOR_TYPE_C_SPECIALISTS:
        sec = cfg.get(key)
        if not sec or not sec.get("enabled"):
            continue
        if parser_row and key in SPECIALIST_PARSER_MAP:
            flag = bool(parser_row.get(SPECIALIST_PARSER_MAP[key]))
            status = "available" if flag else "unavailable"
            source = "parser"
        else:
            status = "unavailable"
            source = "default"
        specialists.append(
            {
                "sector_key": key,
                "status": status,
                "version": 0,
                "last_updated_at": received_at,
                "last_updated_by": None,
                "source": source,
            }
        )

    # --- exams (parser does not cover) -----------------------------------
    exams: list[dict[str, Any]] = []
    for key in SECTOR_TYPE_D_EXAMS:
        sec = cfg.get(key)
        if not sec or not sec.get("enabled"):
            continue
        exams.append(
            {
                "sector_key": key,
                "status": "working",
                "unavailable_reason": None,
                "version": 0,
                "last_updated_at": received_at,
                "last_updated_by": None,
                "source": "default",
            }
        )

    # --- beds (red_room Type A) ------------------------------------------
    beds: list[dict[str, Any]] = []
    red_cfg = cfg.get("red_room")
    if red_cfg and red_cfg.get("enabled"):
        red_capacity = 0
        red_occupied = 0
        if parser_row:
            red_capacity = parser_row.get("red_capacity") or 0
            red_occupied = parser_row.get("red_occupied") or 0
        # Cap occupied at capacity to keep invariants sane.
        if red_capacity and red_occupied > red_capacity:
            red_occupied = red_capacity
        for n in range(1, red_capacity + 1):
            occupied = n <= red_occupied
            beds.append(
                {
                    "bed_number": n,
                    "patient_sigla": "—" if occupied else None,
                    "clinical_summary": "Aguardando detalhamento" if occupied else None,
                    "occupied_since": None,
                    "version": 0,
                    "last_updated_at": received_at,
                    "last_updated_by": None,
                    "source": "parser" if parser_row else "default",
                }
            )

    return {
        "beds": beds,
        "counters": counters,
        "specialists": specialists,
        "exams": exams,
    }


def _fetch_parser_status(conn, unit_code: Optional[str]) -> Optional[dict[str, Any]]:
    if not unit_code:
        return None
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT received_at, updated_at, is_critical, payload,
                   unit_match_method, unit_match_confidence, unit_matched_alias,
                   red_occupied, red_capacity,
                   yellow_occupied, yellow_capacity,
                   isolation_total_occupied, isolation_total_capacity,
                   isolation_female_occupied, isolation_female_capacity,
                   isolation_male_occupied, isolation_male_capacity,
                   isolation_pediatric_occupied, isolation_pediatric_capacity,
                   has_orthopedist, has_surgeon, has_psychiatrist
              FROM current_unit_status
             WHERE unit_code = %s
             ORDER BY received_at DESC
             LIMIT 1
            """,
            (unit_code,),
        )
        return cur.fetchone()


def _yellow_male_female_from_payload(
    parser_row: dict[str, Any] | None,
) -> dict[str, int | None]:
    """Extract yellow_male / yellow_female occupied+capacity from the parser
    ``payload`` JSONB blob (those columns don't exist as top-level fields on
    ``current_unit_status``). Returns keys expected by ``COUNTER_PARSER_MAP``.
    """
    out: dict[str, int | None] = {
        "yellow_male_occupied": None,
        "yellow_male_capacity": None,
        "yellow_female_occupied": None,
        "yellow_female_capacity": None,
    }
    if not parser_row:
        return out
    payload = parser_row.get("payload") or {}
    if isinstance(payload, str):
        try:
            import json as _json

            payload = _json.loads(payload)
        except Exception:
            payload = {}
    rooms = (payload.get("rooms") or {}) if isinstance(payload, dict) else {}
    ymale = rooms.get("yellow_male") or {}
    yfem = rooms.get("yellow_female") or {}
    if isinstance(ymale, dict):
        out["yellow_male_occupied"] = ymale.get("occupied")
        out["yellow_male_capacity"] = ymale.get("capacity")
    if isinstance(yfem, dict):
        out["yellow_female_occupied"] = yfem.get("occupied")
        out["yellow_female_capacity"] = yfem.get("capacity")
    return out


def get_unit_state(conn, unit_id: str) -> dict[str, Any]:
    """Return the full state of a unit — sectors, beds, counters, specs, exams.

    When a manual row is missing for a given enabled resource, the parser's
    latest ``current_unit_status`` row is projected in-memory (``version=0``,
    ``source="parser"`` or ``"default"``). The first manual edit creates a
    real row with ``version=1``.
    """
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

    # Parser snapshot from current_unit_status (joined on unit.code).
    parser_row = _fetch_parser_status(conn, unit.get("code"))
    parser_row_for_projection: Optional[dict[str, Any]] = None
    if parser_row:
        parser_row_for_projection = dict(parser_row)
        parser_row_for_projection.update(_yellow_male_female_from_payload(parser_row))

    # beds (red_room only — Type A) — manual rows first.
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
        beds_rows = [_serialize_bed(r) for r in cur.fetchall()]
    for b in beds_rows:
        b["source"] = "manual"

    enabled_counter_keys = [k for k in SECTOR_TYPE_B_COUNTERS if by_key.get(k) and by_key[k]["enabled"]]
    enabled_spec_keys = [k for k in SECTOR_TYPE_C_SPECIALISTS if by_key.get(k) and by_key[k]["enabled"]]
    enabled_exam_keys = [k for k in SECTOR_TYPE_D_EXAMS if by_key.get(k) and by_key[k]["enabled"]]

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

    counters = [{**c, "source": "manual"} for c in counters if c["sector_key"] in enabled_counter_keys]
    specialists = [{**s, "source": "manual"} for s in specialists if s["sector_key"] in enabled_spec_keys]
    exams = [{**e, "source": "manual"} for e in exams if e["sector_key"] in enabled_exam_keys]

    # Projection — fill enabled resources lacking a manual row.
    projected = project_parser_state(parser_row_for_projection, sectors_config)

    have_counter_keys = {c["sector_key"] for c in counters}
    for pc in projected["counters"]:
        if pc["sector_key"] not in have_counter_keys:
            counters.append(pc)

    have_spec_keys = {s["sector_key"] for s in specialists}
    for ps in projected["specialists"]:
        if ps["sector_key"] not in have_spec_keys:
            specialists.append(ps)

    have_exam_keys = {e["sector_key"] for e in exams}
    for pe in projected["exams"]:
        if pe["sector_key"] not in have_exam_keys:
            exams.append(pe)

    if not beds_rows:
        beds_rows = projected["beds"]

    # parser_snapshot — small payload for the frontend to render
    # "última atualização via WhatsApp".
    parser_snapshot: Optional[dict[str, Any]] = None
    if parser_row:
        raw_text = ""
        payload = parser_row.get("payload") or {}
        if isinstance(payload, str):
            try:
                import json as _json

                payload = _json.loads(payload)
            except Exception:
                payload = {}
        if isinstance(payload, dict):
            raw_text = str(payload.get("raw_text") or payload.get("text") or "")
        parser_snapshot = {
            "received_at": parser_row.get("received_at"),
            "is_critical": bool(parser_row.get("is_critical")),
            "raw_text": raw_text[:200],
            "unit_match_method": parser_row.get("unit_match_method"),
        }

    return {
        "unit": unit,
        "sectors_config": sectors_config,
        "beds": beds_rows,
        "counters": counters,
        "specialists": specialists,
        "exams": exams,
        "parser_snapshot": parser_snapshot,
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
    "project_parser_state",
    "COUNTER_PARSER_MAP",
    "SPECIALIST_PARSER_MAP",
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
