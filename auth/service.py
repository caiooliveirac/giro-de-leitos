"""Business logic for auth, devices, invites, approval."""

from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Optional
from uuid import UUID

from fastapi import HTTPException, status

from auth import crypto
from auth.cpf import validate_cpf
from auth.deps import DEVICE_TOKEN_TTL, PAIRING_CODE_TTL, SHIFT_TOKEN_TTL

INVITE_TTL = timedelta(days=7)
INVITE_RATE_PER_HOUR = 10


# ---------------------------------------------------------------------------
# Admin auth
# ---------------------------------------------------------------------------
def authenticate_admin(conn, email_or_username: str, password: str) -> dict[str, Any]:
    if not email_or_username or not password:
        raise HTTPException(status_code=401, detail="Credenciais inválidas.")
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT id, name, email, role, status, password_hash, unit_id, photo_url
              FROM users
             WHERE email = %s
            """,
            (email_or_username.lower().strip(),),
        )
        row = cur.fetchone()
    if not row:
        raise HTTPException(status_code=401, detail="Credenciais inválidas.")
    if row["role"] != "admin" or row["status"] != "active":
        raise HTTPException(status_code=401, detail="Usuário sem acesso administrativo.")
    if not crypto.verify_password(password, row["password_hash"]):
        raise HTTPException(status_code=401, detail="Credenciais inválidas.")
    return row


# ---------------------------------------------------------------------------
# Device pairing
# ---------------------------------------------------------------------------
def _gen_pairing_code() -> str:
    return f"{secrets.randbelow(1_000_000):06d}"


def create_pairing_code(conn, unit_id: UUID | str, created_by: UUID | str) -> dict[str, Any]:
    expires_at = datetime.now(timezone.utc) + PAIRING_CODE_TTL
    # try a handful of codes to avoid theoretical clash with active codes
    for _ in range(5):
        code = _gen_pairing_code()
        try:
            with conn.cursor() as cur:
                # Use a pending row in trusted_devices, fingerprint placeholder is the code itself.
                cur.execute(
                    """
                    INSERT INTO trusted_devices
                        (unit_id, device_fingerprint, label, paired_at, expires_at,
                         pairing_code, pairing_code_expires_at)
                    VALUES (%s, %s, %s, NULL, %s, %s, %s)
                    ON CONFLICT (unit_id, device_fingerprint) DO NOTHING
                    RETURNING id
                    """,
                    (
                        str(unit_id),
                        f"pending:{code}",
                        "pending",
                        expires_at,
                        code,
                        expires_at,
                    ),
                )
                row = cur.fetchone()
                if row:
                    return {"pairing_code": code, "expires_at": expires_at}
        except Exception:  # noqa: BLE001
            conn.rollback()
            continue
    raise HTTPException(status_code=500, detail="Não foi possível gerar código.")


def pair_device(
    conn,
    pairing_code: str,
    device_fingerprint: str,
    label: Optional[str],
) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT id, unit_id, pairing_code_expires_at
              FROM trusted_devices
             WHERE pairing_code = %s AND paired_at IS NULL
             FOR UPDATE
            """,
            (pairing_code,),
        )
        row = cur.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Código inválido.")
    if row["pairing_code_expires_at"] and row["pairing_code_expires_at"] < now:
        raise HTTPException(status_code=410, detail="Código expirado.")

    expires_at = now + DEVICE_TOKEN_TTL
    with conn.cursor() as cur:
        # Make sure we don't clash with a previously paired device having the same fingerprint
        cur.execute(
            """
            UPDATE trusted_devices
               SET revoked_at = NOW()
             WHERE unit_id = %s AND device_fingerprint = %s AND id <> %s AND revoked_at IS NULL
            """,
            (str(row["unit_id"]), device_fingerprint, str(row["id"])),
        )
        cur.execute(
            """
            UPDATE trusted_devices
               SET device_fingerprint = %s,
                   label = COALESCE(%s, label),
                   paired_at = NOW(),
                   expires_at = %s,
                   pairing_code = NULL,
                   pairing_code_expires_at = NULL
             WHERE id = %s
             RETURNING id, unit_id, expires_at
            """,
            (device_fingerprint, label, expires_at, str(row["id"])),
        )
        result = cur.fetchone()
    return {
        "device_id": str(result["id"]),
        "unit_id": str(result["unit_id"]),
        "expires_at": result["expires_at"],
    }


