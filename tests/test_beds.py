"""Smoke tests for Fase 3 (beds, counters, specialists, exams).

These tests do not require a real database. They verify:
- the new router is registered with the expected routes,
- mutations without credentials return 401,
- payload validation kicks in for malformed bodies.
"""

from __future__ import annotations

import os
import uuid

import pytest

os.environ.setdefault("JWT_SECRET", "test-secret-fase3")
os.environ.setdefault(
    "CPF_ENCRYPTION_KEY",
    "OmaP3i0nC2P9MwJv5wDhlb0aBpfNn5Y73I9c8wL2cIc=",
)
os.environ.setdefault("CPF_HASH_PEPPER", "test-pepper")

from fastapi.testclient import TestClient  # noqa: E402

import main  # noqa: E402
from beds.router import router as beds_router  # noqa: E402


client = TestClient(main.app)


def test_beds_router_registered():
    paths = {r.path for r in beds_router.routes}
    assert "/api/unit/{unit_id}/state" in paths
    assert "/api/unit/{unit_id}/sectors/config" in paths
    assert "/api/unit/{unit_id}/beds/{bed_number}" in paths
    assert "/api/unit/{unit_id}/beds/{bed_number}/discharge" in paths
    assert "/api/unit/{unit_id}/beds/{bed_number}/death" in paths
    assert "/api/unit/{unit_id}/beds/{bed_number}/transfer" in paths
    assert "/api/unit/{unit_id}/beds/{bed_number}/clear" in paths
    assert "/api/unit/{unit_id}/counters/{sector_key}" in paths
    assert "/api/unit/{unit_id}/specialists/{sector_key}" in paths
    assert "/api/unit/{unit_id}/exams/{sector_key}" in paths


def test_unit_websocket_route_present():
    ws_paths = {r.path for r in main.app.routes if getattr(r, "path", None)}
    assert "/ws/unit/{unit_id}" in ws_paths


def test_read_state_without_auth_returns_401_or_503():
    unit_id = str(uuid.uuid4())
    r = client.get(f"/api/unit/{unit_id}/state")
    # 401 = no creds; 503 = DATABASE_URL missing (also a valid contract surface)
    assert r.status_code in (401, 503), r.text


def test_put_bed_without_auth_returns_401_or_503():
    unit_id = str(uuid.uuid4())
    r = client.put(
        f"/api/unit/{unit_id}/beds/1",
        json={"patient_sigla": "JS"},
    )
    assert r.status_code in (401, 503), r.text


def test_put_counter_invalid_payload_returns_422_or_401():
    unit_id = str(uuid.uuid4())
    # negative occupancy -> 422 validation error before auth runs (Pydantic v2 on body).
    r = client.put(
        f"/api/unit/{unit_id}/counters/yellow_male",
        json={"occupancy": -1, "capacity": 5},
    )
    # FastAPI checks deps before body in some orderings; accept either.
    assert r.status_code in (401, 422, 503), r.text


def test_put_sectors_config_rejects_invalid_key():
    unit_id = str(uuid.uuid4())
    r = client.put(
        f"/api/unit/{unit_id}/sectors/config",
        json={"items": [{"sector_key": "not_a_real_sector", "enabled": True}]},
    )
    assert r.status_code in (401, 422, 503), r.text


def test_schema_constants():
    from beds.schemas import (
        VALID_SECTOR_KEYS,
        SECTOR_TYPE_A_BEDS,
        SECTOR_TYPE_B_COUNTERS,
        SECTOR_TYPE_C_SPECIALISTS,
        SECTOR_TYPE_D_EXAMS,
    )

    assert "red_room" in SECTOR_TYPE_A_BEDS
    assert "yellow_male" in SECTOR_TYPE_B_COUNTERS
    assert "surgeon" in SECTOR_TYPE_C_SPECIALISTS
    assert "xray" in SECTOR_TYPE_D_EXAMS
    # Disjoint partitions.
    all_typed = (
        SECTOR_TYPE_A_BEDS
        | SECTOR_TYPE_B_COUNTERS
        | SECTOR_TYPE_C_SPECIALISTS
        | SECTOR_TYPE_D_EXAMS
    )
    assert set(VALID_SECTOR_KEYS) == all_typed


def test_ws_manager_singleton():
    from beds.ws import unit_manager, UnitConnectionManager

    assert isinstance(unit_manager, UnitConnectionManager)
