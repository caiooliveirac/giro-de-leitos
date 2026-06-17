"""FastAPI dependencies for auth, devices and shift sessions.

Cookies:
- ``admin_token``: JWT scope=admin, validade curta (8h).
- ``device_token``: JWT scope=device, carrega ``unit_id`` e ``device_id``.
  Janela deslizante: renovado a cada uso, então o tablet em uso nunca expira;
  só perde o pareamento se ficar ~90 dias parado.
- ``session_token``: JWT scope=shift, 12h, carrega ``session_id``/``user_id``.

JWT secret: env ``JWT_SECRET``. Em dev gera um fallback determinístico-por-processo
para não quebrar import; em produção logamos um warning quando isso ocorre.
"""

from __future__ import annotations

import logging
import os
import secrets as _secrets
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Iterator, Optional
from uuid import UUID

from fastapi import Cookie, Depends, Header, HTTPException, Request, Response, status
from jose import JWTError, jwt
from psycopg.rows import dict_row

import db as _db
from auth import crypto

logger = logging.getLogger(__name__)

JWT_ALGORITHM = "HS256"
ADMIN_TOKEN_TTL = timedelta(hours=8)
DEVICE_TOKEN_TTL = timedelta(days=90)
# Janela deslizante: a cada requisição válida, se faltar menos que isto pra
# expirar, empurramos ``expires_at`` pra frente e reemitimos o cookie. Assim um
# tablet em uso nunca expira; só cai se ficar ~90 dias sem uso. O limiar evita
# escrever no banco em toda requisição (no máx. uma renovação a cada ~45 dias).
DEVICE_TOKEN_RENEW_WINDOW = DEVICE_TOKEN_TTL / 2
# Sessão não expira por tempo: "logou, está logado". A sessão persiste como o
# pareamento do dispositivo — validade longa com janela deslizante, renovada por
# uso (ver get_current_session). Não há conceito de "plantão" que caduca; o que
# protege escritas sensíveis é o PIN de 4 dígitos (require_pin_confirm), não a
# expiração da sessão.
SHIFT_TOKEN_TTL = DEVICE_TOKEN_TTL
SHIFT_TOKEN_RENEW_WINDOW = SHIFT_TOKEN_TTL / 2
PAIRING_CODE_TTL = timedelta(minutes=10)

COOKIE_ADMIN = "admin_token"
COOKIE_DEVICE = "device_token"
COOKIE_SESSION = "session_token"


def _resolve_secret() -> str:
    secret = os.getenv("JWT_SECRET", "").strip()
    if secret:
        return secret
    # Dev fallback — per-process random; tokens won't survive restart.
    logger.warning(
        "JWT_SECRET ausente — gerando segredo efêmero. NÃO use em produção."
    )
    return _secrets.token_urlsafe(48)


_SECRET = _resolve_secret()


def _is_secure_cookie() -> bool:
    base = os.getenv("PUBLIC_BASE_URL", "").strip().lower()
    return base.startswith("https://")


def encode_token(payload: dict[str, Any], ttl: timedelta) -> str:
    now = datetime.now(timezone.utc)
    body = {**payload, "iat": int(now.timestamp()), "exp": int((now + ttl).timestamp())}
    return jwt.encode(body, _SECRET, algorithm=JWT_ALGORITHM)


def decode_token(token: str) -> dict[str, Any]:
    return jwt.decode(token, _SECRET, algorithms=[JWT_ALGORITHM])


def set_admin_cookie(response, token: str) -> None:
    response.set_cookie(
        COOKIE_ADMIN,
        token,
        max_age=int(ADMIN_TOKEN_TTL.total_seconds()),
        httponly=True,
        samesite="lax",
        secure=_is_secure_cookie(),
        path="/",
    )


def set_device_cookie(response, token: str) -> None:
    response.set_cookie(
        COOKIE_DEVICE,
        token,
        max_age=int(DEVICE_TOKEN_TTL.total_seconds()),
        httponly=True,
        samesite="lax",
        secure=_is_secure_cookie(),
        path="/",
    )


def set_session_cookie(response, token: str) -> None:
    response.set_cookie(
        COOKIE_SESSION,
        token,
        max_age=int(SHIFT_TOKEN_TTL.total_seconds()),
        httponly=True,
        samesite="lax",
        secure=_is_secure_cookie(),
        path="/",
    )


def clear_session_cookie(response) -> None:
    response.delete_cookie(COOKIE_SESSION, path="/")


