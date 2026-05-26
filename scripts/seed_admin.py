"""Seed admin user + units/aliases (Fase 1).

Usage:
    python scripts/seed_admin.py

Required env:
    DATABASE_URL
    CPF_ENCRYPTION_KEY        (urlsafe-base64 Fernet key)
    ADMIN_INITIAL_EMAIL
    ADMIN_INITIAL_PASSWORD
    ADMIN_INITIAL_CPF         (11 digits)

Optional env:
    ADMIN_INITIAL_NAME        (default: "Admin Regulador")
    ADMIN_INITIAL_PIN         (4 digits, default: "0000")
    CPF_HASH_PEPPER           (recommended in production)

The script is idempotent: re-running updates the admin password/PIN/name and
re-upserts units + aliases from units.UNIT_REGISTRY.
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

# Make repo root importable when run as `python scripts/seed_admin.py`
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import psycopg
from psycopg.rows import dict_row

import db
from units import UNIT_REGISTRY


def _slugify(code: str) -> str:
    return code  # registry codes already snake_case


def _require_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        print(f"ERRO: variável obrigatória {name} não definida.", file=sys.stderr)
        sys.exit(2)
    return value


def _ensure_fernet_key() -> None:
    if os.getenv("CPF_ENCRYPTION_KEY", "").strip():
        return
    try:
        from cryptography.fernet import Fernet
        generated = Fernet.generate_key().decode("utf-8")
    except Exception:  # noqa: BLE001
        generated = "<<instale `cryptography` e gere com Fernet.generate_key()>>"
    print(
        "ERRO: CPF_ENCRYPTION_KEY não definida.\n"
        f"Sugestão (gere uma e exporte): export CPF_ENCRYPTION_KEY='{generated}'",
        file=sys.stderr,
    )
    sys.exit(2)


def _ensure_pgcrypto(conn: psycopg.Connection) -> None:
    with conn.cursor() as cur:
        cur.execute('CREATE EXTENSION IF NOT EXISTS "pgcrypto"')
    conn.commit()


def _upsert_units(conn: psycopg.Connection) -> int:
    upserts = 0
    with conn.cursor(row_factory=dict_row) as cur:
        for unit in UNIT_REGISTRY:
            code = unit["code"]
            cur.execute(
                """
                INSERT INTO units (code, canonical_name, slug, active)
                VALUES (%s, %s, %s, TRUE)
                ON CONFLICT (code) DO UPDATE SET
                    canonical_name = EXCLUDED.canonical_name,
                    slug = EXCLUDED.slug
                RETURNING id
                """,
                (code, unit["canonical_name"], _slugify(code)),
            )
            row = cur.fetchone()
            unit_id = row["id"]
            for alias in unit.get("aliases", []):
                alias = (alias or "").strip()
                if not alias:
                    continue
                cur.execute(
                    """
                    INSERT INTO unit_aliases (unit_id, alias)
                    VALUES (%s, %s)
                    ON CONFLICT (unit_id, alias) DO NOTHING
                    """,
                    (unit_id, alias),
                )
            upserts += 1
    conn.commit()
    return upserts


def _upsert_admin(conn: psycopg.Connection) -> str:
    from auth.crypto import encrypt_cpf, hash_cpf, hash_password, hash_pin

    name = os.getenv("ADMIN_INITIAL_NAME", "Admin Regulador").strip() or "Admin Regulador"
    email = _require_env("ADMIN_INITIAL_EMAIL").lower()
    password = _require_env("ADMIN_INITIAL_PASSWORD")
    cpf_raw = _require_env("ADMIN_INITIAL_CPF")
    cpf_digits = re.sub(r"\D", "", cpf_raw)
    if len(cpf_digits) != 11:
        print("ERRO: ADMIN_INITIAL_CPF precisa ter 11 dígitos.", file=sys.stderr)
        sys.exit(2)
    pin = os.getenv("ADMIN_INITIAL_PIN", "0000").strip() or "0000"
    if not re.fullmatch(r"\d{4,6}", pin):
        print("ERRO: ADMIN_INITIAL_PIN precisa ser 4-6 dígitos.", file=sys.stderr)
        sys.exit(2)

    cpf_encrypted = encrypt_cpf(cpf_digits)
    cpf_h = hash_cpf(cpf_digits)
    password_h = hash_password(password)
    pin_h = hash_pin(pin)

    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            INSERT INTO users (
                name, cpf_encrypted, cpf_hash, role, status, email,
                password_hash, pin_hash, unit_id, lgpd_accepted_at, approved_at
            )
            VALUES (%s, %s, %s, 'admin', 'active', %s, %s, %s, NULL, NOW(), NOW())
            ON CONFLICT (cpf_hash) DO UPDATE SET
                name = EXCLUDED.name,
                email = EXCLUDED.email,
                password_hash = EXCLUDED.password_hash,
                pin_hash = EXCLUDED.pin_hash,
                cpf_encrypted = EXCLUDED.cpf_encrypted,
                role = 'admin',
                status = 'active',
                unit_id = NULL,
                approved_at = COALESCE(users.approved_at, NOW())
            RETURNING id
            """,
            (name, cpf_encrypted, cpf_h, email, password_h, pin_h),
        )
        row = cur.fetchone()
    conn.commit()
    return str(row["id"])


def main() -> None:
    if not db.is_database_configured():
        print("ERRO: DATABASE_URL não configurada.", file=sys.stderr)
        sys.exit(2)

    _ensure_fernet_key()

    print(">> Aplicando init_db() (schema legado + migrations/*.sql)...")
    db.init_db()

    with psycopg.connect(os.environ["DATABASE_URL"], row_factory=dict_row) as conn:
        _ensure_pgcrypto(conn)
        print(">> Upsert de unidades a partir do UNIT_REGISTRY...")
        n = _upsert_units(conn)
        print(f"   {n} unidades sincronizadas com aliases.")
        print(">> Upsert do admin inicial...")
        admin_id = _upsert_admin(conn)
        print(f"   Admin pronto: id={admin_id}")

    print("\n=== Seed concluído ===")
    print("Login do admin (Fase 2):")
    print(f"  email    : {os.getenv('ADMIN_INITIAL_EMAIL')}")
    print("  senha    : (a que você passou em ADMIN_INITIAL_PASSWORD)")
    print(f"  PIN      : {os.getenv('ADMIN_INITIAL_PIN', '0000')}")
    print("Rode novamente para atualizar senha/PIN/email — é idempotente por CPF.")


if __name__ == "__main__":
    main()
