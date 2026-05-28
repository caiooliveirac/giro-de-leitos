"""HTTP layer for Fase 3 — beds, sectors, counters, specialists, exams.

Auth model:
- Reads (``GET``): a read access dependency accepts EITHER an active shift
  session OR a paired device on the target unit. Admins always pass.
- Mutations (``PUT``/``POST``): an active shift session bound to the unit
  (admin overrides). PIN-confirmed actions take ``X-PIN-Confirm`` header.

Optimistic locking: caller may send ``If-Match: <version>``; on mismatch
we return ``409`` with the current resource state in the body. Without the
header, last-write-wins.
"""

from __future__ import annotations

from typing import Any, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response, status
from fastapi.responses import JSONResponse

from auth.deps import (
    client_meta,
    get_current_admin,
    get_current_session,
    get_db,
    get_device_context,
    require_pin_confirm,
)

from beds import service
from beds.schemas import (
    BedTransfer,
    BedUpdate,
    CounterUpdate,
    ExamUpdate,
    SectorConfigPut,
    SpecialistUpdate,
)
from beds.ws import unit_manager


router = APIRouter(prefix="/api", tags=["beds"])


# ---------------------------------------------------------------------------
# Access dependencies
# ---------------------------------------------------------------------------
def _try_admin(request: Request, conn) -> Optional[dict[str, Any]]:
    if not request.cookies.get("admin_token"):
        return None
    try:
        return get_current_admin(request=request, conn=conn)  # type: ignore[arg-type]
    except HTTPException:
        return None


def get_unit_read_access(
    unit_id: UUID,
    request: Request,
    conn=Depends(get_db),
) -> dict[str, Any]:
    """Read access for a unit: admin, active session, or paired device."""
    admin = _try_admin(request, conn)
    if admin is not None:
        return {"actor": admin, "unit_id": str(unit_id), "session": None, "device": None}

    # Try shift session (preferred — gives actor identity).
    try:
        device = get_device_context(request=request, conn=conn)  # type: ignore[arg-type]
    except HTTPException:
        device = None

    if device is not None:
        try:
            ctx = get_current_session(request=request, conn=conn, device=device)  # type: ignore[arg-type]
            if str(ctx["user"].get("unit_id") or device["unit_id"]) != str(unit_id):
                if ctx["user"]["role"] != "admin":
                    raise HTTPException(status_code=403, detail="Unidade não corresponde.")
            return {
                "actor": ctx["user"],
                "unit_id": str(unit_id),
                "session": ctx["session"],
                "device": ctx["device"],
            }
        except HTTPException:
            pass
        # Device alone is enough for read access.
        if device["unit_id"] != str(unit_id):
            raise HTTPException(status_code=403, detail="Dispositivo não pertence à unidade.")
        return {"actor": None, "unit_id": str(unit_id), "session": None, "device": device}

    raise HTTPException(status_code=401, detail="Sem credenciais para leitura.")


def get_unit_mutation_access(
    unit_id: UUID,
    request: Request,
    conn=Depends(get_db),
) -> dict[str, Any]:
    """Mutation requires an active shift session on the unit (admin overrides)."""
    admin = _try_admin(request, conn)
    if admin is not None:
        return {"actor": admin, "unit_id": str(unit_id), "session": None, "device": None}

    device = get_device_context(request=request, conn=conn)  # type: ignore[arg-type]
    ctx = get_current_session(request=request, conn=conn, device=device)  # type: ignore[arg-type]
    user = ctx["user"]
    user_unit = str(user.get("unit_id")) if user.get("unit_id") else None
    if user["role"] != "admin" and user_unit and user_unit != str(unit_id):
        raise HTTPException(status_code=403, detail="Usuário não pertence à unidade.")
    if device["unit_id"] != str(unit_id) and user["role"] != "admin":
        raise HTTPException(status_code=403, detail="Dispositivo não pertence à unidade.")
    return {
        "actor": user,
        "unit_id": str(unit_id),
        "session": ctx["session"],
        "device": ctx["device"],
    }


def _require_coordinator_or_admin(access: dict[str, Any]) -> None:
    actor = access.get("actor") or {}
    role = actor.get("role")
    if role not in ("admin", "coordinator"):
        raise HTTPException(status_code=403, detail="Apenas coordenador/admin.")


def _parse_if_match(if_match: Optional[str]) -> Optional[int]:
    if if_match is None:
        return None
    raw = if_match.strip().strip('"')
    try:
        return int(raw)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="If-Match inválido.") from exc


def _conflict_response(current: Any) -> JSONResponse:
    return JSONResponse(
        status_code=status.HTTP_409_CONFLICT,
        content={"error": "conflict", "current": _jsonable(current)},
    )