# ---------------------------------------------------------------------------
# DB
# ---------------------------------------------------------------------------
@contextmanager
def _open_conn() -> Iterator[Any]:
    if not _db.is_database_configured():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="DATABASE_URL não configurada.",
        )
    conn = _db._connect()  # noqa: SLF001 — single point of truth
    try:
        conn.row_factory = dict_row
        yield conn
        conn.commit()
    except HTTPException:
        conn.rollback()
        raise
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def get_db() -> Iterator[Any]:
    """Yields a psycopg connection with dict_row. Commits on success."""
    with _open_conn() as conn:
        yield conn


# ---------------------------------------------------------------------------
# Client meta
# ---------------------------------------------------------------------------
def client_meta(request: Request) -> dict[str, Optional[str]]:
    ip = None
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        ip = fwd.split(",")[0].strip()
    elif request.client:
        ip = request.client.host
    return {"client_ip": ip, "user_agent": request.headers.get("user-agent")}


# ---------------------------------------------------------------------------
# Token helpers
# ---------------------------------------------------------------------------
def _read_cookie(request: Request, name: str) -> Optional[str]:
    return request.cookies.get(name)


def _load_user(conn, user_id: str) -> Optional[dict[str, Any]]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT id, name, role, status, unit_id, cargo, email, photo_url,
                   cpf_encrypted, coren_crm
              FROM users
             WHERE id = %s
            """,
            (user_id,),
        )
        return cur.fetchone()


# ---------------------------------------------------------------------------
# Admin auth dependency
# ---------------------------------------------------------------------------
def get_current_admin(
    request: Request,
    conn=Depends(get_db),
) -> dict[str, Any]:
    raw = _read_cookie(request, COOKIE_ADMIN)
    if not raw:
        raise HTTPException(status_code=401, detail="Não autenticado (admin).")
    try:
        data = decode_token(raw)
    except JWTError as exc:
        raise HTTPException(status_code=401, detail="Token inválido.") from exc
    if data.get("scope") != "admin":
        raise HTTPException(status_code=401, detail="Escopo inválido.")
    user_id = data.get("sub")
    if not user_id:
        raise HTTPException(status_code=401, detail="Token sem sub.")
    user = _load_user(conn, user_id)
    if not user or user["role"] != "admin" or user["status"] != "active":
        raise HTTPException(status_code=401, detail="Admin inválido.")
    return user


# ---------------------------------------------------------------------------
# Device context (any operator endpoint)
# ---------------------------------------------------------------------------
def get_device_context(
    request: Request, conn=Depends(get_db), response: Response = None
) -> dict[str, Any]:
    raw = _read_cookie(request, COOKIE_DEVICE)
    if not raw:
        raise HTTPException(status_code=401, detail="Dispositivo não pareado.")
    try:
        data = decode_token(raw)
    except JWTError as exc:
        raise HTTPException(status_code=401, detail="Token de dispositivo inválido.") from exc
    if data.get("scope") != "device":
        raise HTTPException(status_code=401, detail="Escopo inválido.")
    unit_id = data.get("unit_id")
    device_id = data.get("device_id")
    if not unit_id or not device_id:
        raise HTTPException(status_code=401, detail="Token de dispositivo incompleto.")
    # Make sure the device is still valid in DB.
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT id, unit_id, label, expires_at, revoked_at
              FROM trusted_devices
             WHERE id = %s
            """,
            (device_id,),
        )
        dev = cur.fetchone()
    if not dev or dev["revoked_at"] is not None:
        raise HTTPException(status_code=401, detail="Dispositivo revogado.")
    now = datetime.now(timezone.utc)
    if dev["expires_at"] and dev["expires_at"] < now:
        raise HTTPException(status_code=401, detail="Pareamento expirado.")
    # Janela deslizante: renova por uso para o tablet ativo nunca expirar.
    # Só escreve no banco/reemite o cookie quando passa do meio da janela, e
    # somente quando há um ``response`` pra reemitir o cookie junto — caso
    # contrário banco e cookie ficariam dessincronizados (chamadas diretas que
    # não repassam ``response`` apenas leem, sem renovar).
    needs_renew = dev["expires_at"] is None or (dev["expires_at"] - now) < DEVICE_TOKEN_RENEW_WINDOW
    if response is not None and needs_renew:
        new_expires = now + DEVICE_TOKEN_TTL
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE trusted_devices SET expires_at = %s WHERE id = %s",
                (new_expires, str(dev["id"])),
            )
        token = encode_token(
            {
                "scope": "device",
                "unit_id": str(dev["unit_id"]),
                "device_id": str(dev["id"]),
            },
            DEVICE_TOKEN_TTL,
        )
        set_device_cookie(response, token)
    return {"unit_id": str(dev["unit_id"]), "device_id": str(dev["id"])}