# ---------------------------------------------------------------------------
# Self-pair (device pairing by existing user without coordinator code)
# ---------------------------------------------------------------------------
_GENERIC_CREDS_MSG = "Credenciais inválidas."
SELF_PAIR_WINDOW = timedelta(minutes=15)
SELF_PAIR_MAX_FAILS = 5


def find_user_by_username(conn, username: str) -> Optional[dict[str, Any]]:
    if not username:
        return None
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT id, name, role, status, unit_id, cargo, photo_url,
                   cpf_encrypted, coren_crm, password_hash, pin_hash
              FROM users
             WHERE LOWER(username) = LOWER(%s)
            """,
            (username.strip(),),
        )
        return cur.fetchone()


def find_user_by_cpf_digits(conn, cpf_digits: str) -> Optional[dict[str, Any]]:
    if not cpf_digits or len(cpf_digits) != 11:
        return None
    try:
        cpf_hash = crypto.hash_cpf(cpf_digits)
    except Exception:  # noqa: BLE001
        return None
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT id, name, role, status, unit_id, cargo, photo_url,
                   cpf_encrypted, coren_crm, password_hash, pin_hash
              FROM users
             WHERE cpf_hash = %s
            """,
            (cpf_hash,),
        )
        return cur.fetchone()


def _count_recent_self_pair_fails(conn, *, client_ip: Optional[str], cpf_hash: Optional[str]) -> int:
    """Counts recent device.self_pair.fail audit rows by IP or cpf_hash."""
    if not client_ip and not cpf_hash:
        return 0
    clauses = ["action = 'device.self_pair.fail'", "created_at > NOW() - INTERVAL '15 minutes'"]
    params: list[Any] = []
    ors: list[str] = []
    if client_ip:
        ors.append("client_ip = %s")
        params.append(client_ip)
    if cpf_hash:
        ors.append("entity_id = %s")
        params.append(cpf_hash)
    clauses.append("(" + " OR ".join(ors) + ")")
    sql = "SELECT COUNT(*)::int AS n FROM audit_log WHERE " + " AND ".join(clauses)
    try:
        with conn.cursor() as cur:
            cur.execute(sql, tuple(params))
            row = cur.fetchone()
        return int(row["n"]) if row else 0
    except Exception:  # noqa: BLE001
        return 0


def check_self_pair_rate_limit(conn, *, client_ip: Optional[str], cpf_hash: Optional[str]) -> None:
    n = _count_recent_self_pair_fails(conn, client_ip=client_ip, cpf_hash=cpf_hash)
    if n >= SELF_PAIR_MAX_FAILS:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Muitas tentativas. Tente novamente em alguns minutos.",
            headers={"Retry-After": "900"},
        )