def _jsonable(value: Any) -> Any:
    """Best-effort: turn datetimes/UUIDs into JSON-friendly primitives."""
    from datetime import datetime
    from uuid import UUID as _UUID

    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_jsonable(v) for v in value]
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, _UUID):
        return str(value)
    return value


# ---------------------------------------------------------------------------
# State / config
# ---------------------------------------------------------------------------
@router.get("/unit/{unit_id}/state")
def read_unit_state(
    unit_id: UUID,
    access=Depends(get_unit_read_access),
    conn=Depends(get_db),
):
    try:
        state = service.get_unit_state(conn, str(unit_id))
    except service.NotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return _jsonable(state)


@router.get("/unit/{unit_id}/giro-history")
def read_giro_history(
    unit_id: UUID,
    limit: int = 10,
    access=Depends(get_unit_read_access),
    conn=Depends(get_db),
):
    try:
        history = service.get_giro_history(conn, str(unit_id), limit=max(1, min(int(limit), 50)))
    except service.NotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return _jsonable({"items": history})


@router.get("/unit/{unit_id}/sectors/config")
def read_sectors_config(
    unit_id: UUID,
    access=Depends(get_unit_read_access),
    conn=Depends(get_db),
):
    try:
        state = service.get_unit_state(conn, str(unit_id))
    except service.NotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return _jsonable(state["sectors_config"])


@router.put("/unit/{unit_id}/sectors/config")
def put_sectors_config(
    unit_id: UUID,
    payload: SectorConfigPut,
    request: Request,
    access=Depends(get_unit_mutation_access),
    conn=Depends(get_db),
):
    _require_coordinator_or_admin(access)
    try:
        result = service.put_sector_config(
            conn,
            str(unit_id),
            [item.model_dump() for item in payload.items],
            access["actor"] or {},
        )
    except service.NotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return _jsonable(result)


# ---------------------------------------------------------------------------
# Beds
# ---------------------------------------------------------------------------
async def _broadcast(unit_id: str, event_type: str, payload: Any) -> None:
    await unit_manager.broadcast(unit_id, event_type, _jsonable(payload))


@router.put("/unit/{unit_id}/beds/{bed_number}")
async def put_bed(
    unit_id: UUID,
    bed_number: int,
    payload: BedUpdate,
    request: Request,
    if_match: Optional[str] = Header(default=None, alias="If-Match"),
    access=Depends(get_unit_mutation_access),
    conn=Depends(get_db),
):
    expected = _parse_if_match(if_match)
    try:
        result = service.upsert_bed(
            conn, str(unit_id), bed_number, payload.model_dump(), access["actor"] or {}, expected,
        )
    except service.VersionConflict as exc:
        return _conflict_response(exc.current)
    except service.NotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    await _broadcast(str(unit_id), "bed_updated", result)
    # Editar um leito assume a vermelha (rede de segurança no service) — avisa
    # outros dispositivos para saírem do modo "ao vivo".
    await _broadcast(str(unit_id), "red_room_assumed", {"assumed": True})
    return _jsonable(result)


@router.post("/unit/{unit_id}/beds/{bed_number}/discharge")
async def post_bed_discharge(
    unit_id: UUID,
    bed_number: int,
    if_match: Optional[str] = Header(default=None, alias="If-Match"),
    access=Depends(get_unit_mutation_access),
    conn=Depends(get_db),
):
    expected = _parse_if_match(if_match)
    try:
        result = service.discharge_bed(conn, str(unit_id), bed_number, access["actor"] or {}, expected)
    except service.VersionConflict as exc:
        return _conflict_response(exc.current)
    except service.NotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    await _broadcast(str(unit_id), "bed_updated", result)
    return _jsonable(result)


@router.post("/unit/{unit_id}/beds/{bed_number}/death")
async def post_bed_death(
    unit_id: UUID,
    bed_number: int,
    if_match: Optional[str] = Header(default=None, alias="If-Match"),
    ctx=Depends(require_pin_confirm),
    conn=Depends(get_db),
):
    # ``require_pin_confirm`` already validated session + PIN. Verify unit match.
    user = ctx["user"]
    user_unit = str(user.get("unit_id")) if user.get("unit_id") else None
    if user["role"] != "admin" and user_unit and user_unit != str(unit_id):
        raise HTTPException(status_code=403, detail="Usuário não pertence à unidade.")

    expected = _parse_if_match(if_match)
    try:
        result = service.bed_death(conn, str(unit_id), bed_number, user, expected)
    except service.VersionConflict as exc:
        return _conflict_response(exc.current)
    except service.NotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    await _broadcast(str(unit_id), "bed_updated", result)
    return _jsonable(result)