# ---------------------------------------------------------------------------
# Shift session (device + session)
# ---------------------------------------------------------------------------
def get_current_session(
    request: Request,
    conn=Depends(get_db),
    device=Depends(get_device_context),
    response: Response = None,
) -> dict[str, Any]:
    raw = _read_cookie(request, COOKIE_SESSION)
    if not raw:
        raise HTTPException(status_code=401, detail="Sem sessão de plantão.")
    try:
        data = decode_token(raw)
    except JWTError as exc:
        raise HTTPException(status_code=401, detail="Sessão inválida.") from exc
    if data.get("scope") != "shift":
        raise HTTPException(status_code=401, detail="Escopo inválido.")
    session_id = data.get("session_id")
    if not session_id:
        raise HTTPException(status_code=401, detail="Sessão sem id.")
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT id, user_id, device_id, started_at, expires_at, ended_at
              FROM auth_sessions
             WHERE id = %s
            """,
            (session_id,),
        )
        sess = cur.fetchone()
    if not sess:
        raise HTTPException(status_code=401, detail="Sessão inexistente.")
    if sess["ended_at"] is not None:
        raise HTTPException(status_code=401, detail="Sessão encerrada.")
    if sess["expires_at"] < datetime.now(timezone.utc):
        raise HTTPException(status_code=401, detail="Sessão expirada.")
    user = _load_user(conn, str(sess["user_id"]))
    if not user or user["status"] != "active":
        raise HTTPException(status_code=401, detail="Usuário inativo.")
    if user["unit_id"] and str(user["unit_id"]) != device["unit_id"]:
        raise HTTPException(status_code=403, detail="Usuário não pertence à unidade do dispositivo.")
    # Janela deslizante: enquanto a sessão estiver em uso, empurramos a expiração
    # pra frente e reemitimos o cookie. Assim "logou, está logado" — a sessão só
    # cai após ~90 dias de inatividade. Só renova quando há ``response`` pra
    # reemitir o cookie (senão banco e cookie ficam dessincronizados) e quando
    # passou do meio da janela, evitando escrever no banco a cada requisição.
    now = datetime.now(timezone.utc)
    needs_renew = sess["expires_at"] is None or (sess["expires_at"] - now) < SHIFT_TOKEN_RENEW_WINDOW
    if response is not None and needs_renew:
        new_expires = now + SHIFT_TOKEN_TTL
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE auth_sessions SET expires_at = %s WHERE id = %s",
                (new_expires, str(sess["id"])),
            )
        token = encode_token(
            {"scope": "shift", "session_id": str(sess["id"]), "sub": str(sess["user_id"])},
            SHIFT_TOKEN_TTL,
        )
        set_session_cookie(response, token)
        sess["expires_at"] = new_expires
    return {"session": sess, "user": user, "device": device}


# ---------------------------------------------------------------------------
# Role guard
# ---------------------------------------------------------------------------
def require_role(*roles: str):
    allowed = set(roles)

    def _checker(ctx=Depends(get_current_session)) -> dict[str, Any]:
        user = ctx["user"]
        if user["role"] not in allowed:
            raise HTTPException(status_code=403, detail="Permissão insuficiente.")
        return ctx

    return _checker


# ---------------------------------------------------------------------------
# PIN confirmation (sensitive actions)
# ---------------------------------------------------------------------------
def require_pin_confirm(
    request: Request,
    x_pin_confirm: Optional[str] = Header(default=None, alias="X-PIN-Confirm"),
    ctx=Depends(get_current_session),
    conn=Depends(get_db),
) -> dict[str, Any]:
    if not x_pin_confirm:
        raise HTTPException(status_code=403, detail="PIN de confirmação obrigatório.")
    user_id = ctx["user"]["id"]
    with conn.cursor() as cur:
        cur.execute("SELECT pin_hash FROM users WHERE id = %s", (str(user_id),))
        row = cur.fetchone()
    if not row or not crypto.verify_pin(x_pin_confirm, row["pin_hash"]):
        raise HTTPException(status_code=403, detail="PIN inválido.")
    return ctx


__all__ = [
    "ADMIN_TOKEN_TTL",
    "DEVICE_TOKEN_TTL",
    "DEVICE_TOKEN_RENEW_WINDOW",
    "SHIFT_TOKEN_TTL",
    "PAIRING_CODE_TTL",
    "COOKIE_ADMIN",
    "COOKIE_DEVICE",
    "COOKIE_SESSION",
    "client_meta",
    "clear_session_cookie",
    "decode_token",
    "encode_token",
    "get_current_admin",
    "get_current_session",
    "get_db",
    "get_device_context",
    "require_pin_confirm",
    "require_role",
    "set_admin_cookie",
    "set_device_cookie",
    "set_session_cookie",
]
