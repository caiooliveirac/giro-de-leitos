"""Seed coordenadores via CSV (UPA, nome) — gera username/senha/PIN/CPF e persiste.

Lê `coordenadores.csv` (formato: `Canonical Name UPA,Primeiro Nome Coord`), resolve
o unit_code via `units.resolve_unit_name`, gera credenciais (CPF válido, senha 6 chars
alfanuméricos sem ambiguidade, PIN 4 dígitos, username `<primeiro_nome>.<slug_curto>`),
e faz UPSERT em users por cpf_hash. Sobrescreve `username` também.

Uso:
    .venv/bin/python scripts/seed_coordinators_csv.py coordenadores.csv \
        --out credentials.v2.json [--include-admin]
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import random
import re
import secrets
import sys
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import psycopg
from psycopg.rows import dict_row

import db
from auth.crypto import encrypt_cpf, hash_cpf, hash_password, hash_pin
from units import UNIT_REGISTRY, resolve_unit_name


ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghjkmnpqrstuvwxyz23456789"  # sem 0/O/I/l/1


# Mapeamento explícito pros 3 casos cujo nome no CSV diverge muito do canonical.
EXPLICIT_CODE_BY_RAW = {
    "12º CENTRO DE SAÚDE": "centro_marback_alfredo_bureau",
    "12 CENTRO DE SAUDE": "centro_marback_alfredo_bureau",
    "16º CENTRO DE SAÚDE": "centro_maria_conceicao_santiago_imbassahy",
    "16 CENTRO DE SAUDE": "centro_maria_conceicao_santiago_imbassahy",
    "PA TANCREDO NEVES - R.A. 6° CENTRO": "pa_tancredo_neves_rodrigo_argolo",
    "PA TANCREDO NEVES - R.A. 6 CENTRO": "pa_tancredo_neves_rodrigo_argolo",
}


def gen_password(n: int = 6) -> str:
    return "".join(secrets.choice(ALPHABET) for _ in range(n))


def gen_pin() -> str:
    return f"{secrets.randbelow(10000):04d}"


def gen_cpf() -> str:
    n = [random.randint(0, 9) for _ in range(9)]
    d1 = sum((10 - i) * n[i] for i in range(9)) % 11
    d1 = 0 if d1 < 2 else 11 - d1
    n.append(d1)
    d2 = sum((11 - i) * n[i] for i in range(10)) % 11
    d2 = 0 if d2 < 2 else 11 - d2
    n.append(d2)
    return "".join(str(d) for d in n)


def _strip_accents(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value)
    return "".join(ch for ch in normalized if not unicodedata.combining(ch))


def _normalize_first_name(name: str) -> str:
    """Lowercase, sem acentos, primeiro token apenas."""
    flat = _strip_accents(name).strip().lower()
    flat = re.sub(r"[^a-z0-9 ]", "", flat)
    parts = flat.split()
    return parts[0] if parts else ""


def _short_slug(unit_code: str) -> str:
    """Slug curto pra compor o username, baseado no unit_code.

    Regras:
      - `upa_brotas` -> `brotas`
      - `upa_bairro_da_paz_orlando_imbassahy` -> `bairrodapaz`
      - `centro_marback_alfredo_bureau` -> `marback`
      - `centro_maria_conceicao_santiago_imbassahy` -> `mariaconcei`
      - `pa_tancredo_neves_rodrigo_argolo` -> `tancredoneves`
      - `pa_pernambues` -> `pernambues`
      - `pa_sao_marcos` -> `saomarcos`

    Estratégia: remove prefixos (`upa_`, `pa_`, `centro_`), pega o "núcleo"
    descritivo (até 2 tokens significativos), junta sem underscore.
    """
    parts = unit_code.split("_")
    # Remove prefixo institucional
    if parts and parts[0] in ("upa", "pa", "centro"):
        parts = parts[1:]
    # Casos explícitos com nome de pessoa no code → manter só primeiros 2 tokens
    explicit = {
        "upa_bairro_da_paz_orlando_imbassahy": "bairrodapaz",
        "upa_piraja_santo_inacio": "piraja",
        "upa_parque_sao_cristovao": "saocristovao",
        "upa_periperi": "periperi",
        "pa_tancredo_neves_rodrigo_argolo": "tancredoneves",
        "pa_pernambues": "pernambues",
        "pa_sao_marcos": "saomarcos",
        "centro_marback_alfredo_bureau": "marback",
        "centro_maria_conceicao_santiago_imbassahy": "mariaconcei",
        "upa_santo_antonio": "santoantonio",
        "upa_san_martin": "sanmartin",
        "upa_helio_machado": "heliomachado",
        "upa_valeria": "valeria",
        "upa_brotas": "brotas",
        "upa_paripe": "paripe",
        "upa_barris": "barris",
    }
    if unit_code in explicit:
        return explicit[unit_code]
    # Fallback: junta os 2 primeiros tokens restantes
    head = parts[:2] if parts else [unit_code]
    return "".join(head)


def _resolve_code(raw_name: str) -> str | None:
    raw_norm = _strip_accents(raw_name).upper().strip()
    raw_norm = re.sub(r"\s+", " ", raw_norm)
    # Try explicit map first
    if raw_norm in EXPLICIT_CODE_BY_RAW:
        return EXPLICIT_CODE_BY_RAW[raw_norm]
    if raw_name.strip() in EXPLICIT_CODE_BY_RAW:
        return EXPLICIT_CODE_BY_RAW[raw_name.strip()]
    # Fuzzy fallback
    match = resolve_unit_name(raw_name)
    if match:
        return match["unit_code"]
    return None


def _load_csv(path: Path) -> list[tuple[str, str]]:
    rows: list[tuple[str, str]] = []
    with path.open("r", encoding="utf-8") as fh:
        reader = csv.reader(fh)
        for line in reader:
            if not line or len(line) < 2:
                continue
            upa = line[0].strip()
            coord = line[1].strip()
            if not upa or not coord:
                continue
            rows.append((upa, coord))
    return rows


def _build_records(csv_rows: list[tuple[str, str]]) -> list[dict]:
    by_code: dict[str, dict] = {}
    used_usernames: set[str] = set()
    records: list[dict] = []

    for raw_upa, raw_coord in csv_rows:
        code = _resolve_code(raw_upa)
        if not code:
            print(f"ERRO: não consegui resolver '{raw_upa}' pra um unit_code.", file=sys.stderr)
            sys.exit(2)
        first = _normalize_first_name(raw_coord)
        if not first:
            print(f"ERRO: nome vazio pra UPA '{raw_upa}'.", file=sys.stderr)
            sys.exit(2)
        # Regra Jonathas/Jonatas: unifica como 'jonathas'
        if first in ("jonathas", "jonatas"):
            first = "jonathas"
            display_first = "Jonathas"
            full_name = "Jonathas"
        else:
            display_first = first.capitalize()
            full_name = raw_coord.strip()

        slug = _short_slug(code)
        username = f"{first}.{slug}"
        if username in used_usernames:
            # extremamente improvável; mas se acontecer, append sufixo
            suffix = 2
            while f"{username}{suffix}" in used_usernames:
                suffix += 1
            username = f"{username}{suffix}"
        used_usernames.add(username)

        unit_meta = next(u for u in UNIT_REGISTRY if u["code"] == code)
        rec = {
            "code": code,
            "canonical_name": unit_meta["canonical_name"],
            "csv_name": raw_upa,
            "coord_first_name": display_first,
            "full_name": full_name,
            "username": username,
            "password": gen_password(6),
            "pin": gen_pin(),
            "cpf": gen_cpf(),
            "cargo": "Coordenador de Plantão",
        }
        records.append(rec)
        by_code[code] = rec
    return records


def _persist(conn, records: list[dict]) -> None:
    with conn.cursor() as cur:
        cur.execute("SELECT id, code FROM units")
        unit_id_by_code = {r["code"]: r["id"] for r in cur.fetchall()}

    for rec in records:
        if rec["code"] not in unit_id_by_code:
            print(f"[skip] {rec['code']} ausente em units.")
            continue
        unit_id = unit_id_by_code[rec["code"]]
        cpf_enc = encrypt_cpf(rec["cpf"])
        cpf_h = hash_cpf(rec["cpf"])
        pw_h = hash_password(rec["password"])
        pin_h = hash_pin(rec["pin"])

        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO users (
                    name, cpf_encrypted, cpf_hash, role, cargo, unit_id, status,
                    password_hash, pin_hash, username, lgpd_accepted_at, approved_at
                ) VALUES (
                    %s, %s, %s, 'coordinator', %s, %s, 'active',
                    %s, %s, %s, NOW(), NOW()
                )
                ON CONFLICT (cpf_hash) DO UPDATE SET
                    name = EXCLUDED.name,
                    role = 'coordinator',
                    cargo = EXCLUDED.cargo,
                    unit_id = EXCLUDED.unit_id,
                    status = 'active',
                    password_hash = EXCLUDED.password_hash,
                    pin_hash = EXCLUDED.pin_hash,
                    username = EXCLUDED.username,
                    approved_at = COALESCE(users.approved_at, NOW())
                RETURNING id
                """,
                (
                    rec["full_name"], cpf_enc, cpf_h, rec["cargo"], unit_id,
                    pw_h, pin_h, rec["username"],
                ),
            )
            row = cur.fetchone()
        conn.commit()
        print(f"[ok] {rec['code']:42s} -> username={rec['username']} (user_id={row['id']})")