@router.post("/unit/{unit_id}/beds/{bed_number}/transfer")
async def post_bed_transfer(
    unit_id: UUID,
    bed_number: int,
    payload: BedTransfer,
    if_match: Optional[str] = Header(default=None, alias="If-Match"),
    access=Depends(get_unit_mutation_access),
    conn=Depends(get_db),
):
    expected = _parse_if_match(if_match)
    try:
        result = service.bed_transfer(
            conn, str(unit_id), bed_number, access["actor"] or {},
            destination=payload.destination, expected_version=expected,
        )
    except service.VersionConflict as exc:
        return _conflict_response(exc.current)
    except service.NotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    await _broadcast(str(unit_id), "bed_updated", result)
    return _jsonable(result)


@router.post("/unit/{unit_id}/beds/{bed_number}/clear")
async def post_bed_clear(
    unit_id: UUID,
    bed_number: int,
    if_match: Optional[str] = Header(default=None, alias="If-Match"),
    access=Depends(get_unit_mutation_access),
    conn=Depends(get_db),
):
    expected = _parse_if_match(if_match)
    try:
        result = service.bed_clear(conn, str(unit_id), bed_number, access["actor"] or {}, expected)
    except service.VersionConflict as exc:
        return _conflict_response(exc.current)
    except service.NotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    await _broadcast(str(unit_id), "bed_updated", result)
    return _jsonable(result)


# ---------------------------------------------------------------------------
# Sala vermelha — takeover ("assumir giro")
# ---------------------------------------------------------------------------
@router.post("/unit/{unit_id}/red-room/assume")
async def post_red_room_assume(
    unit_id: UUID,
    access=Depends(get_unit_mutation_access),
    conn=Depends(get_db),
):
    try:
        result = service.assume_red_room(conn, str(unit_id), access["actor"] or {})
    except service.NotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    await _broadcast(str(unit_id), "red_room_assumed", result)
    return _jsonable(result)


@router.post("/unit/{unit_id}/red-room/release")
async def post_red_room_release(
    unit_id: UUID,
    access=Depends(get_unit_mutation_access),
    conn=Depends(get_db),
):
    try:
        result = service.release_red_room(conn, str(unit_id), access["actor"] or {})
    except service.NotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    await _broadcast(str(unit_id), "red_room_released", result)
    return _jsonable(result)


# ---------------------------------------------------------------------------
# Counters / specialists / exams
# ---------------------------------------------------------------------------
@router.put("/unit/{unit_id}/counters/{sector_key}")
async def put_counter(
    unit_id: UUID,
    sector_key: str,
    payload: CounterUpdate,
    if_match: Optional[str] = Header(default=None, alias="If-Match"),
    access=Depends(get_unit_mutation_access),
    conn=Depends(get_db),
):
    expected = _parse_if_match(if_match)
    try:
        result = service.update_counter(
            conn, str(unit_id), sector_key, payload.occupancy, payload.capacity,
            access["actor"] or {}, expected_version=expected,
        )
    except service.VersionConflict as exc:
        return _conflict_response(exc.current)
    except service.NotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    await _broadcast(str(unit_id), "counter_updated", result)
    return _jsonable(result)


@router.put("/unit/{unit_id}/specialists/{sector_key}")
async def put_specialist(
    unit_id: UUID,
    sector_key: str,
    payload: SpecialistUpdate,
    if_match: Optional[str] = Header(default=None, alias="If-Match"),
    access=Depends(get_unit_mutation_access),
    conn=Depends(get_db),
):
    expected = _parse_if_match(if_match)
    try:
        result = service.update_specialist(
            conn, str(unit_id), sector_key, payload.status,
            access["actor"] or {}, expected_version=expected,
        )
    except service.VersionConflict as exc:
        return _conflict_response(exc.current)
    except service.NotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    await _broadcast(str(unit_id), "specialist_updated", result)
    return _jsonable(result)


@router.put("/unit/{unit_id}/exams/{sector_key}")
async def put_exam(
    unit_id: UUID,
    sector_key: str,
    payload: ExamUpdate,
    if_match: Optional[str] = Header(default=None, alias="If-Match"),
    access=Depends(get_unit_mutation_access),
    conn=Depends(get_db),
):
    expected = _parse_if_match(if_match)
    try:
        result = service.update_exam(
            conn, str(unit_id), sector_key, payload.status,
            access["actor"] or {},
            unavailable_reason=payload.unavailable_reason,
            expected_version=expected,
        )
    except service.VersionConflict as exc:
        return _conflict_response(exc.current)
    except service.NotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    await _broadcast(str(unit_id), "exam_updated", result)
    return _jsonable(result)


__all__ = ["router", "get_unit_read_access", "get_unit_mutation_access"]