def self_pair_device(
    conn,
    *,
    cpf_digits: str,
    password: str,
    pin: str,
    device_fingerprint: str,
    label: Optional[str],
    username: Optional[str] = None,
) -> dict[str, Any]:
    """Pair a device using an existing active user's own credentials.

    Failure modes 1-3 (unknown CPF, wrong password, wrong PIN) all collapse to a
    single generic 401 to avoid enumeration. Statuses 4-5 are explicit because
    they aren't credential-enumerable.
    """
    cpf_hash: Optional[str] = None
    try:
        cpf_hash = crypto.hash_cpf(cpf_digits) if cpf_digits and len(cpf_digits) == 11 else None
    except Exception:  # noqa: BLE001
        cpf_hash = None

    if username:
        user = find_user_by_username(conn, username)
    else:
        user = find_user_by_cpf_digits(conn, cpf_digits)
    if not user:
        raise HTTPException(status_code=401, detail=_GENERIC_CREDS_MSG)
    if not crypto.verify_password(password, user.get("password_hash")):
        raise HTTPException(status_code=401, detail=_GENERIC_CREDS_MSG)
    if not crypto.verify_pin(pin, user.get("pin_hash")):
        raise HTTPException(status_code=401, detail=_GENERIC_CREDS_MSG)
    if user["status"] != "active":
        raise HTTPException(status_code=403, detail="Conta não ativa.")
    if not user.get("unit_id"):
        raise HTTPException(status_code=403, detail="Usuário sem unidade.")

    unit_id = str(user["unit_id"])
    now = datetime.now(timezone.utc)
    device_expires = now + DEVICE_TOKEN_TTL
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO trusted_devices
                (unit_id, device_fingerprint, label, paired_at, expires_at)
            VALUES (%s, %s, %s, NOW(), %s)
            ON CONFLICT (unit_id, device_fingerprint) DO UPDATE
              SET label = COALESCE(EXCLUDED.label, trusted_devices.label),
                  paired_at = NOW(),
                  expires_at = EXCLUDED.expires_at,
                  revoked_at = NULL,
                  pairing_code = NULL,
                  pairing_code_expires_at = NULL
            RETURNING id, unit_id, expires_at
            """,
            (unit_id, device_fingerprint, label, device_expires),
        )
        dev = cur.fetchone()

    device_id = str(dev["id"])
    sess = start_shift_no_pin(conn, user_id=user["id"], device_id=device_id)
    return {
        "device_id": device_id,
        "unit_id": dev["unit_id"],
        "session": sess,
        "user": user,
    }


def start_shift_no_pin(conn, *, user_id: UUID | str, device_id: str) -> dict[str, Any]:
    """Open a shift session for an already-authenticated user (no PIN re-check).

    Used by self_pair where PIN was already verified upstream.
    """
    expires_at = datetime.now(timezone.utc) + SHIFT_TOKEN_TTL
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE auth_sessions
               SET ended_at = NOW(), end_reason = 'superseded'
             WHERE user_id = %s AND ended_at IS NULL
            """,
            (str(user_id),),
        )
        cur.execute(
            """
            INSERT INTO auth_sessions (user_id, device_id, expires_at)
            VALUES (%s, %s, %s)
            RETURNING id, user_id, device_id, started_at, expires_at
            """,
            (str(user_id), device_id, expires_at),
        )
        return cur.fetchone()


# ---------------------------------------------------------------------------
# Shift session
# ---------------------------------------------------------------------------
def start_shift(conn, user_id: UUID | str, pin: str, device_id: str) -> dict[str, Any]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id, status, pin_hash, unit_id FROM users WHERE id = %s",
            (str(user_id),),
        )
        user = cur.fetchone()
    if not user or user["status"] != "active":
        raise HTTPException(status_code=403, detail="Usuário inválido.")
    if not crypto.verify_pin(pin, user["pin_hash"]):
        raise HTTPException(status_code=401, detail="PIN incorreto.")
    expires_at = datetime.now(timezone.utc) + SHIFT_TOKEN_TTL
    with conn.cursor() as cur:
        # close existing live sessions for this user on this device
        cur.execute(
            """
            UPDATE auth_sessions
               SET ended_at = NOW(), end_reason = 'superseded'
             WHERE user_id = %s AND ended_at IS NULL
            """,
            (str(user_id),),
        )
        cur.execute(
            """
            INSERT INTO auth_sessions (user_id, device_id, expires_at)
            VALUES (%s, %s, %s)
            RETURNING id, user_id, device_id, started_at, expires_at
            """,
            (str(user_id), device_id, expires_at),
        )
        sess = cur.fetchone()
    return sess


