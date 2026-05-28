"""Seed unit_sectors_config a partir do estado atual do parser.

Usage:
    python scripts/seed_unit_sectors_config.py [--dry-run]

Lê `current_unit_status` (último estado parseado do WhatsApp por UPA) e
popula `unit_sectors_config` com os setores que cada unidade efetivamente
reporta, com suas capacidades reais. Idempotente: pode ser re-rodado.

Regras (resumo):
  - red_room: enabled se red_capacity > 0, capacity = red_capacity
  - yellow_unisex: enabled se UPA não esta em FIXED_NO_YELLOW e nao ha
    split masc/fem no payload (yellow_male / yellow_female == null)
  - yellow_male / yellow_female: enabled se o payload tiver capacity>0
  - isolation_adult_unisex: enabled se isolation_total_capacity > 0 e
    nao ha split masc/fem
  - isolation_adult_m / isolation_adult_f: enabled se cada capacity > 0
  - isolation_pediatric: enabled se isolation_pediatric_capacity > 0
  - specialists `orthopedist` e `surgeon`: enabled = True (todas UPAs)
  - dentist/pediatrician/psychiatrist: enabled onde o bloco ATENDIMENTO
    reporta o sinal (payload.data.specialists / coluna has_psychiatrist)
  - medication_room / ward_internment / ward_pediatric_internment: enabled
    onde rooms.other_beds reporta capacity > 0
  - obituary, pediatric_observation, exames: desabilitados (coord decide)

NUNCA mexe em counters / beds / specialists / exams: deixa a projecao em
memoria (fallback) cuidar deles. Conflitos com edicoes manuais futuras
sao evitados via UPSERT que so toca `enabled` e `capacity`.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import psycopg
from psycopg.rows import dict_row

import db


# Mantenha em sincronia com main.FIXED_NO_YELLOW_UNIT_CODES
FIXED_NO_YELLOW_UNIT_CODES = {
    "upa_bairro_da_paz_orlando_imbassahy",
    "upa_periperi",
}


def _payload_room_capacity(payload: dict | None, room_key: str) -> int | None:
    """Pega payload.data.rooms.<room_key>.capacity, retorna None se ausente."""
    if not payload:
        return None
    try:
        room = (payload.get("data") or {}).get("rooms", {}).get(room_key)
    except AttributeError:
        return None
    if not isinstance(room, dict):
        return None
    cap = room.get("capacity")
    if isinstance(cap, int):
        return cap
    return None


# Rótulos do parser (other_beds[].key) → sector_key sintético do app.
# NB: other_verde vem rotulado como "internamento" → mapeia p/ internamento.
_OTHER_BEDS_KEY_MAP = {
    "other_medicacao": "medication_room",
    "other_internamento": "ward_internment",
    "other_verde": "ward_internment",
    "other_pediatria": "ward_pediatric_internment",
}


def _payload_other_beds(payload: dict | None) -> dict[str, int]:
    """Capacidade efetiva por sector_key sintético de rooms.other_beds.

    Habilita o setor quando ELE EXISTE no giro (occ ou cap > 0) — salas de
    medicação/internamento muitas vezes são contagem sem capacidade fixa.
    Capacidade efetiva = max(capacity, occupied) para exibição sã.
    """
    acc: dict[str, tuple[int, int]] = {}
    if not payload:
        return {}
    rooms = (payload.get("data") or {}).get("rooms") if isinstance(payload.get("data"), dict) else None
    other_beds = rooms.get("other_beds") if isinstance(rooms, dict) else None
    if not isinstance(other_beds, list):
        return {}
    for bed in other_beds:
        if not isinstance(bed, dict):
            continue
        sector_key = _OTHER_BEDS_KEY_MAP.get(str(bed.get("key") or ""))
        if not sector_key:
            continue
        occ0, cap0 = acc.get(sector_key, (0, 0))
        acc[sector_key] = (occ0 + (bed.get("occupied") or 0), cap0 + (bed.get("capacity") or 0))
    # capacity efetiva = max(cap, occ); só inclui setores presentes (occ ou cap).
    return {k: max(cap, occ) for k, (occ, cap) in acc.items() if (occ > 0 or cap > 0)}


def _payload_specialist(payload: dict | None, has_key: str) -> bool:
    """Lê data.specialists.<has_key> do payload (ex.: has_dentist)."""
    if not payload:
        return False
    spec = (payload.get("data") or {}).get("specialists") if isinstance(payload.get("data"), dict) else None
    return bool(spec.get(has_key)) if isinstance(spec, dict) else False


def _compute_plan(row: dict) -> list[tuple[str, bool, int | None]]:
    """Retorna lista [(sector_key, enabled, capacity), ...] para a UPA.

    `row` e o dict do JOIN units LEFT JOIN current_unit_status.
    """
    code = row["code"]
    payload = row.get("payload") or {}

    red_capacity = row.get("red_capacity")
    yellow_capacity = row.get("yellow_capacity")
    ym_cap = _payload_room_capacity(payload, "yellow_male")
    yf_cap = _payload_room_capacity(payload, "yellow_female")
    has_yellow_split = ym_cap is not None or yf_cap is not None

    iso_total = row.get("isolation_total_capacity")
    iso_male = row.get("isolation_male_capacity")
    iso_female = row.get("isolation_female_capacity")
    iso_ped = row.get("isolation_pediatric_capacity")
    has_iso_adult_split = bool((iso_male or 0) > 0 or (iso_female or 0) > 0)

    no_yellow = code in FIXED_NO_YELLOW_UNIT_CODES

    plan: list[tuple[str, bool, int | None]] = []

    # Type A: red_room
    plan.append((
        "red_room",
        bool(red_capacity and red_capacity > 0),
        red_capacity if red_capacity and red_capacity > 0 else None,
    ))

    # Type B: yellow
    yellow_unisex_enabled = (
        not no_yellow
        and not has_yellow_split
        and bool(yellow_capacity and yellow_capacity > 0)
    )
    plan.append((
        "yellow_unisex",
        yellow_unisex_enabled,
        yellow_capacity if yellow_unisex_enabled else None,
    ))

    if no_yellow:
        plan.append(("yellow_male", False, None))
        plan.append(("yellow_female", False, None))
    else:
        plan.append((
            "yellow_male",
            bool(ym_cap and ym_cap > 0),
            ym_cap if ym_cap and ym_cap > 0 else None,
        ))
        plan.append((
            "yellow_female",
            bool(yf_cap and yf_cap > 0),
            yf_cap if yf_cap and yf_cap > 0 else None,
        ))

    # Type B: isolation
    plan.append((
        "isolation_adult_m",
        bool(iso_male and iso_male > 0),
        iso_male if iso_male and iso_male > 0 else None,
    ))
    plan.append((
        "isolation_adult_f",
        bool(iso_female and iso_female > 0),
        iso_female if iso_female and iso_female > 0 else None,
    ))

    iso_unisex_enabled = (
        not has_iso_adult_split
        and bool(iso_total and iso_total > 0)
    )
    plan.append((
        "isolation_adult_unisex",
        iso_unisex_enabled,
        iso_total if iso_unisex_enabled else None,
    ))

    plan.append((
        "isolation_pediatric",
        bool(iso_ped and iso_ped > 0),
        iso_ped if iso_ped and iso_ped > 0 else None,
    ))

    # Setores Type B nao cobertos pelo parser (deixa coord habilitar depois)
    plan.append(("obituary", False, None))
    plan.append(("pediatric_observation", False, None))

    # Type B derivados de rooms.other_beds (sala medicação/verde, internamento,
    # internamento pediátrico). Habilita onde o setor existe no giro.
    other_caps = _payload_other_beds(payload)
    for sector_key in ("medication_room", "ward_internment", "ward_pediatric_internment"):
        cap = other_caps.get(sector_key)
        plan.append((sector_key, sector_key in other_caps, cap))

    # Type C: specialists. orthopedist/surgeon sempre; dentist/pediatra/psiquiatra
    # habilitados onde o parser (bloco ATENDIMENTO) reporta o sinal.
    plan.append(("orthopedist", True, None))
    plan.append(("surgeon", True, None))
    plan.append(("dentist", _payload_specialist(payload, "has_dentist"), None))
    plan.append(("pediatrician", _payload_specialist(payload, "has_pediatrician"), None))
    plan.append(("psychiatrist", bool(row.get("has_psychiatrist")), None))

    # Type D: exames — nada habilitado por padrao
    for exam in ("xray", "ecg", "lab", "ultrasound", "tomography"):
        plan.append((exam, False, None))

    return plan


def _load_units(conn: psycopg.Connection) -> list[dict]:
    sql = """
        SELECT
            u.id,
            u.code,
            c.red_capacity,
            c.yellow_capacity,
            c.isolation_total_capacity,
            c.isolation_female_capacity,
            c.isolation_male_capacity,
            c.isolation_pediatric_capacity,
            c.has_orthopedist,
            c.has_surgeon,
            c.has_psychiatrist,
            c.payload
        FROM units u
        LEFT JOIN current_unit_status c ON c.unit_code = u.code
        ORDER BY u.code
    """
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(sql)
        return list(cur.fetchall())


def _apply_plan(conn: psycopg.Connection, unit_id: str, plan: list[tuple[str, bool, int | None]]) -> None:
    sql = """
        INSERT INTO unit_sectors_config (unit_id, sector_key, enabled, capacity)
        VALUES (%s, %s, %s, %s)
        ON CONFLICT (unit_id, sector_key) DO UPDATE SET
            enabled = EXCLUDED.enabled,
            capacity = EXCLUDED.capacity,
            updated_at = NOW()
    """
    with conn.cursor() as cur:
        for sector_key, enabled, capacity in plan:
            cur.execute(sql, (unit_id, sector_key, enabled, capacity))


def _print_summary(row: dict, plan: list[tuple[str, bool, int | None]]) -> None:
    enabled = [(k, c) for (k, e, c) in plan if e]
    parts = []
    for k, c in enabled:
        parts.append(f"{k}={c}" if c is not None else k)
    no_data = row.get("red_capacity") is None and row.get("yellow_capacity") is None and row.get("isolation_total_capacity") is None
    flag = " (SEM DADOS DO PARSER!)" if no_data else ""
    print(f"  {row['code']:<45} | {len(enabled):>2} setores | {', '.join(parts)}{flag}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed unit_sectors_config a partir do parser.")
    parser.add_argument("--dry-run", action="store_true", help="Imprime plano sem aplicar.")
    args = parser.parse_args()

    if not db.is_database_configured():
        print("ERRO: DATABASE_URL nao configurada.", file=sys.stderr)
        sys.exit(2)

    mode = "DRY-RUN" if args.dry_run else "APPLY"
    print(f">> Modo: {mode}")
    print(">> Lendo units + current_unit_status...")

    with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
        rows = _load_units(conn)
        print(f"   {len(rows)} UPAs encontradas.\n")

        print("Plano por UPA:")
        for row in rows:
            plan = _compute_plan(row)
            _print_summary(row, plan)
            if not args.dry_run:
                _apply_plan(conn, row["id"], plan)

        if args.dry_run:
            print("\n>> Dry-run: nada aplicado.")
            conn.rollback()
        else:
            conn.commit()
            print("\n>> Commit OK.")

            # Verificacao pos-aplicar
            print("\nVerificacao final:")
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute(
                    """
                    SELECT u.code, COUNT(*) FILTER (WHERE c.enabled) AS setores_ativos
                    FROM units u
                    LEFT JOIN unit_sectors_config c ON c.unit_id = u.id
                    GROUP BY u.code
                    ORDER BY u.code
                    """
                )
                for r in cur.fetchall():
                    print(f"  {r['code']:<45} | setores_ativos={r['setores_ativos']}")


if __name__ == "__main__":
    main()
