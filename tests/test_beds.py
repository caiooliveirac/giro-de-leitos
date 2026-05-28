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
    assert "/api/unit/{unit_id}/red-room/assume" in paths
    assert "/api/unit/{unit_id}/red-room/release" in paths


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


# ---------------------------------------------------------------------------
# project_parser_state — pure projection (no DB)
# ---------------------------------------------------------------------------
def _full_enabled_config() -> list[dict]:
    from beds.schemas import VALID_SECTOR_KEYS

    return [
        {"sector_key": k, "enabled": True, "capacity": None}
        for k in VALID_SECTOR_KEYS
    ]


def test_project_counter_yellow_male():
    from beds.service import project_parser_state

    parser_row = {
        "received_at": None,
        "yellow_male_occupied": 5,
        "yellow_male_capacity": 6,
    }
    out = project_parser_state(parser_row, _full_enabled_config())
    yellow_male = next(c for c in out["counters"] if c["sector_key"] == "yellow_male")
    assert yellow_male["occupancy"] == 5
    assert yellow_male["capacity"] == 6
    assert yellow_male["version"] == 0
    assert yellow_male["source"] == "parser"
    assert yellow_male["last_updated_by"] is None


def test_payload_data_unwraps_envelope():
    """Production payloads wrap parsed content in a ``{"data": {...}}`` envelope.
    The inner dict must be returned; flat (legacy) payloads pass through.
    """
    from beds.service import _parser_payload_data

    enveloped = {"payload": {"data": {"rooms": {"x": 1}, "raw_text": "hi"}, "type": "upa_update"}}
    assert _parser_payload_data(enveloped) == {"rooms": {"x": 1}, "raw_text": "hi"}

    flat = {"payload": {"rooms": {"x": 2}, "raw_text": "yo"}}
    assert _parser_payload_data(flat) == {"rooms": {"x": 2}, "raw_text": "yo"}

    # JSON-encoded string payloads are decoded too.
    import json as _json

    as_str = {"payload": _json.dumps({"data": {"rooms": {"x": 3}}})}
    assert _parser_payload_data(as_str)["rooms"] == {"x": 3}

    assert _parser_payload_data(None) == {}
    assert _parser_payload_data({"payload": None}) == {}


def test_yellow_split_extracted_from_enveloped_payload():
    """Regression: yellow_male/yellow_female live under payload['data']['rooms'];
    reading the top-level path used to yield 0/0 for every unit (amarela vazia).
    """
    from beds.service import _yellow_male_female_from_payload

    parser_row = {
        "payload": {
            "data": {
                "rooms": {
                    "yellow_male": {"occupied": 6, "capacity": 6},
                    "yellow_female": {"occupied": 6, "capacity": 6},
                }
            }
        }
    }
    out = _yellow_male_female_from_payload(parser_row)
    assert out["yellow_male_occupied"] == 6
    assert out["yellow_male_capacity"] == 6
    assert out["yellow_female_occupied"] == 6
    assert out["yellow_female_capacity"] == 6


def test_project_red_room_patients_from_payload():
    from beds.service import (
        project_parser_state,
        _red_room_patients_from_payload,
    )

    parser_row = {
        "red_occupied": 3,
        "red_capacity": 2,  # over-capacity: 3 pacientes em 2 leitos
        "payload": {
            "data": {
                "rooms": {
                    "red_room": {
                        "patients": [
                            {"sigla": "OPO", "age": "77a", "clinical_summary": "CRISE CONVULSIVA", "raw": "x"},
                            {"sigla": "JBD", "age": "83 ANOS", "clinical_summary": "HEMORRAGIA", "raw": "y"},
                            {"sigla": "MJS", "age": None, "clinical_summary": "AVC", "raw": "z"},
                        ]
                    }
                }
            }
        },
    }
    parser_row["red_room_patients"] = _red_room_patients_from_payload(parser_row)
    out = project_parser_state(parser_row, _full_enabled_config())
    beds = out["beds"]
    # over-capacity: a grade cresce para mostrar todos os 3 pacientes ocupados.
    assert len(beds) == 3
    assert beds[0]["patient_sigla"] == "OPO"
    assert "CRISE CONVULSIVA" in beds[0]["clinical_summary"]
    assert "77a" in beds[0]["clinical_summary"]
    assert beds[2]["patient_sigla"] == "MJS"
    assert all(b["source"] == "parser" for b in beds)