def end_shift(conn, session_id: UUID | str, reason: str = "logout") -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE auth_sessions
               SET ended_at = NOW(), end_reason = %s
             WHERE id = %s AND ended_at IS NULL
            """,
            (reason, str(session_id)),
        )


def verify_pin(conn, user_id: UUID | str, pin: str) -> bool:
    with conn.cursor() as cur:
        cur.execute("SELECT pin_hash FROM users WHERE id = %s", (str(user_id),))
        row = cur.fetchone()
    return bool(row and crypto.verify_pin(pin, row["pin_hash"]))


# ---------------------------------------------------------------------------
# Invites
# ---------------------------------------------------------------------------
def _check_rate_limit(conn, created_by: str) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT COUNT(*)::int AS n
              FROM invites
             WHERE created_by = %s AND created_at > NOW() - INTERVAL '1 hour'
            """,
            (created_by,),
        )
        row = cur.fetchone()
    if row and row["n"] >= INVITE_RATE_PER_HOUR:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Limite de convites por hora atingido.",
        )


def create_invite(
    conn,
    *,
    created_by: dict[str, Any],
    type: str,
    target_unit_id: Optional[UUID | str] = None,
) -> dict[str, Any]:
    creator_role = created_by["role"]
    creator_id = str(created_by["id"])

    if type == "coordinator":
        if creator_role != "admin":
            raise HTTPException(status_code=403, detail="Somente admin cria coordenador.")
        if not target_unit_id:
            raise HTTPException(status_code=400, detail="target_unit_id obrigatório.")
        unit_id = str(target_unit_id)
    elif type == "professional":
        if creator_role not in ("coordinator", "admin"):
            raise HTTPException(status_code=403, detail="Sem permissão.")
        if creator_role == "coordinator":
            unit_id = str(created_by["unit_id"]) if created_by.get("unit_id") else None
            if not unit_id:
                raise HTTPException(status_code=400, detail="Coordenador sem unidade vinculada.")
        else:
            if not target_unit_id:
                raise HTTPException(status_code=400, detail="target_unit_id obrigatório.")
            unit_id = str(target_unit_id)
    else:
        raise HTTPException(status_code=400, detail="Tipo de convite inválido.")

    _check_rate_limit(conn, creator_id)

    token = secrets.token_urlsafe(32)
    expires_at = datetime.now(timezone.utc) + INVITE_TTL
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO invites (token, type, target_unit_id, created_by, expires_at)
            VALUES (%s, %s, %s, %s, %s)
            RETURNING id, token, type, target_unit_id, expires_at, created_at, status
            """,
            (token, type, unit_id, creator_id, expires_at),
        )
        row = cur.fetchone()
    return row


def preview_invite(conn, token: str) -> dict[str, Any]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT i.type, i.expires_at, i.status, i.target_unit_id,
                   u.canonical_name AS unit_name,
                   creator.name AS inviter_name
              FROM invites i
              LEFT JOIN units u ON u.id = i.target_unit_id
              LEFT JOIN users creator ON creator.id = i.created_by
             WHERE i.token = %s
            """,
            (token,),
        )
        row = cur.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Convite não encontrado.")
    if row["status"] != "active":
        raise HTTPException(status_code=410, detail="Convite indisponível.")
    if row["expires_at"] < datetime.now(timezone.utc):
        raise HTTPException(status_code=410, detail="Convite expirado.")
    return {
        "type": row["type"],
        "unit_name": row["unit_name"],
        "inviter_name": row["inviter_name"] or "Equipe Giro",
        "expires_at": row["expires_at"],
    }


