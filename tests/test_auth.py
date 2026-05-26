"""Smoke tests for Fase 2 (auth/invites/approval).

Real DB access is not assumed: when DATABASE_URL is missing the dependency
raises 503, which we still treat as a valid contract surface (the endpoint
exists and returns a documented error). Where the route should respond
without any DB (e.g. an unauthenticated admin login attempt), we monkey-patch
the get_db dependency with an in-memory fake.
"""

from __future__ import annotations

import os
import uuid
from typing import Any

import pytest

os.environ.setdefault("JWT_SECRET", "test-secret-fase2")
os.environ.setdefault(
    "CPF_ENCRYPTION_KEY",
    # generated once for tests; harmless if leaked since data is ephemeral
    "OmaP3i0nC2P9MwJv5wDhlb0aBpfNn5Y73I9c8wL2cIc=",
)
os.environ.setdefault("CPF_HASH_PEPPER", "test-pepper")


from fastapi.testclient import TestClient  # noqa: E402

import main  # noqa: E402
from auth import deps as auth_deps  # noqa: E402
from auth import service as auth_service  # noqa: E402
from auth.cpf import validate_cpf  # noqa: E402


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------
class _FakeCursor:
    def __init__(self, store: dict[str, Any]):
        self.store = store
        self.last: list[dict[str, Any]] | None = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def execute(self, sql: str, params: tuple = ()):  # noqa: D401
        self.store["calls"].append((sql.strip().split()[0].lower(), params))
        # Treat all reads as "no row".
        self.last = []

    def fetchone(self):
        return None

    def fetchall(self):
        return []


class _FakeConn:
    def __init__(self):
        self.store = {"calls": []}

    def cursor(self):
        return _FakeCursor(self.store)

    def commit(self):
        pass

    def rollback(self):
        pass

    def close(self):
        pass


def _fake_db():
    yield _FakeConn()


@pytest.fixture(autouse=True)
def _override_db():
    main.app.dependency_overrides[auth_deps.get_db] = _fake_db
    yield
    main.app.dependency_overrides.pop(auth_deps.get_db, None)


@pytest.fixture()
def client():
    return TestClient(main.app)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------
def test_router_registered():
    paths = {r.path for r in main.app.router.routes}
    expected = {
        "/api/auth/admin/login",
        "/api/auth/device/generate-code",
        "/api/auth/device/pair",
        "/api/auth/me/unit/staff",
        "/api/auth/shift/start",
        "/api/auth/shift/end",
        "/api/auth/pin/verify",
        "/api/invites",
        "/api/invites/{token}/preview",
        "/api/invites/{token}/accept",
        "/api/invites/{invite_id}/revoke",
        "/api/users/pending",
        "/api/users/{user_id}/approve",
        "/api/users/{user_id}/reject",
        "/api/users/{user_id}/suspend",
    }
    missing = expected - paths
    assert not missing, f"rotas faltando: {missing}"


def test_admin_login_invalid(client):
    resp = client.post(
        "/api/auth/admin/login",
        json={"email": "fulano@example.com", "password": "wrong"},
    )
    assert resp.status_code == 401


def test_pair_with_unknown_code(client):
    resp = client.post(
        "/api/auth/device/pair",
        json={"pairing_code": "000000", "device_fingerprint": "abc1234"},
    )
    assert resp.status_code == 404


def test_invite_preview_unknown(client):
    resp = client.get("/api/invites/some-bogus-token/preview")
    assert resp.status_code == 404


def test_protected_routes_require_session(client):
    # /api/users/pending — _current_inviter falls through to session lookup
    resp = client.get("/api/users/pending")
    assert resp.status_code in (401, 403)


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------
def test_validate_cpf_known_good():
    # Known valid CPF (used widely in test fixtures).
    assert validate_cpf("529.982.247-25") is True
    assert validate_cpf("52998224725") is True


def test_validate_cpf_rejects_bad():
    assert validate_cpf("123") is False
    assert validate_cpf("11111111111") is False
    assert validate_cpf("12345678900") is False


def test_pairing_code_format(monkeypatch):
    # Indirectly exercise _gen_pairing_code via attribute import.
    code = auth_service._gen_pairing_code()  # noqa: SLF001
    assert len(code) == 6 and code.isdigit()
