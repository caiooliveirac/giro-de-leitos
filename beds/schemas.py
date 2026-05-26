"""Pydantic schemas for Fase 3 — beds, counters, specialists, exams."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


# ---------------------------------------------------------------------------
# Sector taxonomy
# ---------------------------------------------------------------------------
VALID_SECTOR_KEYS: tuple[str, ...] = (
    "red_room",
    "yellow_female",
    "yellow_male",
    "yellow_unisex",
    "isolation_adult_m",
    "isolation_adult_f",
    "isolation_adult_unisex",
    "isolation_pediatric",
    "obituary",
    "pediatric_observation",
    "surgeon",
    "orthopedist",
    "dentist",
    "pediatrician",
    "xray",
    "ecg",
    "lab",
    "ultrasound",
    "tomography",
)

SECTOR_TYPE_A_BEDS: frozenset[str] = frozenset({"red_room"})
SECTOR_TYPE_B_COUNTERS: frozenset[str] = frozenset({
    "yellow_female",
    "yellow_male",
    "yellow_unisex",
    "isolation_adult_m",
    "isolation_adult_f",
    "isolation_adult_unisex",
    "isolation_pediatric",
    "obituary",
    "pediatric_observation",
})
SECTOR_TYPE_C_SPECIALISTS: frozenset[str] = frozenset({
    "surgeon",
    "orthopedist",
    "dentist",
    "pediatrician",
})
SECTOR_TYPE_D_EXAMS: frozenset[str] = frozenset({
    "xray",
    "ecg",
    "lab",
    "ultrasound",
    "tomography",
})


def _ensure_valid_sector(value: str) -> str:
    if value not in VALID_SECTOR_KEYS:
        raise ValueError(f"sector_key inválido: {value}")
    return value


# ---------------------------------------------------------------------------
# Sector config
# ---------------------------------------------------------------------------
class SectorConfig(BaseModel):
    sector_key: str
    enabled: bool
    capacity: Optional[int] = None

    @field_validator("sector_key")
    @classmethod
    def _check_key(cls, v: str) -> str:
        return _ensure_valid_sector(v)

    @field_validator("capacity")
    @classmethod
    def _check_capacity(cls, v: Optional[int]) -> Optional[int]:
        if v is not None and v < 0:
            raise ValueError("capacity deve ser >= 0")
        return v


class SectorConfigPut(BaseModel):
    items: list[SectorConfig]


# ---------------------------------------------------------------------------
# Bed payloads
# ---------------------------------------------------------------------------
class BedUpdate(BaseModel):
    patient_sigla: str = Field(min_length=1, max_length=64)
    clinical_summary: Optional[str] = Field(default=None, max_length=2000)


class BedTransfer(BaseModel):
    destination: Optional[str] = Field(default=None, max_length=200)


# ---------------------------------------------------------------------------
# Counter / specialist / exam payloads
# ---------------------------------------------------------------------------
class CounterUpdate(BaseModel):
    occupancy: int = Field(ge=0)
    capacity: int = Field(ge=0)


class SpecialistUpdate(BaseModel):
    status: Literal["available", "unavailable", "on_call"]


class ExamUpdate(BaseModel):
    status: Literal["working", "unavailable"]
    unavailable_reason: Optional[str] = Field(default=None, max_length=500)


# ---------------------------------------------------------------------------
# Read models
# ---------------------------------------------------------------------------
class _BaseRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    version: int
    last_updated_at: datetime
    last_updated_by: Optional[UUID] = None


class BedRead(_BaseRead):
    bed_number: int
    patient_sigla: Optional[str] = None
    clinical_summary: Optional[str] = None
    occupied_since: Optional[datetime] = None


class CounterRead(_BaseRead):
    sector_key: str
    occupancy: int
    capacity: int


class SpecialistRead(_BaseRead):
    sector_key: str
    status: Literal["available", "unavailable", "on_call"]


class ExamRead(_BaseRead):
    sector_key: str
    status: Literal["working", "unavailable"]
    unavailable_reason: Optional[str] = None


class SectorConfigRead(BaseModel):
    sector_key: str
    enabled: bool
    capacity: Optional[int] = None


class UnitStateResponse(BaseModel):
    unit: dict[str, Any]
    sectors_config: list[SectorConfigRead]
    beds: list[BedRead]
    counters: list[CounterRead]
    specialists: list[SpecialistRead]
    exams: list[ExamRead]


__all__ = [
    "VALID_SECTOR_KEYS",
    "SECTOR_TYPE_A_BEDS",
    "SECTOR_TYPE_B_COUNTERS",
    "SECTOR_TYPE_C_SPECIALISTS",
    "SECTOR_TYPE_D_EXAMS",
    "SectorConfig",
    "SectorConfigPut",
    "SectorConfigRead",
    "BedUpdate",
    "BedTransfer",
    "CounterUpdate",
    "SpecialistUpdate",
    "ExamUpdate",
    "BedRead",
    "CounterRead",
    "SpecialistRead",
    "ExamRead",
    "UnitStateResponse",
]
