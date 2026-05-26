"""Reusable cryptographic helpers for auth/PII.

- Passwords: bcrypt cost 12
- PINs: bcrypt cost 10
- CPF: Fernet-encrypted at rest, sha256 hash for lookup/duplicate detection
- CPF mask helper for UI

Env vars:
- CPF_ENCRYPTION_KEY: urlsafe-base64 Fernet key (44 chars). Required for
  encrypt_cpf/decrypt_cpf. Generate via cryptography.fernet.Fernet.generate_key().
"""

from __future__ import annotations

import hashlib
import os
import re
from functools import lru_cache

import bcrypt
from cryptography.fernet import Fernet

_PASSWORD_ROUNDS = 12
_PIN_ROUNDS = 10


# ---------------------------------------------------------------------------
# Passwords
# ---------------------------------------------------------------------------
def hash_password(password: str) -> str:
    if not isinstance(password, str) or not password:
        raise ValueError("password must be a non-empty string")
    salt = bcrypt.gensalt(rounds=_PASSWORD_ROUNDS)
    return bcrypt.hashpw(password.encode("utf-8"), salt).decode("utf-8")


def verify_password(password: str, password_hash: str | None) -> bool:
    if not password or not password_hash:
        return False
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
    except (ValueError, TypeError):
        return False


# ---------------------------------------------------------------------------
# PINs (typically 4-6 digits)
# ---------------------------------------------------------------------------
def hash_pin(pin: str) -> str:
    if not isinstance(pin, str) or not pin:
        raise ValueError("pin must be a non-empty string")
    salt = bcrypt.gensalt(rounds=_PIN_ROUNDS)
    return bcrypt.hashpw(pin.encode("utf-8"), salt).decode("utf-8")


def verify_pin(pin: str, pin_hash: str | None) -> bool:
    if not pin or not pin_hash:
        return False
    try:
        return bcrypt.checkpw(pin.encode("utf-8"), pin_hash.encode("utf-8"))
    except (ValueError, TypeError):
        return False


# ---------------------------------------------------------------------------
# CPF
# ---------------------------------------------------------------------------
_CPF_PEPPER_ENV = "CPF_HASH_PEPPER"


def _normalize_cpf(cpf: str) -> str:
    digits = re.sub(r"\D", "", cpf or "")
    return digits


@lru_cache(maxsize=1)
def _fernet() -> Fernet:
    key = os.getenv("CPF_ENCRYPTION_KEY", "").strip()
    if not key:
        raise RuntimeError(
            "CPF_ENCRYPTION_KEY não configurada. Gere uma com "
            "`python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'`"
        )
    try:
        return Fernet(key.encode("utf-8"))
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"CPF_ENCRYPTION_KEY inválida: {exc}") from exc


def encrypt_cpf(cpf: str) -> str:
    digits = _normalize_cpf(cpf)
    if len(digits) != 11:
        raise ValueError("CPF precisa ter 11 dígitos")
    return _fernet().encrypt(digits.encode("utf-8")).decode("utf-8")


def decrypt_cpf(token: str) -> str:
    if not token:
        raise ValueError("token vazio")
    return _fernet().decrypt(token.encode("utf-8")).decode("utf-8")


def hash_cpf(cpf: str) -> str:
    """Deterministic sha256 hash for duplicate detection / lookup.

    Optional pepper via CPF_HASH_PEPPER env var (recommended in production).
    """
    digits = _normalize_cpf(cpf)
    if len(digits) != 11:
        raise ValueError("CPF precisa ter 11 dígitos")
    pepper = os.getenv(_CPF_PEPPER_ENV, "")
    payload = (pepper + digits).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def mask_cpf(cpf: str) -> str:
    """Render `***.***.***-XX` keeping only the last 2 digits visible."""
    digits = _normalize_cpf(cpf)
    if len(digits) != 11:
        return "***.***.***-**"
    return f"***.***.***-{digits[-2:]}"