def list_invites(conn, *, user: dict[str, Any]) -> list[dict[str, Any]]:
    with conn.cursor() as cur:
        if user["role"] == "admin":
            cur.execute(
                """
                SELECT id, type, target_unit_id, status, created_at, expires_at, used_by
                  FROM invites
                 ORDER BY created_at DESC
                 LIMIT 200
                """
            )
        else:
            cur.execute(
                """
                SELECT id, type, target_unit_id, status, created_at, expires_at, used_by
                  FROM invites
                 WHERE created_by = %s
                 ORDER BY created_at DESC
                 LIMIT 200
                """,
                (str(user["id"]),),
            )
        return cur.fetchall()


def revoke_invite(conn, *, user: dict[str, Any], invite_id: UUID | str) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id, created_by, status FROM invites WHERE id = %s FOR UPDATE",
            (str(invite_id),),
        )
        row = cur.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Convite não encontrado.")
    if user["role"] != "admin" and str(row["created_by"]) != str(user["id"]):
        raise HTTPException(status_code=403, detail="Sem permissão para revogar.")
    if row["status"] != "active":
        return
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE invites SET status = 'revoked' WHERE id = %s",
            (str(invite_id),),
        )


# ---------------------------------------------------------------------------
# Invite acceptance
# ---------------------------------------------------------------------------
def accept_invite(conn, token: str, payload) -> dict[str, Any]:
    if not validate_cpf(payload.cpf):
        raise HTTPException(status_code=400, detail="CPF inválido.")
    cpf_hash = crypto.hash_cpf(payload.cpf)

    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT id, type, target_unit_id, expires_at, status
              FROM invites
             WHERE token = %s
             FOR UPDATE
            """,
            (token,),
        )
        invite = cur.fetchone()
    if not invite:
        raise HTTPException(status_code=404, detail="Convite não encontrado.")
    if invite["status"] != "active":
        raise HTTPException(status_code=410, detail="Convite indisponível.")
    if invite["expires_at"] < datetime.now(timezone.utc):
        raise HTTPException(status_code=410, detail="Convite expirado.")

    role = "coordinator" if invite["type"] == "coordinator" else "professional"
    target_unit_id = invite["target_unit_id"]

    # Idempotency: duplicate CPF check
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id, unit_id, status FROM users WHERE cpf_hash = %s",
            (cpf_hash,),
        )
        dup = cur.fetchone()
    if dup:
        same_unit = target_unit_id and dup["unit_id"] and str(dup["unit_id"]) == str(target_unit_id)
        if dup["status"] in ("pending", "active") and same_unit:
            raise HTTPException(status_code=409, detail="CPF já cadastrado nesta unidade.")
        raise HTTPException(status_code=409, detail="CPF já cadastrado no sistema.")

    cpf_enc = crypto.encrypt_cpf(payload.cpf)
    pwd_hash = crypto.hash_password(payload.password)
    pin_hash = crypto.hash_pin(payload.pin)
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO users (
                name, cpf_encrypted, cpf_hash, phone, photo_url, role, cargo,
                coren_crm, unit_id, status, password_hash, pin_hash, lgpd_accepted_at
            ) VALUES (
                %s, %s, %s, %s, %s, %s, %s, %s, %s, 'pending', %s, %s, NOW()
            )
            RETURNING id, name, role, status, unit_id, cargo, photo_url, coren_crm
            """,
            (
                payload.name.strip(),
                cpf_enc,
                cpf_hash,
                payload.phone,
                payload.photo_url,
                role,
                payload.cargo,
                payload.coren_crm,
                str(target_unit_id) if target_unit_id else None,
                pwd_hash,
                pin_hash,
            ),
        )
        user = cur.fetchone()
        cur.execute(
            """
            UPDATE invites
               SET status = 'used', used_by = %s, used_at = NOW()
             WHERE id = %s
            """,
            (str(user["id"]), str(invite["id"])),
        )
    return user