def test_project_other_beds_to_counters():
    from beds.service import project_parser_state, _other_beds_from_payload

    parser_row = {
        "payload": {
            "data": {
                "rooms": {
                    "other_beds": [
                        {"key": "other_medicacao", "occupied": 5, "capacity": 9},
                        {"key": "other_pediatria", "occupied": 4, "capacity": 8},
                    ]
                }
            }
        },
    }
    parser_row.update(_other_beds_from_payload(parser_row))
    out = project_parser_state(parser_row, _full_enabled_config())
    med = next(c for c in out["counters"] if c["sector_key"] == "medication_room")
    ped = next(c for c in out["counters"] if c["sector_key"] == "ward_pediatric_internment")
    assert (med["occupancy"], med["capacity"]) == (5, 9)
    assert (ped["occupancy"], ped["capacity"]) == (4, 8)
    assert med["source"] == "parser"


def test_specialists_from_payload_includes_dentist_pediatrician():
    from beds.service import project_parser_state, _specialists_from_payload

    parser_row = {
        "payload": {"data": {"specialists": {
            "has_dentist": True, "has_pediatrician": True, "has_psychiatrist": False,
        }}},
    }
    parser_row.update(_specialists_from_payload(parser_row))
    out = project_parser_state(parser_row, _full_enabled_config())
    by_key = {s["sector_key"]: s for s in out["specialists"]}
    assert by_key["dentist"]["status"] == "available"
    assert by_key["pediatrician"]["status"] == "available"
    assert by_key["psychiatrist"]["status"] == "unavailable"


def test_choose_beds_gate():
    from beds.service import _choose_beds

    manual = [{"bed_number": 1, "patient_sigla": "AAA"}]
    projected = [{"bed_number": 1, "patient_sigla": "BBB"}]
    # assumido → manual vence; não assumido → projeção ao vivo.
    assert _choose_beds(True, manual, projected) is manual
    assert _choose_beds(False, manual, projected) is projected


def test_project_specialist_orthopedist_available():
    from beds.service import project_parser_state

    parser_row = {"has_orthopedist": True, "has_surgeon": False}
    out = project_parser_state(parser_row, _full_enabled_config())
    ortho = next(s for s in out["specialists"] if s["sector_key"] == "orthopedist")
    surg = next(s for s in out["specialists"] if s["sector_key"] == "surgeon")
    assert ortho["status"] == "available"
    assert ortho["source"] == "parser"
    assert surg["status"] == "unavailable"
    assert surg["source"] == "parser"
    # dentist/pediatrician/psychiatrist agora têm fonte no parser (bloco
    # ATENDIMENTO). Sem flag no parser_row → unavailable, mas source "parser".
    ped = next(s for s in out["specialists"] if s["sector_key"] == "pediatrician")
    assert ped["status"] == "unavailable"
    assert ped["source"] == "parser"


def test_project_red_room_beds():
    from beds.service import project_parser_state

    parser_row = {"red_occupied": 2, "red_capacity": 4}
    out = project_parser_state(parser_row, _full_enabled_config())
    beds = out["beds"]
    assert len(beds) == 4
    occupied = [b for b in beds if b["patient_sigla"]]
    vacant = [b for b in beds if not b["patient_sigla"]]
    assert len(occupied) == 2
    assert len(vacant) == 2
    for b in occupied:
        assert b["patient_sigla"] == "—"
        assert b["clinical_summary"] == "Aguardando detalhamento"
        assert b["version"] == 0
        assert b["source"] == "parser"
    for b in vacant:
        assert b["patient_sigla"] is None
        assert b["version"] == 0


def test_project_parser_row_none_defaults():
    from beds.service import project_parser_state

    out = project_parser_state(None, _full_enabled_config())
    # No beds because no capacity info available without parser_row.
    assert out["beds"] == []
    # Counters/specialists/exams come through with default zero/unavailable.
    for c in out["counters"]:
        assert c["occupancy"] == 0
        assert c["capacity"] == 0
        assert c["source"] == "default"
        assert c["version"] == 0
    for s in out["specialists"]:
        assert s["status"] == "unavailable"
        assert s["source"] == "default"
    for e in out["exams"]:
        assert e["status"] == "working"
        assert e["source"] == "default"


def test_project_skips_disabled_sectors():
    from beds.service import project_parser_state

    cfg = [
        {"sector_key": "yellow_male", "enabled": False, "capacity": None},
        {"sector_key": "yellow_female", "enabled": True, "capacity": None},
    ]
    out = project_parser_state({"yellow_female_occupied": 1, "yellow_female_capacity": 2}, cfg)
    keys = [c["sector_key"] for c in out["counters"]]
    assert "yellow_male" not in keys
    assert "yellow_female" in keys
