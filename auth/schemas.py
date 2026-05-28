"""Pydantic v2 schemas for auth, invites, sessions."""

from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator, model_validator


# ---------------------------------------------------------------------------
# Admin login
# ---------------------------------------------------------------------------
class AdminLogin(BaseModel):
    email: Optional[str] = Field(default=None, description="Admin email")
    username: Optional[str] = Field(
        default=None,
        description="Alternative login (kept for compatibility; mapped to email).",
    )
    password: str = Field(min_length=1)

    @field_validator("email", "username")
    @classmethod
    def _strip(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        v = value.strip()
        return v or None


# ---------------------------------------------------------------------------
# Device pairing
# ---------------------------------------------------------------------------
class DeviceGenerateCodeRequest(BaseModel):
    unit_id: UUID


class DeviceGenerateCodeResponse(BaseModel):
    pairing_code: str
    expires_at: datetime


class DevicePair(BaseModel):
    pairing_code: str = Field(min_length=4, max_length=12)
    device_fingerprint: str = Field(min_length=4, max_length=128)
    label: Optional[str] = Field(default=None, max_length=120)


class DevicePairResponse(BaseModel):
    unit_id: UUID
    device_id: UUID
    expires_at: datetime


class DeviceSelfPair(BaseModel):
    username: Optional[str] = Field(default=None, min_length=1, max_length=64)
    cpf: Optional[str] = Field(default=None, min_length=11, max_length=14)
    password: str = Field(min_length=1, max_length=128)
    pin: str = Field(min_length=4, max_length=8)
    device_fingerprint: str = Field(min_length=4, max_length=128)
    label: Optional[str] = Field(default=None, max_length=120)

    @model_validator(mode="after")
    def _one_login(self):
        if not (self.username or self.cpf):
            raise ValueError("Informe username ou cpf.")
        return self


class DeviceSelfPairResponse(BaseModel):
    device_id: str
    unit_id: UUID
    session_id: UUID
    expires_at: datetime
    user: "UserPublic"


# ---------------------------------------------------------------------------
# Shift / PIN
# ---------------------------------------------------------------------------
class ShiftStart(BaseModel):
    user_id: UUID
    pin: str = Field(min_length=3, max_length=8)


class ShiftStartResponse(BaseModel):
    session_id: UUID
    user_id: UUID
    expires_at: datetime


class ShiftEnd(BaseModel):
    reason: Optional[str] = Field(default="logout", max_length=80)


class PinVerify(BaseModel):
    pin: str = Field(min_length=3, max_length=8)


# ---------------------------------------------------------------------------
# Invites
# ---------------------------------------------------------------------------
class InviteCreate(BaseModel):
    type: Literal["coordinator", "professional"]
    target_unit_id: Optional[UUID] = None


class InviteCreateResponse(BaseModel):
    id: UUID
    token: str
    type: str
    target_unit_id: Optional[UUID]
    expires_at: datetime


class InvitePreview(BaseModel):
    type: str
    unit_name: Optional[str]
    inviter_name: str
    expires_at: datetime


class InviteAccept(BaseModel):
    name: str = Field(min_length=2, max_length=160)
    cpf: str = Field(min_length=11, max_length=14)
    phone: Optional[str] = Field(default=None, max_length=32)
    cargo: str = Field(min_length=2, max_length=80)
    coren_crm: Optional[str] = Field(default=None, max_length=40)
    password: str = Field(min_length=8, max_length=128)
    pin: str = Field(min_length=4, max_length=8)
    photo_url: Optional[str] = Field(default=None, max_length=500)
    lgpd_accepted: bool = False

    @field_validator("lgpd_accepted")
    @classmethod
    def _must_accept(cls, value: bool) -> bool:
        if not value:
            raise ValueError("LGPD precisa ser aceita.")
        return value


class InviteListItem(BaseModel):
    id: UUID
    type: str
    target_unit_id: Optional[UUID]
    status: str
    created_at: datetime
    expires_at: datetime
    used_by: Optional[UUID] = None


# ---------------------------------------------------------------------------
# Users
# ---------------------------------------------------------------------------
class UserPublic(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    role: str
    cargo: Optional[str] = None
    photo_url: Optional[str] = None
    status: str
    unit_id: Optional[UUID] = None
    cpf_masked: str


class PendingUser(BaseModel):
    id: UUID
    name: str
    role: str
    cargo: Optional[str] = None
    unit_id: Optional[UUID] = None
    created_at: datetime
    cpf_masked: str
    coren_crm: Optional[str] = None


class ApproveResponse(BaseModel):
    id: UUID
    status: str


class UnitMember(BaseModel):
    id: UUID
    name: str
    role: str
    status: str
    cargo: Optional[str] = None
    coren_crm: Optional[str] = None
    phone: Optional[str] = None
    photo_url: Optional[str] = None
    cpf_masked: str
    created_at: datetime
    approved_at: Optional[datetime] = None


# ---------------------------------------------------------------------------
# Admin — units overview
# ---------------------------------------------------------------------------
class AdminUnit(BaseModel):
    id: UUID
    code: str
    canonical_name: str
    slug: str
    active: bool
    coordinator_count: int = 0
    enabled_sector_count: int = 0
    red_capacity: int = 0


DeviceSelfPairResponse.model_rebuild()