def _rotate_admin(conn) -> dict | None:
    email = os.getenv("ADMIN_INITIAL_EMAIL", "admin@giro.mnrs.com.br").lower().strip()
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id, name, email FROM users WHERE role='admin' AND email=%s",
            (email,),
        )
        admin = cur.fetchone()
    if not admin:
        print(f"[admin] não encontrado por email={email}; pulando rotação.", file=sys.stderr)
        return None
    new_pwd = gen_password(8)
    new_pin = gen_pin()
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE users SET password_hash=%s, pin_hash=%s WHERE id=%s",
            (hash_password(new_pwd), hash_pin(new_pin), admin["id"]),
        )
    conn.commit()
    print(f"[admin] senha rotacionada: {email}")
    return {
        "email": email,
        "name": admin["name"],
        "password": new_pwd,
        "pin": new_pin,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("csv_path", nargs="?", default="coordenadores.csv")
    parser.add_argument("--out", default="credentials.v2.json")
    parser.add_argument("--include-admin", action="store_true")
    args = parser.parse_args()

    csv_path = Path(args.csv_path)
    if not csv_path.exists():
        print(f"ERRO: CSV não encontrado: {csv_path}", file=sys.stderr)
        sys.exit(2)
    if not db.is_database_configured():
        print("ERRO: DATABASE_URL não configurada.", file=sys.stderr)
        sys.exit(2)

    rows = _load_csv(csv_path)
    records = _build_records(rows)

    with psycopg.connect(db.DATABASE_URL, row_factory=dict_row) as conn:
        _persist(conn, records)
        admin_block = _rotate_admin(conn) if args.include_admin else None

    out = {
        "version": 2,
        "units": [
            {
                "code": r["code"],
                "canonical_name": r["canonical_name"],
                "coord_first_name": r["coord_first_name"],
                "full_name": r["full_name"],
                "username": r["username"],
                "password": r["password"],
                "pin": r["pin"],
                "cpf": r["cpf"],
                "cargo": r["cargo"],
            }
            for r in records
        ],
    }
    if admin_block:
        out["admin"] = admin_block

    Path(args.out).write_text(json.dumps(out, indent=2, ensure_ascii=False))
    print(f"\nEscrito: {args.out}")
    print("\n=== Credenciais geradas ===")
    print(f"{'UPA (code)':45s} {'username':28s} {'senha':8s} {'PIN':4s}")
    for r in records:
        print(f"{r['code']:45s} {r['username']:28s} {r['password']:8s} {r['pin']:4s}")
    if admin_block:
        print(f"\nadmin: {admin_block['email']}  senha={admin_block['password']}  pin={admin_block['pin']}")


if __name__ == "__main__":
    main()