# ---------------------------------------------------------------------------
# Approval flow
# ---------------------------------------------------------------------------
def _ensure_approver_can_act(approver: dict[str, Any], target: dict[str, Any]) -> None:
    if approver["role"] == "admin":
        if target["role"] not in ("coordinator", "professional"):
            raise HTTPException(status_code=403, detail="Alvo inválido.")
        return
    if approver["role"] == "coordinator":
        if target["role"] != "professional":
            raise HTTPException(status_code=403, detail="Coordenador só age sobre profissionais.")
        if not approver.get("unit_id") or str(approver["unit_id"]) != str(target.get("unit_id")):
            raise HTTPException(status_code=403, detail="Fora da sua unidade.")
        return
    raise HTTPException(status_code=403, detail="Sem permissão.")


def _load_target(conn, user_id: UUID | str) -> dict[str, Any]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id, name, role, status, unit_id, phone FROM users WHERE id = %s",
            (str(user_id),),
        )
        row = cur.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Usuário não encontrado.")
    return row


def approve_user(conn, approver: dict[str, Any], user_id: UUID | str) -> dict[str, Any]:
    target = _load_target(conn, user_id)
    _ensure_approver_can_act(approver, target)
    if target["status"] == "active":
        return target
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE users
               SET status = 'active', approved_at = NOW(), approved_by = %s
             WHERE id = %s
             RETURNING id, name, role, status, unit_id, phone
            """,
            (str(approver["id"]), str(user_id)),
        )
        return cur.fetchone()


def reject_user(conn, approver: dict[str, Any], user_id: UUID | str) -> dict[str, Any]:
    target = _load_target(conn, user_id)
    _ensure_approver_can_act(approver, target)
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE users
               SET status = 'suspended'
             WHERE id = %s
             RETURNING id, name, role, status, unit_id, phone
            """,
            (str(user_id),),
        )
        return cur.fetchone()


def suspend_user(conn, approver: dict[str, Any], user_id: UUID | str) -> dict[str, Any]:
    target = _load_target(conn, user_id)
    _ensure_approver_can_act(approver, target)
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE users
               SET status = 'suspended'
             WHERE id = %s
             RETURNING id, name, role, status, unit_id, phone
            """,
            (str(user_id),),
        )
        return cur.fetchone()


def list_unit_staff(conn, unit_id: UUID | str) -> list[dict[str, Any]]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT id, name, role, cargo, photo_url, status, unit_id, cpf_encrypted
              FROM users
             WHERE unit_id = %s AND status = 'active'
             ORDER BY name ASC
            """,
            (str(unit_id),),
        )
        return cur.fetchall()


def list_unit_members(conn, unit_id: UUID | str) -> list[dict[str, Any]]:
    """All users (any status) tied to a unit. For admin team management."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT id, name, role, cargo, coren_crm, phone, photo_url, status,
                   unit_id, cpf_encrypted, created_at, approved_at
              FROM users
             WHERE unit_id = %s AND role IN ('coordinator', 'professional')
             ORDER BY
                CASE status
                    WHEN 'pending' THEN 0
                    WHEN 'active' THEN 1
                    WHEN 'suspended' THEN 2
                    ELSE 3
                END,
                name ASC
            """,
            (str(unit_id),),
        )
        return cur.fetchall()


def list_pending(conn, approver: dict[str, Any]) -> list[dict[str, Any]]:
    with conn.cursor() as cur:
        if approver["role"] == "admin":
            cur.execute(
                """
                SELECT id, name, role, cargo, unit_id, created_at, cpf_encrypted, coren_crm
                  FROM users
                 WHERE status = 'pending' AND role IN ('coordinator','professional')
                 ORDER BY created_at ASC
                """
            )
        elif approver["role"] == "coordinator":
            if not approver.get("unit_id"):
                return []
            cur.execute(
                """
                SELECT id, name, role, cargo, unit_id, created_at, cpf_encrypted, coren_crm
                  FROM users
                 WHERE status = 'pending' AND role = 'professional' AND unit_id = %s
                 ORDER BY created_at ASC
                """,
                (str(approver["unit_id"]),),
            )
        else:
            return []
        return cur.fetchall()
