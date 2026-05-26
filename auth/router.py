"""HTTP layer for Fase 2 — auth, devices, invites, approval."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status

from auth import service
from auth.audit import record_audit
from auth.crypto import decrypt_cpf, mask_cpf
from auth.deps import (
    ADMIN_TOKEN_TTL,
    DEVICE_TOKEN_TTL,
    SHIFT_TOKEN_TTL,
    clear_session_cookie,
    client_meta,
    encode_token,
    get_current_admin,
    get_current_session,
    get_db,
    get_device_context,
    set_admin_cookie,
    set_device_cookie,
    set_session_cookie,
)
from auth.schemas import (
    AdminLogin,
    ApproveResponse,
    DeviceGenerateCodeRequest,
    DeviceGenerateCodeResponse,
    DevicePair,
    DevicePairResponse,
    InviteAccept,
    InviteCreate,
    InviteCreateResponse,
    InviteListItem,
    InvitePreview,
    PendingUser,
    PinVerify,
    ShiftEnd,
    ShiftStart,
    ShiftStartResponse,
    UserPublic,
)
from services import notifications

router = APIRouter(prefix="/api", tags=["auth"])


def _safe_cpf_masked(cpf_encrypted: str | None) -> str:
    if not cpf_encrypted:
        return "***.***.***-**"
    try:
        return mask_cpf(decrypt_cpf(cpf_encrypted))
    except Exception:  # noqa: BLE001
        return "***.***.***-**"


def _user_public(row: dict[str, Any]) -> UserPublic:
    return UserPublic(
        id=row["id"],
        name=row["name"],
        role=row["role"],
        cargo=row.get("cargo"),
        photo_url=row.get("photo_url"),
        status=row["status"],
        unit_id=row.get("unit_id"),
        cpf_masked=_safe_cpf_masked(row.get("cpf_encrypted")),
    )


def _pending_user(row: dict[str, Any]) -> PendingUser:
    return PendingUser(
        id=row["id"],
        name=row["name"],
        role=row["role"],
        cargo=row.get("cargo"),
        unit_id=row.get("unit_id"),
        created_at=row["created_at"],
        cpf_masked=_safe_cpf_masked(row.get("cpf_encrypted")),
        coren_crm=row.get("coren_crm"),
    )


# ---------------------------------------------------------------------------
# Admin login
# ---------------------------------------------------------------------------
@router.post("/auth/admin/login")
def admin_login(
    payload: AdminLogin,
    response: Response,
    request: Request,
    conn=Depends(get_db),
):
    identifier = payload.email or payload.username
    user = service.authenticate_admin(conn, identifier or "", payload.password)
    token = encode_token({"sub": str(user["id"]), "scope": "admin"}, ADMIN_TOKEN_TTL)
    set_admin_cookie(response, token)
    meta = client_meta(request)
    record_audit(
        conn,
        actor_user_id=user["id"],
        action="admin.login",
        entity_type="user",
        entity_id=str(user["id"]),
        **meta,
    )
    return {
        "user": _user_public(
            {**user, "cpf_encrypted": None, "cargo": None, "photo_url": user.get("photo_url")}
        ).model_dump(mode="json"),
    }


@router.post("/auth/admin/logout")
def admin_logout(response: Response):
    response.delete_cookie("admin_token", path="/")
    return {"ok": True}


# ---------------------------------------------------------------------------
# Device pairing
# ---------------------------------------------------------------------------
@router.post("/auth/device/generate-code", response_model=DeviceGenerateCodeResponse)
def device_generate_code(
    payload: DeviceGenerateCodeRequest,
    request: Request,
    conn=Depends(get_db),
):
    # Authorization: admin OR coordinator of the target unit.
    actor = None
    admin_cookie = request.cookies.get("admin_token")
    if admin_cookie:
        try:
            actor = get_current_admin(request=request, conn=conn)  # type: ignore[arg-type]
        except HTTPException:
            actor = None
    if actor is None:
        # Try shift session — coordinator must be active on a device in the same unit.
        try:
            ctx = get_current_session(  # type: ignore[arg-type]
                request=request,
                conn=conn,
                device=get_device_context(request=request, conn=conn),
            )
        except HTTPException as exc:
            raise HTTPException(status_code=401, detail="Não autorizado.") from exc
        user = ctx["user"]
        if user["role"] != "coordinator" or str(user.get("unit_id")) != str(payload.unit_id):
            raise HTTPException(status_code=403, detail="Sem permissão.")
        actor = user

    result = service.create_pairing_code(conn, payload.unit_id, actor["id"])
    record_audit(
        conn,
        actor_user_id=actor["id"],
        action="device.pairing_code.create",
        entity_type="unit",
        entity_id=str(payload.unit_id),
        new_value={"expires_at": result["expires_at"].isoformat()},
        **client_meta(request),
    )
    return result


@router.post("/auth/device/pair", response_model=DevicePairResponse)
def device_pair(payload: DevicePair, request: Request, response: Response, conn=Depends(get_db)):
    paired = service.pair_device(
        conn,
        pairing_code=payload.pairing_code,
        device_fingerprint=payload.device_fingerprint,
        label=payload.label,
    )
    token = encode_token(
        {
            "scope": "device",
            "unit_id": paired["unit_id"],
            "device_id": paired["device_id"],
        },
        DEVICE_TOKEN_TTL,
    )
    set_device_cookie(response, token)
    record_audit(
        conn,
        device_id=paired["device_id"],
        action="device.pair",
        entity_type="device",
        entity_id=paired["device_id"],
        new_value={"unit_id": paired["unit_id"], "label": payload.label},
        **client_meta(request),
    )
    return paired


# ---------------------------------------------------------------------------
# Staff visible on a device
# ---------------------------------------------------------------------------
@router.get("/auth/me/unit/staff")
def list_my_unit_staff(
    device=Depends(get_device_context),
    conn=Depends(get_db),
):
    rows = service.list_unit_staff(conn, device["unit_id"])
    return [_user_public(r).model_dump(mode="json") for r in rows]


# ---------------------------------------------------------------------------
# Shift session
# ---------------------------------------------------------------------------
@router.post("/auth/shift/start", response_model=ShiftStartResponse)
def shift_start(
    payload: ShiftStart,
    request: Request,
    response: Response,
    device=Depends(get_device_context),
    conn=Depends(get_db),
):
    # Confirm user belongs to the device's unit before checking PIN.
    with conn.cursor() as cur:
        cur.execute("SELECT unit_id FROM users WHERE id = %s", (str(payload.user_id),))
        row = cur.fetchone()
    if not row or (row["unit_id"] and str(row["unit_id"]) != device["unit_id"]):
        raise HTTPException(status_code=403, detail="Usuário não pertence à unidade.")

    sess = service.start_shift(conn, payload.user_id, payload.pin, device["device_id"])
    token = encode_token(
        {"scope": "shift", "session_id": str(sess["id"]), "sub": str(sess["user_id"])},
        SHIFT_TOKEN_TTL,
    )
    set_session_cookie(response, token)
    record_audit(
        conn,
        actor_user_id=sess["user_id"],
        session_id=sess["id"],
        device_id=device["device_id"],
        action="shift.start",
        entity_type="session",
        entity_id=str(sess["id"]),
        **client_meta(request),
    )
    return {
        "session_id": sess["id"],
        "user_id": sess["user_id"],
        "expires_at": sess["expires_at"],
    }


@router.post("/auth/shift/end")
def shift_end(
    payload: ShiftEnd,
    request: Request,
    response: Response,
    ctx=Depends(get_current_session),
    conn=Depends(get_db),
):
    service.end_shift(conn, ctx["session"]["id"], reason=payload.reason or "logout")
    clear_session_cookie(response)
    record_audit(
        conn,
        actor_user_id=ctx["user"]["id"],
        session_id=ctx["session"]["id"],
        device_id=ctx["device"]["device_id"],
        action="shift.end",
        entity_type="session",
        entity_id=str(ctx["session"]["id"]),
        new_value={"reason": payload.reason or "logout"},
        **client_meta(request),
    )
    return {"ok": True}


@router.post("/auth/pin/verify")
def pin_verify(
    payload: PinVerify,
    ctx=Depends(get_current_session),
    conn=Depends(get_db),
):
    ok = service.verify_pin(conn, ctx["user"]["id"], payload.pin)
    if not ok:
        raise HTTPException(status_code=403, detail="PIN incorreto.")
    return {"ok": True}


# ---------------------------------------------------------------------------
# Invites
# ---------------------------------------------------------------------------
def _current_inviter(
    request: Request,
    conn,
) -> dict[str, Any]:
    """Admin OR active coordinator session — both can create invites."""
    try:
        admin = get_current_admin(request=request, conn=conn)  # type: ignore[arg-type]
        return admin
    except HTTPException:
        pass
    ctx = get_current_session(  # type: ignore[arg-type]
        request=request,
        conn=conn,
        device=get_device_context(request=request, conn=conn),
    )
    if ctx["user"]["role"] not in ("coordinator", "admin"):
        raise HTTPException(status_code=403, detail="Sem permissão.")
    return ctx["user"]


@router.post("/invites", response_model=InviteCreateResponse, status_code=201)
def create_invite_endpoint(
    payload: InviteCreate,
    request: Request,
    conn=Depends(get_db),
):
    inviter = _current_inviter(request, conn)
    row = service.create_invite(
        conn,
        created_by=inviter,
        type=payload.type,
        target_unit_id=payload.target_unit_id,
    )
    record_audit(
        conn,
        actor_user_id=inviter["id"],
        action="invite.create",
        entity_type="invite",
        entity_id=str(row["id"]),
        new_value={"type": row["type"], "target_unit_id": str(row["target_unit_id"]) if row["target_unit_id"] else None},
        **client_meta(request),
    )
    notifications.enqueue(
        conn,
        channel="whatsapp",
        recipient="pending",
        template="invite.created",
        payload={"invite_id": str(row["id"]), "type": row["type"]},
    )
    return InviteCreateResponse(
        id=row["id"],
        token=row["token"],
        type=row["type"],
        target_unit_id=row["target_unit_id"],
        expires_at=row["expires_at"],
    )


@router.get("/invites", response_model=list[InviteListItem])
def list_invites_endpoint(request: Request, conn=Depends(get_db)):
    user = _current_inviter(request, conn)
    rows = service.list_invites(conn, user=user)
    return [InviteListItem(**r) for r in rows]


@router.get("/invites/{token}/preview", response_model=InvitePreview)
def preview_invite_endpoint(token: str, conn=Depends(get_db)):
    return InvitePreview(**service.preview_invite(conn, token))


@router.post("/invites/{token}/accept", status_code=201)
def accept_invite_endpoint(
    token: str,
    payload: InviteAccept,
    request: Request,
    conn=Depends(get_db),
):
    user = service.accept_invite(conn, token, payload)
    record_audit(
        conn,
        actor_user_id=user["id"],
        action="invite.accept",
        entity_type="user",
        entity_id=str(user["id"]),
        new_value={"role": user["role"], "unit_id": str(user["unit_id"]) if user.get("unit_id") else None},
        **client_meta(request),
    )
    notifications.enqueue(
        conn,
        channel="whatsapp",
        recipient="pending",
        template="user.pending",
        payload={"user_id": str(user["id"]), "role": user["role"]},
    )
    return _user_public({**user, "cpf_encrypted": None}).model_dump(mode="json")


@router.post("/invites/{invite_id}/revoke")
def revoke_invite_endpoint(invite_id: UUID, request: Request, conn=Depends(get_db)):
    user = _current_inviter(request, conn)
    service.revoke_invite(conn, user=user, invite_id=invite_id)
    record_audit(
        conn,
        actor_user_id=user["id"],
        action="invite.revoke",
        entity_type="invite",
        entity_id=str(invite_id),
        **client_meta(request),
    )
    return {"ok": True}


# ---------------------------------------------------------------------------
# Approval
# ---------------------------------------------------------------------------
@router.get("/users/pending", response_model=list[PendingUser])
def list_pending_endpoint(request: Request, conn=Depends(get_db)):
    user = _current_inviter(request, conn)
    rows = service.list_pending(conn, user)
    return [_pending_user(r) for r in rows]


@router.post("/users/{user_id}/approve", response_model=ApproveResponse)
def approve_user_endpoint(user_id: UUID, request: Request, conn=Depends(get_db)):
    approver = _current_inviter(request, conn)
    result = service.approve_user(conn, approver, user_id)
    record_audit(
        conn,
        actor_user_id=approver["id"],
        action="user.approve",
        entity_type="user",
        entity_id=str(user_id),
        new_value={"status": "active"},
        **client_meta(request),
    )
    notifications.enqueue(
        conn,
        channel="whatsapp",
        recipient=result.get("phone") or "pending",
        template="user.approved",
        payload={"user_id": str(result["id"])},
    )
    return ApproveResponse(id=result["id"], status=result["status"])


@router.post("/users/{user_id}/reject", response_model=ApproveResponse)
def reject_user_endpoint(user_id: UUID, request: Request, conn=Depends(get_db)):
    approver = _current_inviter(request, conn)
    result = service.reject_user(conn, approver, user_id)
    record_audit(
        conn,
        actor_user_id=approver["id"],
        action="user.reject",
        entity_type="user",
        entity_id=str(user_id),
        new_value={"status": "suspended"},
        **client_meta(request),
    )
    notifications.enqueue(
        conn,
        channel="whatsapp",
        recipient=result.get("phone") or "pending",
        template="user.rejected",
        payload={"user_id": str(result["id"])},
    )
    return ApproveResponse(id=result["id"], status=result["status"])


@router.post("/users/{user_id}/suspend", response_model=ApproveResponse)
def suspend_user_endpoint(user_id: UUID, request: Request, conn=Depends(get_db)):
    approver = _current_inviter(request, conn)
    result = service.suspend_user(conn, approver, user_id)
    record_audit(
        conn,
        actor_user_id=approver["id"],
        action="user.suspend",
        entity_type="user",
        entity_id=str(user_id),
        new_value={"status": "suspended"},
        **client_meta(request),
    )
    return ApproveResponse(id=result["id"], status=result["status"])
