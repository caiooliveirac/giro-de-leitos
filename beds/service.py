"""Service layer for Fase 3 — operates on a psycopg connection (dict_row).

All functions are pure with respect to the DB connection: the caller owns
commit/rollback. Optimistic locking uses the ``version`` column on each
resource. When the caller passes ``expected_version`` and it does not match
the current row, ``VersionConflict`` is raised carrying the current state.
"""

from __future__ import annotations

import json
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
    # Setores derivados de ``rooms.other_beds`` (sem coluna própria em
    # current_unit_status). As chaves sintéticas abaixo são preenchidas em
    # ``_other_beds_from_payload`` e mescladas no parser_row antes da projeção.
    "medication_room": ("medication_room_occupied", "medication_room_capacity"),
    "ward_internment": ("ward_internment_occupied", "ward_internment_capacity"),
    "ward_pediatric_internment": ("ward_ped_internment_occupied", "ward_ped_internment_capacity"),
}

# Type-C specialists. surgeon/orthopedist/psychiatrist têm coluna em
# current_unit_status; dentist/pediatrician vêm do payload (mesclados no
# parser_row). Os valores efetivos são lidos de ``parser_row[<has_*>]``.
SPECIALIST_PARSER_MAP: dict[str, str] = {
    "orthopedist": "has_orthopedist",
    "surgeon": "has_surgeon",
    "psychiatrist": "has_psychiatrist",
    "dentist": "has_dentist",
    "pediatrician": "has_pediatrician",
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
        patients: list[dict[str, Any]] = []
        if parser_row:
            red_capacity = parser_row.get("red_capacity") or 0
            red_occupied = parser_row.get("red_occupied") or 0
            patients = parser_row.get("red_room_patients") or []
        # A sala vermelha frequentemente opera em over-capacity (ocupado >
        # capacidade). Mostramos TODOS os pacientes ocupados — a grade cresce
        # além da capacidade configurada; o frontend marca os excedentes.
        n_beds = min(max(red_capacity, red_occupied, len(patients)), 60)
        for n in range(1, n_beds + 1):
            patient = patients[n - 1] if n <= len(patients) else None
            if patient:
                sigla = patient.get("sigla") or "—"
                parts = [patient.get("age"), patient.get("clinical_summary")]
                summary = " · ".join(p for p in parts if p) or patient.get("raw") or "Aguardando detalhamento"
                occupied = True
            elif n <= red_occupied:
                sigla = "—"
                summary = "Aguardando detalhamento"
                occupied = True
            else:
                sigla = None
                summary = None
                occupied = False
            beds.append(
                {
                    "bed_number": n,
                    "patient_sigla": sigla if occupied else None,
                    "clinical_summary": summary if occupied else None,
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


def _parser_payload_data(parser_row: Optional[dict[str, Any]]) -> dict[str, Any]:
    """Return the inner parsed dict from a ``parsed_events`` /
    ``current_unit_status`` payload.

    Production wraps the parsed content in an envelope
    ``{"data": {...}, "type", "source", "received_at"}`` — the actual fields
    (``rooms``, ``raw_text``, ``reported_at``, ``specialists`` …) live under
    ``data``. Older rows were flat. Return the inner ``data`` dict when present,
    otherwise the payload itself, so both shapes work.
    """
    if not parser_row:
        return {}
    payload = parser_row.get("payload") or {}
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except Exception:
            return {}
    if not isinstance(payload, dict):
        return {}
    data = payload.get("data")
    return data if isinstance(data, dict) else payload


def _fetch_latest_manual_update(conn, unit_id: str) -> Optional[dict[str, Any]]:
    """Return the most recent manual edit on this unit's giro across all
    resources (beds/counters/specialists/exams), with the editor's name.
    Returns None when no manual rows exist for the unit.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            WITH latest AS (
                SELECT last_updated_at AS at, last_updated_by AS by_id FROM beds         WHERE unit_id = %s
                UNION ALL
                SELECT last_updated_at, last_updated_by FROM counters    WHERE unit_id = %s
                UNION ALL
                SELECT last_updated_at, last_updated_by FROM specialists WHERE unit_id = %s
                UNION ALL
                SELECT last_updated_at, last_updated_by FROM exams       WHERE unit_id = %s
            )
            SELECT l.at, l.by_id, u.name AS by_name
              FROM latest l
              LEFT JOIN users u ON u.id = l.by_id
             WHERE l.at IS NOT NULL
             ORDER BY l.at DESC
             LIMIT 1
            """,
            (unit_id, unit_id, unit_id, unit_id),
        )
        return cur.fetchone()


def _normalize_source(source: Optional[str]) -> str:
    """Map raw ``parsed_events.source`` to a stable category for the UI."""
    if not source:
        return "unknown"
    s = source.lower()
    if "whatsapp" in s:
        return "whatsapp"
    if s in ("manual", "web", "site", "frontend"):
        return "manual_ingest"
    return s


def _build_provenance(
    parser_row: Optional[dict[str, Any]],
    manual_row: Optional[dict[str, Any]],
) -> dict[str, Any]:
    """Consolidate giro origin info for the UPA view.

    Picks whichever of (parser snapshot, latest manual edit) is more recent
    as ``latest`` — but always reports both so the UI can show history /
    "última leitura via WhatsApp" alongside "última edição manual".
    """
    whatsapp_block: Optional[dict[str, Any]] = None
    if parser_row:
        data = _parser_payload_data(parser_row)
        reported_at = data.get("reported_at")
        raw_text = str(data.get("raw_text") or data.get("text") or "")
        whatsapp_block = {
            "source": _normalize_source(parser_row.get("source")),
            "source_raw": parser_row.get("source"),
            "received_at": parser_row.get("received_at"),
            "reported_at": reported_at,
            "raw_text_preview": raw_text[:200],
        }

    manual_block: Optional[dict[str, Any]] = None
    if manual_row and manual_row.get("at"):
        manual_block = {
            "source": "site",
            "received_at": manual_row.get("at"),
            "reported_at": manual_row.get("at"),
            "user_id": str(manual_row["by_id"]) if manual_row.get("by_id") else None,
            "user_name": manual_row.get("by_name"),
        }

    candidates: list[tuple[datetime, str, dict[str, Any]]] = []
    if whatsapp_block and whatsapp_block.get("received_at"):
        candidates.append((whatsapp_block["received_at"], "whatsapp", whatsapp_block))
    if manual_block and manual_block.get("received_at"):
        candidates.append((manual_block["received_at"], "site", manual_block))

    latest_kind: Optional[str] = None
    latest_at: Optional[datetime] = None
    if candidates:
        candidates.sort(key=lambda x: x[0], reverse=True)
        latest_at, latest_kind, _ = candidates[0]

    return {
        "latest_kind": latest_kind,           # "whatsapp" | "site" | None
        "latest_at": latest_at,                # the most recent of the two
        "whatsapp": whatsapp_block,            # may be None
        "manual": manual_block,                # may be None
    }


def _fetch_parser_status(conn, unit_code: Optional[str]) -> Optional[dict[str, Any]]:
    if not unit_code:
        return None
    with conn.cursor() as cur:
        # NOTE: current_unit_status does NOT have unit_match_* columns
        # (those live in parsed_events). Join on last_event_id when available.
        cur.execute(
            """
            SELECT s.source, s.received_at, s.updated_at, s.is_critical, s.payload,
                   pe.unit_match_method,
                   pe.unit_match_confidence,
                   pe.unit_matched_alias,
                   s.red_occupied, s.red_capacity,
                   s.yellow_occupied, s.yellow_capacity,
                   s.isolation_total_occupied, s.isolation_total_capacity,
                   s.isolation_female_occupied, s.isolation_female_capacity,
                   s.isolation_male_occupied, s.isolation_male_capacity,
                   s.isolation_pediatric_occupied, s.isolation_pediatric_capacity,
                   s.has_orthopedist, s.has_surgeon, s.has_psychiatrist
              FROM current_unit_status s
              LEFT JOIN parsed_events pe ON pe.id = s.last_event_id
             WHERE s.unit_code = %s
             ORDER BY s.received_at DESC
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
    data = _parser_payload_data(parser_row)
    rooms = data.get("rooms") or {}
    if not isinstance(rooms, dict):
        rooms = {}
    ymale = rooms.get("yellow_male") or {}
    yfem = rooms.get("yellow_female") or {}
    if isinstance(ymale, dict):
        out["yellow_male_occupied"] = ymale.get("occupied")
        out["yellow_male_capacity"] = ymale.get("capacity")
    if isinstance(yfem, dict):
        out["yellow_female_occupied"] = yfem.get("occupied")
        out["yellow_female_capacity"] = yfem.get("capacity")
    return out


# Rótulos do parser (``other_beds[].key``) → sector_key sintético do app.
# NB: ``other_verde`` vem rotulado como "internamento" nas mensagens, então
# mapeia para internamento (não para a sala de medicação).
_OTHER_BEDS_KEY_MAP: dict[str, str] = {
    "other_medicacao": "medication_room",
    "other_internamento": "ward_internment",
    "other_verde": "ward_internment",
    "other_pediatria": "ward_pediatric_internment",
}

_OTHER_BEDS_COL_PREFIX: dict[str, str] = {
    "medication_room": "medication_room",
    "ward_internment": "ward_internment",
    "ward_pediatric_internment": "ward_ped_internment",
}


def _other_beds_from_payload(parser_row: Optional[dict[str, Any]]) -> dict[str, int | None]:
    """Mapeia ``rooms.other_beds`` (medicação/verde, internamento, internamento
    pediátrico) para as colunas sintéticas esperadas por ``COUNTER_PARSER_MAP``.

    Salas de medicação/internamento muitas vezes vêm como contagem de pacientes
    sem capacidade fixa (capacity=0). Para não exibir "5/0" como over eterno,
    a capacidade efetiva é ``max(capacity, occupied)``.
    """
    out: dict[str, int | None] = {}
    data = _parser_payload_data(parser_row)
    rooms = data.get("rooms") if isinstance(data.get("rooms"), dict) else {}
    other_beds = rooms.get("other_beds") if isinstance(rooms, dict) else None
    if not isinstance(other_beds, list):
        return out
    for bed in other_beds:
        if not isinstance(bed, dict):
            continue
        sector_key = _OTHER_BEDS_KEY_MAP.get(str(bed.get("key") or ""))
        if not sector_key:
            continue
        prefix = _OTHER_BEDS_COL_PREFIX[sector_key]
        # Soma quando há mais de um other_bed para o mesmo setor.
        occ = (out.get(f"{prefix}_occupied") or 0) + (bed.get("occupied") or 0)
        cap = (out.get(f"{prefix}_capacity") or 0) + (bed.get("capacity") or 0)
        out[f"{prefix}_occupied"] = occ
        out[f"{prefix}_capacity"] = max(cap, occ)
    return out


def _specialists_from_payload(parser_row: Optional[dict[str, Any]]) -> dict[str, bool]:
    """Lê ``data.specialists`` do payload (inclui dentist/pediatrician que não
    têm coluna em current_unit_status). Só retorna chaves presentes.
    """
    data = _parser_payload_data(parser_row)
    spec = data.get("specialists")
    if not isinstance(spec, dict):
        return {}
    out: dict[str, bool] = {}
    for key in ("has_surgeon", "has_orthopedist", "has_psychiatrist", "has_dentist", "has_pediatrician"):
        if key in spec:
            out[key] = bool(spec[key])
    return out


def _red_room_patients_from_payload(parser_row: Optional[dict[str, Any]]) -> list[dict[str, Any]]:
    """Extrai a lista de pacientes da sala vermelha de ``rooms.red_room.patients``."""
    data = _parser_payload_data(parser_row)
    rooms = data.get("rooms") if isinstance(data.get("rooms"), dict) else {}
    red = rooms.get("red_room") if isinstance(rooms, dict) else None
    patients = red.get("patients") if isinstance(red, dict) else None
    return patients if isinstance(patients, list) else []


def _fetch_red_room_takeover(conn, unit_id: str) -> Optional[dict[str, Any]]:
    """Estado do takeover da sala vermelha (com nome de quem assumiu)."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT t.assumed_at, t.released_at, t.assumed_by, u.name AS assumed_by_name
              FROM unit_sector_takeover t
              LEFT JOIN users u ON u.id = t.assumed_by
             WHERE t.unit_id = %s AND t.sector_key = 'red_room'
            """,
            (unit_id,),
        )
        return cur.fetchone()


def _choose_beds(
    assumed: bool,
    manual_rows: list[dict[str, Any]],
    projected_beds: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Decide a fonte dos leitos da vermelha (puro, testável sem DB).

    Assumido → linhas manuais (controle do plantonista). Não assumido →
    projeção ao vivo do parser, ignorando linhas manuais antigas.
    """
    return manual_rows if assumed else projected_beds


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
        parser_row_for_projection.update(_other_beds_from_payload(parser_row))
        parser_row_for_projection.update(_specialists_from_payload(parser_row))
        parser_row_for_projection["red_room_patients"] = _red_room_patients_from_payload(parser_row)

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

    # Sala vermelha: gate de takeover. Sem takeover → projeta AO VIVO do parser
    # (re-semeia a cada giro), ignorando linhas manuais antigas. Com takeover →
    # a edição manual vence (parser para de sobrescrever).
    takeover = _fetch_red_room_takeover(conn, unit_id)
    red_assumed = bool(takeover and takeover.get("released_at") is None)
    beds_rows = _choose_beds(red_assumed, beds_rows, projected["beds"])

    # parser_snapshot — small payload for the frontend to render
    # "última atualização via WhatsApp".
    parser_snapshot: Optional[dict[str, Any]] = None
    if parser_row:
        data = _parser_payload_data(parser_row)
        raw_text = str(data.get("raw_text") or data.get("text") or "")
        parser_snapshot = {
            "received_at": parser_row.get("received_at"),
            "is_critical": bool(parser_row.get("is_critical")),
            "raw_text": raw_text[:200],
            "unit_match_method": parser_row.get("unit_match_method"),
        }

    manual_latest = _fetch_latest_manual_update(conn, unit_id)
    provenance = _build_provenance(parser_row, manual_latest)

    return {
        "unit": unit,
        "sectors_config": sectors_config,
        "beds": beds_rows,
        "counters": counters,
        "specialists": specialists,
        "exams": exams,
        "parser_snapshot": parser_snapshot,
        "provenance": provenance,
        "red_room_assumed": red_assumed,
        "red_room_assumed_by": (takeover or {}).get("assumed_by_name") if red_assumed else None,
        "red_room_assumed_at": (takeover or {}).get("assumed_at") if red_assumed else None,
    }


def get_giro_history(conn, unit_id: str, limit: int = 10) -> list[dict[str, Any]]:
    """Return recent giro events (parser ingestions) for this unit, plus
    a separate slice of the latest manual edits. The frontend uses this
    behind a "ver histórico" affordance on the provenance badge.
    """
    unit = _get_unit(conn, unit_id)
    unit_code = unit.get("code")

    parser_history: list[dict[str, Any]] = []
    if unit_code:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, source, received_at, payload,
                       red_occupied, red_capacity,
                       yellow_occupied, yellow_capacity,
                       is_critical
                  FROM parsed_events
                 WHERE unit_code = %s
                 ORDER BY received_at DESC
                 LIMIT %s
                """,
                (unit_code, int(limit)),
            )
            rows = cur.fetchall()
        for r in rows:
            data = _parser_payload_data(r)
            reported_at = data.get("reported_at")
            raw_text = str(data.get("raw_text") or data.get("text") or "")
            parser_history.append(
                {
                    "id": r["id"],
                    "kind": "whatsapp",
                    "source": _normalize_source(r.get("source")),
                    "source_raw": r.get("source"),
                    "received_at": r.get("received_at"),
                    "reported_at": reported_at,
                    "is_critical": bool(r.get("is_critical")),
                    "raw_text_preview": raw_text[:200],
                    "red": {"occupied": r.get("red_occupied"), "capacity": r.get("red_capacity")},
                    "yellow": {"occupied": r.get("yellow_occupied"), "capacity": r.get("yellow_capacity")},
                }
            )

    return parser_history


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
    # Rede de segurança: editar um leito implica assumir a vermelha, para que o
    # próximo giro não sobrescreva o que o plantonista digitou.
    tk = _fetch_red_room_takeover(conn, unit_id)
    if not (tk and tk.get("released_at") is None):
        _set_red_room_takeover(conn, unit_id, actor_id)
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
# Sala vermelha — takeover ("assumir giro")
# ---------------------------------------------------------------------------
def _project_red_beds(conn, unit_id: str) -> list[dict[str, Any]]:
    """Projeta os leitos da sala vermelha a partir do último giro (sem gate)."""
    unit = _get_unit(conn, unit_id)
    with conn.cursor() as cur:
        cur.execute(
            "SELECT sector_key, enabled, capacity FROM unit_sectors_config WHERE unit_id = %s",
            (unit_id,),
        )
        by_key = {r["sector_key"]: r for r in cur.fetchall()}
    sectors_config = [
        {
            "sector_key": key,
            "enabled": bool(by_key[key]["enabled"]) if key in by_key else False,
            "capacity": by_key[key].get("capacity") if key in by_key else None,
        }
        for key in VALID_SECTOR_KEYS
    ]
    parser_row = _fetch_parser_status(conn, unit.get("code"))
    pr: Optional[dict[str, Any]] = None
    if parser_row:
        pr = dict(parser_row)
        pr.update(_yellow_male_female_from_payload(parser_row))
        pr["red_room_patients"] = _red_room_patients_from_payload(parser_row)
    return project_parser_state(pr, sectors_config)["beds"]


def _set_red_room_takeover(conn, unit_id: str, actor_id: Optional[str]) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO unit_sector_takeover (unit_id, sector_key, assumed_by, assumed_at, released_at)
            VALUES (%s, 'red_room', %s, NOW(), NULL)
            ON CONFLICT (unit_id, sector_key) DO UPDATE
                SET assumed_by = EXCLUDED.assumed_by, assumed_at = NOW(), released_at = NULL
            """,
            (unit_id, actor_id),
        )


def assume_red_room(conn, unit_id: str, actor: dict[str, Any]) -> dict[str, Any]:
    """Plantonista assume a sala vermelha: congela a leitura ao vivo do parser
    em linhas editáveis e trava o re-seed automático.
    """
    _get_unit(conn, unit_id)
    if not _red_room_enabled(conn, unit_id):
        raise ValueError("setor red_room desabilitado para esta unidade")
    actor_id = str(actor["id"]) if actor.get("id") else None

    projected = _project_red_beds(conn, unit_id)
    with conn.cursor() as cur:
        for bed in projected:
            if not bed.get("patient_sigla"):
                continue
            cur.execute(
                """
                INSERT INTO beds (unit_id, bed_number, patient_sigla, clinical_summary,
                                  occupied_since, last_updated_by, last_updated_at, version)
                VALUES (%s, %s, %s, %s, NULL, %s, NOW(), 1)
                ON CONFLICT (unit_id, bed_number) DO NOTHING
                """,
                (unit_id, bed["bed_number"], bed["patient_sigla"], bed.get("clinical_summary"), actor_id),
            )
    _set_red_room_takeover(conn, unit_id, actor_id)
    record_audit(
        conn,
        actor_user_id=actor_id,
        action="red_room.assume",
        entity_type="unit",
        entity_id=str(unit_id),
        previous_value=None,
        new_value={"assumed": True, "seeded_beds": len([b for b in projected if b.get("patient_sigla")])},
    )
    return {"assumed": True}


def release_red_room(conn, unit_id: str, actor: dict[str, Any]) -> dict[str, Any]:
    """Libera a sala vermelha: apaga os leitos manuais e volta ao modo ao vivo
    (próximo giro re-semeia).
    """
    _get_unit(conn, unit_id)
    actor_id = str(actor["id"]) if actor.get("id") else None
    with conn.cursor() as cur:
        cur.execute("DELETE FROM beds WHERE unit_id = %s", (unit_id,))
        cur.execute(
            "UPDATE unit_sector_takeover SET released_at = NOW() WHERE unit_id = %s AND sector_key = 'red_room'",
            (unit_id,),
        )
    record_audit(
        conn,
        actor_user_id=actor_id,
        action="red_room.release",
        entity_type="unit",
        entity_id=str(unit_id),
        previous_value={"assumed": True},
        new_value={"assumed": False},
    )
    return {"assumed": False}


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
