from __future__ import annotations

import hashlib
import hmac
import json
import os
from urllib import error, request
from datetime import datetime, timezone
from typing import Any

from fastapi.concurrency import run_in_threadpool
from fastapi import FastAPI, Header, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from db import get_latest_events, get_latest_status_by_unit, get_parsed_event, get_pending_unit_confirmations, get_recent_alerts, get_registered_units, init_db, is_database_configured, resolve_pending_unit_confirmation, save_event, update_unit_reported_at, delete_event, admin_update_event, get_event_detail
from parser_service import parse_whatsapp_message
from units import resolve_unit_from_text, resolve_unit_name


FIXED_NO_YELLOW_UNIT_CODES = {
    "upa_bairro_da_paz_orlando_imbassahy",
    "upa_periperi",
}


def _remove_parser_warning(warnings: list[str], warning_text: str) -> list[str]:
    return [warning for warning in warnings if warning != warning_text]


class WebhookPayload(BaseModel):
    text: str = Field(..., min_length=1, description="Texto bruto recebido do WhatsApp")
    source: str = Field(default="whatsapp", description="Origem do evento")
    received_at: datetime | None = Field(default=None, description="Timestamp opcional do recebimento")


class ManualIngestPayload(BaseModel):
    text: str = Field(..., min_length=1, description="Texto bruto do giro enviado manualmente pelo frontend ou navegador")
    source: str = Field(default="manual", description="Origem lógica da ingestão")
    unit_hint: str | None = Field(default=None, description="Dica opcional de unidade para auditoria futura")
    official_at: datetime | None = Field(default=None, description="Horário oficial do giro, quando informado manualmente")


class WhatsAppBridgeIngestPayload(BaseModel):
    text: str = Field(..., min_length=1, description="Texto bruto do giro recebido via WhatsApp bridge")
    source: str = Field(default="whatsapp-bridge", description="Origem lógica da ingestão")
    unit_hint: str | None = Field(default=None, description="Nome da UPA identificada pelo telefone do remetente")
    sender_phone: str | None = Field(default=None, description="Telefone do remetente (formato 55XXXXXXXXXXX)")
    dry_run: bool = Field(default=False, description="Se True, parseia e retorna o resultado mas NÃO salva no banco nem publica no dashboard")


class UpdateReportedAtPayload(BaseModel):
    reported_at: datetime = Field(..., description="Novo horário oficial do último giro da unidade")


class ResolvePendingUnitPayload(BaseModel):
    unit_name: str = Field(..., min_length=2, description="Nome ou alias da unidade para tentar resolver a pendência")


class AdminLoginPayload(BaseModel):
    username: str = Field(..., min_length=1, description="Usuário admin")
    password: str = Field(..., min_length=1, description="Senha admin")


class AdminUpdateEventPayload(BaseModel):
    upa_name: str | None = Field(default=None, description="Novo nome da UPA")
    unit_code: str | None = Field(default=None, description="Novo unit_code")
    reported_at: datetime | None = Field(default=None, description="Novo horário oficial")
    red_occupied: int | None = Field(default=None, ge=0)
    red_capacity: int | None = Field(default=None, ge=0)
    yellow_occupied: int | None = Field(default=None, ge=0)
    yellow_capacity: int | None = Field(default=None, ge=0)
    yellow_male_occupied: int | None = Field(default=None, ge=0)
    yellow_male_capacity: int | None = Field(default=None, ge=0)
    yellow_female_occupied: int | None = Field(default=None, ge=0)
    yellow_female_capacity: int | None = Field(default=None, ge=0)
    isolation_mode: str | None = Field(default=None)
    isolation_total_occupied: int | None = Field(default=None, ge=0)
    isolation_total_capacity: int | None = Field(default=None, ge=0)
    isolation_female_occupied: int | None = Field(default=None, ge=0)
    isolation_female_capacity: int | None = Field(default=None, ge=0)
    isolation_male_occupied: int | None = Field(default=None, ge=0)
    isolation_male_capacity: int | None = Field(default=None, ge=0)
    isolation_pediatric_occupied: int | None = Field(default=None, ge=0)
    isolation_pediatric_capacity: int | None = Field(default=None, ge=0)
    has_orthopedist: bool | None = Field(default=None)
    has_surgeon: bool | None = Field(default=None)
    has_psychiatrist: bool | None = Field(default=None)


class TelegramUpdatePayload(BaseModel):
    update_id: int | None = None
    message: dict[str, Any] | None = None
    edited_message: dict[str, Any] | None = None


class ConnectionManager:
    def __init__(self) -> None:
        self._connections: list[WebSocket] = []

    async def connect(self, websocket: WebSocket) -> None:
        await websocket.accept()
        self._connections.append(websocket)

    def disconnect(self, websocket: WebSocket) -> None:
        if websocket in self._connections:
            self._connections.remove(websocket)

    async def broadcast_json(self, payload: dict[str, Any]) -> None:
        stale_connections: list[WebSocket] = []
        for connection in self._connections:
            try:
                await connection.send_json(payload)
            except Exception:
                stale_connections.append(connection)

        for connection in stale_connections:
            self.disconnect(connection)


app = FastAPI(
    title="Giro de Leitos Parser API",
    version="0.1.0",
    description="Microsserviço FastAPI para ingestão e parsing de mensagens de WhatsApp com atualização em tempo real via WebSocket.",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

manager = ConnectionManager()
app.state.last_dashboard_event = None
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_ADMIN_CHAT_ID = os.getenv("TELEGRAM_ADMIN_CHAT_ID", "").strip()
TELEGRAM_WEBHOOK_SECRET = os.getenv("TELEGRAM_WEBHOOK_SECRET", "").strip()
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "https://mnrs.com.br").rstrip("/")
PUBLIC_WEBHOOK_PATH = os.getenv("PUBLIC_WEBHOOK_PATH", "/giro/api/webhook/telegram").strip() or "/giro/api/webhook/telegram"


@app.on_event("startup")
async def startup_event() -> None:
    if is_database_configured():
        await run_in_threadpool(init_db)


def build_dashboard_event(
    text: str,
    source: str,
    received_at: datetime | None = None,
    unit_hint: str | None = None,
    official_at: datetime | None = None,
) -> dict[str, Any]:
    ingested_at = received_at or datetime.now(timezone.utc)
    parsed = parse_whatsapp_message(text)
    reported_upa_name = parsed.get("upa_name")
    reported_name_match = resolve_unit_name(reported_upa_name)
    raw_text_match = resolve_unit_from_text(text)
    hinted_match = resolve_unit_name(unit_hint)
    unit_match = reported_name_match or raw_text_match or hinted_match
    # Identificar se a unidade veio do texto ou apenas pelo hint (remetente)
    unit_identified_by_text = bool(reported_name_match or raw_text_match)
    unit_identified_by_hint = bool(not unit_identified_by_text and hinted_match)
    effective_reported_at = official_at.isoformat() if official_at else parsed.get("reported_at") or ingested_at.isoformat()

    # Sanity check: se o horário digitado está muito longe do horário real,
    # substituir pelo horário de ingestão e marcar anomalia.
    _time_anomaly: dict[str, Any] | None = None
    if not official_at and effective_reported_at:
        try:
            _parsed_dt = datetime.fromisoformat(effective_reported_at.replace("Z", "+00:00"))
            if _parsed_dt.tzinfo is None:
                _parsed_dt = _parsed_dt.replace(tzinfo=timezone.utc)
            _drift_seconds = (ingested_at - _parsed_dt).total_seconds()
            _drift_hours = abs(_drift_seconds) / 3600
            if _drift_hours > 6:
                # Horário delirante — substituir pelo horário real
                _time_anomaly = {
                    "type": "absurd_time",
                    "typed_time": effective_reported_at,
                    "system_time": ingested_at.isoformat(),
                    "drift_hours": round(_drift_hours, 1),
                }
                effective_reported_at = ingested_at.isoformat()
            elif _drift_hours > 2:
                # Drift moderado — manter digitado mas alertar
                _time_anomaly = {
                    "type": "suspect_time",
                    "typed_time": effective_reported_at,
                    "system_time": ingested_at.isoformat(),
                    "drift_hours": round(_drift_hours, 1),
                }
        except (ValueError, TypeError):
            pass

    parsed["reported_at"] = effective_reported_at
    parsed["ingested_at"] = ingested_at.isoformat()
    if _time_anomaly:
        parsed["_time_anomaly"] = _time_anomaly

    if reported_upa_name:
        parsed["reported_upa_name"] = reported_upa_name

    warnings = parsed.setdefault("warnings", [])
    if unit_match and unit_identified_by_text:
        warnings = _remove_parser_warning(warnings, "Nome da UPA não identificado no payload.")

    if unit_match and unit_match.get("unit_code") in FIXED_NO_YELLOW_UNIT_CODES:
        warnings = _remove_parser_warning(warnings, "Capacidade da Sala Amarela não identificada.")

    parsed["warnings"] = warnings

    if unit_match:
        parsed["upa_name"] = unit_match["canonical_name"]
        parsed["unit_code"] = unit_match["unit_code"]
        parsed["unit_match"] = unit_match
        if unit_identified_by_hint:
            parsed["unit_identified_by"] = "sender_phone"
            hint_warning = f"Unidade identificada pelo remetente ({unit_hint}), não pelo texto da mensagem."
            if hint_warning not in warnings:
                warnings.append(hint_warning)
        else:
            parsed["unit_identified_by"] = "text"
    else:
        parsed["unit_code"] = None
        parsed["unit_match"] = None
        if reported_upa_name:
            warnings = parsed.setdefault("warnings", [])
            warning = f"Unidade não reconhecida no cadastro: {reported_upa_name}."
            if warning not in warnings:
                warnings.append(warning)

    return {
        "type": "upa_update",
        "source": source,
        "received_at": effective_reported_at,
        "data": parsed,
    }


async def publish_event(event: dict[str, Any]) -> dict[str, Any]:
    app.state.last_dashboard_event = event
    save_result: dict[str, Any] | None = None
    if is_database_configured():
        save_result = await run_in_threadpool(save_event, event)
    await manager.broadcast_json(event)

    # Notificar admin via Telegram sobre anomalias de horário
    data = event.get("data", {})
    unit_name = data.get("upa_name") or data.get("unit_code") or "?"
    time_anomaly = data.get("_time_anomaly")

    if time_anomaly and time_anomaly["type"] == "absurd_time":
        _notify_admin_telegram(
            f"⚠️ <b>Horário delirante substituído</b>\n"
            f"🏥 {unit_name}\n"
            f"✏️ Digitado: {time_anomaly['typed_time']}\n"
            f"🕐 Usado: {time_anomaly['system_time']}\n"
            f"📏 Drift: {time_anomaly['drift_hours']}h\n\n"
            f"O horário digitado no giro estava {time_anomaly['drift_hours']}h "
            f"fora do horário real. Foi publicado com o horário de recebimento."
        )
    elif time_anomaly and time_anomaly["type"] == "suspect_time":
        _notify_admin_telegram(
            f"🔶 <b>Horário suspeito mantido</b>\n"
            f"🏥 {unit_name}\n"
            f"✏️ Digitado: {time_anomaly['typed_time']}\n"
            f"🕐 Sistema: {time_anomaly['system_time']}\n"
            f"📏 Drift: {time_anomaly['drift_hours']}h\n\n"
            f"O horário digitado está {time_anomaly['drift_hours']}h fora do real. "
            f"Mantido como digitado — verifique se precisa corrigir no admin."
        )

    if save_result and save_result.get("time_regression"):
        _notify_admin_telegram(
            f"🔙 <b>Regressão temporal detectada</b>\n"
            f"🏥 {save_result.get('unit_name', '?')}\n"
            f"⏮️ Anterior: {save_result['previous_received_at']}\n"
            f"⏭️ Novo: {save_result['new_received_at']}\n"
            f"📝 Evento #{save_result.get('new_event_id', '?')} "
            f"(substituiu #{save_result.get('previous_event_id', '?')})\n\n"
            f"O giro mais recente tem horário anterior ao que estava publicado. "
            f"O conteúdo foi atualizado normalmente — verifique no admin se o horário está correto."
        )

    return event


def _notify_admin_telegram(message: str) -> None:
    """Envia alerta para o chat do admin no Telegram."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_ADMIN_CHAT_ID:
        return
    try:
        payload = json.dumps({
            "chat_id": TELEGRAM_ADMIN_CHAT_ID,
            "text": message,
            "parse_mode": "HTML",
        }).encode("utf-8")
        req = request.Request(
            url=f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with request.urlopen(req, timeout=15) as response:
            response.read()
    except Exception as exc:
        import logging
        logging.getLogger(__name__).warning("Falha ao notificar admin Telegram: %s", exc)


def _format_room_line(label: str, room: dict[str, Any] | None) -> str:
    if not room:
        return f"- {label}: não identificado"
    return f"- {label}: {room['ratio']}"


def build_telegram_reply(event: dict[str, Any]) -> str:
    data = event["data"]
    rooms = data.get("rooms", {})
    specialists = data.get("specialists", {})
    warnings = data.get("warnings", [])

    isolation_mode = rooms.get("isolation_mode")
    if isolation_mode == "split":
        isolation_lines = [
            _format_room_line("Isolamento Feminino", rooms.get("isolation_female")),
            _format_room_line("Isolamento Masculino", rooms.get("isolation_male")),
            _format_room_line("Isolamento Pediátrico", rooms.get("isolation_pediatric")),
        ]
    else:
        isolation_lines = [_format_room_line("Isolamento", rooms.get("isolation_total"))]

    specialist_lines = [
        f"- Ortopedia: {'✅' if specialists.get('has_orthopedist') else '❌'}",
        f"- Cirurgia: {'✅' if specialists.get('has_surgeon') else '❌'}",
        f"- Psiquiatria: {'✅' if specialists.get('has_psychiatrist') else '❌'}",
    ]

    warning_block = ""
    if warnings:
        warning_block = "\n\n⚠️ Avisos:\n" + "\n".join(f"- {warning}" for warning in warnings)

    return "\n".join(
        [
            "📥 Giro recebido e parseado.",
            f"🏥 Unidade: {data.get('upa_name') or 'não identificada'}",
            f"🚨 Vermelha crítica: {'SIM' if data.get('is_critical') else 'NÃO'}",
            "",
            "Leitura de leitos:",
            _format_room_line("Sala Vermelha", rooms.get("red_room")),
            _format_room_line("Sala Amarela", rooms.get("yellow_room")),
            *isolation_lines,
            "",
            "Especialistas:",
            *specialist_lines,
        ]
    ) + warning_block


def _event_requires_unit_confirmation(event: dict[str, Any]) -> bool:
    data = event.get("data", {}) if isinstance(event, dict) else {}
    if not isinstance(data, dict):
        return True
    return not bool(data.get("unit_code"))


def build_missing_unit_reply(event: dict[str, Any]) -> str:
    data = event.get("data", {}) if isinstance(event, dict) else {}
    rooms = data.get("rooms", {}) if isinstance(data.get("rooms"), dict) else {}

    return "\n".join(
        [
            "⚠️ Não consegui identificar a unidade deste giro.",
            "",
            "Por favor, reenvie a mensagem informando claramente o nome da UPA/PA logo no início.",
            "Exemplos:",
            "- Unidade: PA SÃO MARCOS",
            "- Unidade: UPA PERIPERI",
            "",
            "Pré-leitura detectada:",
            _format_room_line("Sala Vermelha", rooms.get("red_room")),
            _format_room_line("Sala Amarela", rooms.get("yellow_room")),
            "",
            "Enquanto a unidade não for informada, este giro não será consolidado no painel.",
        ]
    )


def build_telegram_help_reply() -> str:
    return "\n".join(
        [
            "👋 Envie o giro com o nome da unidade para consolidar no painel.",
            "",
            "Exemplos:",
            "- Unidade: PA SÃO MARCOS",
            "- Unidade: UPA SAN MARTIN",
            "- Unidade: UPA ADROALDO ALBERGARIA",
            "",
            "Comandos disponíveis:",
            "- /resumo",
            "- /alertas",
            "- /status",
        ]
    )


def _room_has_vacancy(room: dict[str, Any] | None) -> bool:
    return bool(room and room.get("has_capacity"))


def _room_vacancies(room: dict[str, Any] | None) -> int:
    if not room:
        return 0
    occupied = room.get("occupied")
    capacity = room.get("capacity")
    if occupied is None or capacity is None or capacity <= occupied:
        return 0
    return capacity - occupied


def _format_unit_list(title: str, unit_names: list[str]) -> list[str]:
    if unit_names:
        return [f"{title}: {', '.join(unit_names)}"]
    return [f"{title}: nenhuma"]


def _unit_has_other_vacancy(rooms: dict[str, Any]) -> bool:
    other_beds = rooms.get("other_beds") or []
    return any(_room_has_vacancy(room) for room in other_beds if isinstance(room, dict))


def _room_label_is_pediatric(label: str | None) -> bool:
    normalized = (label or "").casefold()
    return "pedi" in normalized


def _adult_other_beds(rooms: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        room
        for room in (rooms.get("other_beds") or [])
        if isinstance(room, dict) and not _room_label_is_pediatric(room.get("label"))
    ]


def _adult_isolation_entries(rooms: dict[str, Any], *, unit_key: str, unit_name: str) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []

    if rooms.get("isolation_mode") == "split":
        for label, room_key in (("Feminino", "isolation_female"), ("Masculino", "isolation_male")):
            room = rooms.get(room_key)
            if not _room_has_vacancy(room):
                continue
            entries.append(
                {
                    "unit_key": unit_key,
                    "unit_name": unit_name,
                    "label": label,
                    "vacancies": _room_vacancies(room),
                    "ratio": room.get("ratio"),
                }
            )
    else:
        total_room = rooms.get("isolation_total")
        if _room_has_vacancy(total_room):
            entries.append(
                {
                    "unit_key": unit_key,
                    "unit_name": unit_name,
                    "label": "Unissex",
                    "vacancies": _room_vacancies(total_room),
                    "ratio": total_room.get("ratio"),
                }
            )

    return entries


def build_priority_buckets(status_rows: list[dict[str, Any]]) -> dict[str, Any]:
    red: list[str] = []
    yellow_male: list[dict[str, Any]] = []
    yellow_female: list[dict[str, Any]] = []
    isolation: list[dict[str, Any]] = []
    other: list[dict[str, Any]] = []
    orthopedist: list[str] = []

    for row in status_rows:
        payload = row.get("payload", {})
        data = payload.get("data", {}) if isinstance(payload, dict) else {}
        rooms = data.get("rooms", {})
        unit_name = row.get("displayed_name") or row.get("canonical_name") or row.get("upa_name") or data.get("upa_name") or row.get("source")

        if _room_has_vacancy(rooms.get("red_room")):
            red.append(unit_name)

        yellow_male_room = rooms.get("yellow_male")
        if _room_has_vacancy(yellow_male_room):
            yellow_male.append({
                "unit_key": row.get("unit_key"),
                "unit_name": unit_name,
                "vacancies": _room_vacancies(yellow_male_room),
                "ratio": yellow_male_room.get("ratio"),
            })

        yellow_female_room = rooms.get("yellow_female")
        if _room_has_vacancy(yellow_female_room):
            yellow_female.append({
                "unit_key": row.get("unit_key"),
                "unit_name": unit_name,
                "vacancies": _room_vacancies(yellow_female_room),
                "ratio": yellow_female_room.get("ratio"),
            })

        isolation.extend(_adult_isolation_entries(rooms, unit_key=row.get("unit_key"), unit_name=unit_name))

        for room in _adult_other_beds(rooms):
            if not isinstance(room, dict) or not _room_has_vacancy(room):
                continue
            other.append({
                "unit_key": row.get("unit_key"),
                "unit_name": unit_name,
                "label": room.get("label") or "internamento",
                "vacancies": _room_vacancies(room),
                "ratio": room.get("ratio"),
            })

        if row.get("has_orthopedist"):
            orthopedist.append(unit_name)

    yellow_male.sort(key=lambda item: (-item["vacancies"], item["unit_name"]))
    yellow_female.sort(key=lambda item: (-item["vacancies"], item["unit_name"]))
    isolation.sort(key=lambda item: (-item["vacancies"], item["unit_name"], item.get("label") or ""))
    other.sort(key=lambda item: (-item["vacancies"], item["unit_name"], item.get("label") or ""))

    return {
        "red_priority": red,
        "yellow_male_priority": yellow_male,
        "yellow_female_priority": yellow_female,
        "isolation_priority": isolation,
        "other_beds": other,
        "with_orthopedist": orthopedist,
        "totals": {
            "yellow_male_vacancies": sum(item["vacancies"] for item in yellow_male),
            "yellow_female_vacancies": sum(item["vacancies"] for item in yellow_female),
            "isolation_vacancies": sum(item["vacancies"] for item in isolation),
            "other_beds_vacancies": sum(item["vacancies"] for item in other),
        },
    }


def build_system_summary_text(status_rows: list[dict[str, Any]]) -> str:
    if not status_rows:
        return "📭 Ainda não há giros persistidos no banco."

    units_with_red_vacancy: list[str] = []
    units_with_yellow_male_vacancy: list[str] = []
    units_with_yellow_female_vacancy: list[str] = []
    units_with_isolation_vacancy: list[str] = []
    units_with_other_vacancy: list[str] = []
    units_with_orthopedist: list[str] = []

    normalized_rows: list[tuple[dict[str, Any], dict[str, Any], dict[str, Any], str]] = []

    buckets = build_priority_buckets(status_rows)
    units_with_red_vacancy = buckets["red_priority"]
    units_with_yellow_male_vacancy = [f"{item['unit_name']} ({item['vacancies']})" for item in buckets["yellow_male_priority"]]
    units_with_yellow_female_vacancy = [f"{item['unit_name']} ({item['vacancies']})" for item in buckets["yellow_female_priority"]]
    units_with_isolation_vacancy = [f"{item['unit_name']} · {item['label']} ({item['vacancies']})" for item in buckets["isolation_priority"]]
    units_with_other_vacancy = [f"{item['unit_name']} · {item['label']} ({item['vacancies']})" for item in buckets["other_beds"]]
    units_with_orthopedist = buckets["with_orthopedist"]

    for row in status_rows:
        payload = row.get("payload", {})
        data = payload.get("data", {}) if isinstance(payload, dict) else {}
        rooms = data.get("rooms", {})
        unit_name = row.get("displayed_name") or row.get("canonical_name") or row.get("upa_name") or data.get("upa_name") or row.get("source")
        normalized_rows.append((row, data, rooms, unit_name))

    lines = ["📊 Situação geral mais recente por unidade:"]
    lines.extend(
        [
            "",
            "📌 Visão rápida:",
            *_format_unit_list("- UPAs com vaga na vermelha", units_with_red_vacancy),
            *_format_unit_list("- Vagas amarelas masculinas", units_with_yellow_male_vacancy),
            *_format_unit_list("- Vagas amarelas femininas", units_with_yellow_female_vacancy),
            *_format_unit_list("- Isolamento adulto disponível", units_with_isolation_vacancy),
            *_format_unit_list("- Outros leitos / internamento", units_with_other_vacancy),
            *_format_unit_list("- UPAs com ortopedista", units_with_orthopedist),
        ]
    )

    for row, data, rooms, unit_name in normalized_rows:
        red_room = rooms.get("red_room")
        yellow_room = rooms.get("yellow_room")
        yellow_male = rooms.get("yellow_male")
        yellow_female = rooms.get("yellow_female")
        isolation_mode = rooms.get("isolation_mode")

        isolation_text = "n/i"
        if isolation_mode == "split":
            female = rooms.get("isolation_female")
            male = rooms.get("isolation_male")
            pediatric = rooms.get("isolation_pediatric")
            parts = []
            if female:
                parts.append(f"F {female['ratio']}")
            if male:
                parts.append(f"M {male['ratio']}")
            if pediatric:
                parts.append(f"P {pediatric['ratio']}")
            isolation_text = ", ".join(parts) if parts else "n/i"
        elif rooms.get("isolation_total"):
            isolation_text = rooms["isolation_total"]["ratio"]

        other_beds = rooms.get("other_beds") or []
        other_beds_text = ", ".join(
            f"{room.get('label', 'internamento')} {room.get('ratio', 'n/i')}"
            for room in other_beds
            if isinstance(room, dict)
        ) or "n/i"

        lines.extend(
            [
                "",
                f"🏥 {unit_name}",
                f"- Vermelha: {red_room['ratio'] if red_room else 'n/i'}",
                f"- Amarela/observação: {yellow_room['ratio'] if yellow_room else 'n/i'}",
                f"- Amarela masculina: {yellow_male['ratio'] if yellow_male else 'n/i'} | feminina: {yellow_female['ratio'] if yellow_female else 'n/i'}",
                f"- Internamento / outros leitos: {other_beds_text}",
                f"- Isolamento: {isolation_text}",
                f"- Ortopedia: {'✅' if row.get('has_orthopedist') else '❌'} | Psiquiatria: {'✅' if row.get('has_psychiatrist') else '❌'} | Cirurgia: {'✅' if row.get('has_surgeon') else '❌'}",
            ]
        )

    return "\n".join(lines)


def build_alerts_text(alert_rows: list[dict[str, Any]]) -> str:
    if not alert_rows:
        return "🔕 Nenhum alerta recente registrado."

    lines = ["🚨 Alertas recentes:"]
    for row in alert_rows[:10]:
        severity = row.get("severity", "info").upper()
        lines.extend(
            [
                "",
                f"[{severity}] {row.get('unit_name')}",
                f"- {row.get('title')}",
                f"- {row.get('message')}",
            ]
        )
    return "\n".join(lines)


def send_telegram_message(chat_id: int | str, text: str) -> None:
    if not TELEGRAM_BOT_TOKEN:
        return

    payload = json.dumps(
        {
            "chat_id": chat_id,
            "text": text,
        }
    ).encode("utf-8")

    req = request.Request(
        url=f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with request.urlopen(req, timeout=15) as response:
            response.read()
    except error.URLError as exc:
        raise RuntimeError(f"Falha ao responder no Telegram: {exc}") from exc


@app.get("/health")
async def healthcheck() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/health")
async def api_healthcheck() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/last-event", summary="Último evento parseado")
async def get_last_event() -> dict[str, Any]:
    if app.state.last_dashboard_event is None:
        return {
            "status": "empty",
            "message": "Nenhum texto foi parseado ainda.",
            "event": None,
        }

    return {
        "status": "ok",
        "event": app.state.last_dashboard_event,
    }


@app.get("/api/history", summary="Histórico recente de eventos persistidos")
async def get_history(limit: int = 20) -> dict[str, Any]:
    if not is_database_configured():
        return {
            "status": "disabled",
            "message": "Banco não configurado para persistência.",
            "events": [],
        }

    events = await run_in_threadpool(get_latest_events, limit)
    return {
        "status": "ok",
        "events": events,
    }


@app.get("/api/summary", summary="Resumo operacional consolidado por unidade")
async def get_summary() -> dict[str, Any]:
    if not is_database_configured():
        return {
            "status": "disabled",
            "message": "Banco não configurado para persistência.",
            "units": [],
            "pending_unit_confirmations": [],
        }

    units = await run_in_threadpool(get_latest_status_by_unit)
    pending_unit_confirmations = await run_in_threadpool(get_pending_unit_confirmations, 20)
    return {
        "status": "ok",
        "priority_buckets": build_priority_buckets(units),
        "units": units,
        "pending_unit_confirmations": pending_unit_confirmations,
    }


@app.get("/api/alerts", summary="Alertas recentes de mudanças relevantes")
async def get_alerts(limit: int = 20) -> dict[str, Any]:
    if not is_database_configured():
        return {
            "status": "disabled",
            "message": "Banco não configurado para persistência.",
            "alerts": [],
        }

    alerts = await run_in_threadpool(get_recent_alerts, limit)
    return {
        "status": "ok",
        "alerts": alerts,
    }


@app.get("/api/units", summary="Cadastro de UPAs e aliases conhecidos")
async def get_units() -> dict[str, Any]:
    units = await run_in_threadpool(get_registered_units)
    return {
        "status": "ok",
        "units": units,
    }


@app.get("/api/telegram/status", summary="Status da integração com Telegram")
async def telegram_status() -> dict[str, Any]:
    return {
        "status": "ok",
        "telegram": {
            "bot_token_configured": bool(TELEGRAM_BOT_TOKEN),
            "secret_configured": bool(TELEGRAM_WEBHOOK_SECRET),
            "expected_webhook_url": f"{PUBLIC_BASE_URL}{PUBLIC_WEBHOOK_PATH}",
            "public_webhook_path": PUBLIC_WEBHOOK_PATH,
            "webhook_secret_header": "X-Telegram-Bot-Api-Secret-Token" if TELEGRAM_WEBHOOK_SECRET else None,
        },
    }


@app.get("/api/playground", response_class=HTMLResponse, summary="Interface de teste manual do parser")
async def parser_playground() -> str:
        return """
<!DOCTYPE html>
<html lang="pt-BR">
<head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <title>Giro de Leitos · Playground</title>
    <style>
        body { font-family: Arial, sans-serif; max-width: 1100px; margin: 0 auto; padding: 24px; background: #f8fafc; color: #0f172a; }
        h1 { margin-bottom: 8px; }
        .grid { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }
        textarea { width: 100%; min-height: 360px; padding: 12px; border: 1px solid #cbd5e1; border-radius: 12px; font-family: monospace; }
        input[type=datetime-local] { width: 100%; padding: 12px; border: 1px solid #cbd5e1; border-radius: 12px; font-family: Arial, sans-serif; }
        pre { background: #0f172a; color: #e2e8f0; padding: 16px; border-radius: 12px; overflow: auto; min-height: 360px; }
        button { background: #2563eb; color: white; border: none; border-radius: 10px; padding: 12px 18px; font-weight: 700; cursor: pointer; }
        button.secondary { background: #475569; }
        .hint { color: #475569; margin-bottom: 18px; }
        .field { margin-top: 12px; }
        .field label { display: block; font-size: 12px; font-weight: 700; color: #334155; margin-bottom: 6px; text-transform: uppercase; letter-spacing: .08em; }
    </style>
</head>
<body>
    <h1>Playground do Parser</h1>
    <p class="hint">Cole o texto do giro, informe o horário oficial quando ele não vier no texto e veja o JSON parseado imediatamente. O último resultado também fica disponível em <code>/giro-de-leitos/api/last-event</code>.</p>
    <div class="grid">
        <div>
            <textarea id="payload">UPA BROTAS\nSALA VERMELHA 03/04\nSALA AMARELA 05/08\nISOLAMENTO MASC 01/02\nISOLAMENTO FEM 00/02\nISOLAMENTO PED 01/01\nCORREDOR:\n1. AVC isquêmico\n2. DOR TORÁCICA\nORTOPEDISTA: SIM\nCIRURGIÃO: NÃO</textarea>
            <div class="field">
                <label for="officialAt">Horário oficial do giro</label>
                <input id="officialAt" type="datetime-local" />
            </div>
            <div style="margin-top: 12px; display: flex; gap: 8px; align-items: center;">
                <button onclick="sendPayload()">Enviar para parser</button>
                <button class="secondary" onclick="loadLastEvent()">Ver último parse</button>
                <span id="status"></span>
            </div>
        </div>
        <pre id="output">Aguardando envio...</pre>
    </div>
    <script>
        async function sendPayload() {
            const text = document.getElementById('payload').value;
            const officialAt = document.getElementById('officialAt').value;
            const status = document.getElementById('status');
            const output = document.getElementById('output');
            status.textContent = 'Enviando...';
            try {
                const res = await fetch('./ingest/manual', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        text,
                        source: 'playground',
                        official_at: officialAt ? new Date(officialAt).toISOString() : null
                    })
                });
                const data = await res.json();
                output.textContent = JSON.stringify(data, null, 2);
                status.textContent = res.ok ? 'OK' : 'Erro';
            } catch (error) {
                output.textContent = String(error);
                status.textContent = 'Falha';
            }
        }

        async function loadLastEvent() {
            const status = document.getElementById('status');
            const output = document.getElementById('output');
            status.textContent = 'Carregando...';
            try {
                const res = await fetch('./last-event');
                const data = await res.json();
                output.textContent = JSON.stringify(data, null, 2);
                status.textContent = res.ok ? 'OK' : 'Erro';
            } catch (error) {
                output.textContent = String(error);
                status.textContent = 'Falha';
            }
        }
    </script>
</body>
</html>
        """


@app.post("/api/webhook/whatsapp", summary="Webhook bruto legado de WhatsApp")
async def whatsapp_webhook(payload: WebhookPayload) -> dict[str, Any]:
    event = build_dashboard_event(payload.text, payload.source, payload.received_at)
    event = await publish_event(event)

    return {
        "status": "accepted",
        "message": "Payload recebido, parseado e publicado para os clientes conectados.",
        "event": event,
    }


@app.post(
    "/api/ingest/manual",
    summary="Entrada manual para frontend ou navegador",
    description="Rota pronta para o frontend Next.js ou para uso direto no navegador. Recebe o texto bruto do giro, parseia e publica o evento em tempo real no WebSocket.",
)
async def manual_ingest(payload: ManualIngestPayload) -> dict[str, Any]:
    event = build_dashboard_event(payload.text, payload.source, unit_hint=payload.unit_hint, official_at=payload.official_at)
    event = await publish_event(event)

    return {
        "status": "accepted",
        "message": "Texto manual parseado com sucesso e transmitido ao dashboard.",
        "event": event,
        "next_steps": {
            "websocket": "/giro-de-leitos/ws/dashboard",
            "telegram_webhook": "/giro-de-leitos/api/webhook/telegram",
        },
    }


@app.post(
    "/api/ingest/whatsapp-bridge",
    summary="Entrada automática via WhatsApp bridge (Baileys)",
    description=(
        "Recebe o texto bruto capturado automaticamente do grupo WhatsApp. "
        "Valida se o giro contém os dados mínimos (unidade e horário). "
        "Se faltar algo, retorna issues detalhadas para o bridge responder no grupo."
    ),
)
async def whatsapp_bridge_ingest(payload: WhatsAppBridgeIngestPayload) -> dict[str, Any]:
    event = build_dashboard_event(payload.text, payload.source, unit_hint=payload.unit_hint)

    data = event.get("data", {})
    warnings = data.get("warnings", [])
    unit_code = data.get("unit_code")
    reported_at = data.get("reported_at")
    unit_identified_by = data.get("unit_identified_by")

    issues: list[dict[str, str]] = []

    # Verificar se tem unidade
    if not unit_code:
        issues.append({
            "field": "unit",
            "severity": "blocking",
            "message": "Não foi possível identificar a unidade (UPA/PA). O giro NÃO será publicado até que informe o nome.",
        })

    # Verificar se tem horário
    has_time_warning = any("Horário oficial não identificado" in w for w in warnings)
    if has_time_warning:
        severity = "warning" if unit_code else "blocking"
        issues.append({
            "field": "time",
            "severity": severity,
            "message": "Horário oficial do giro não identificado no texto. Será usado o horário de recebimento.",
        })

    has_blocking = any(i["severity"] == "blocking" for i in issues)

    # ── Dry run: parsear e retornar sem salvar/publicar ──────────────────
    if payload.dry_run:
        return {
            "status": "accepted" if not has_blocking else "pending",
            "message": "[DRY RUN] Resultado do parsing — nenhum dado foi salvo ou publicado.",
            "event": event,
            "issues": issues,
            "reply_text": _build_whatsapp_missing_data_reply(issues, data) if has_blocking else None,
            "dry_run": True,
        }

    if has_blocking:
        # Salvar como pendente mas NÃO publicar no dashboard
        if is_database_configured():
            await run_in_threadpool(save_event, event)
        # Broadcast de refresh para mostrar pendência
        await manager.broadcast_json(event)

        return {
            "status": "pending",
            "message": "Giro recebido mas com dados insuficientes. Não publicado no painel.",
            "event": event,
            "issues": issues,
            "reply_text": _build_whatsapp_missing_data_reply(issues, data),
        }

    # Tudo OK — publicar normalmente
    event = await publish_event(event)

    return {
        "status": "accepted",
        "message": "Giro do WhatsApp parseado e publicado com sucesso.",
        "event": event,
        "issues": issues,
        "reply_text": None,
    }


def _build_whatsapp_missing_data_reply(issues: list[dict[str, str]], data: dict[str, Any]) -> str:
    """Constrói mensagem amigável para responder no grupo WhatsApp."""
    rooms = data.get("rooms", {}) if isinstance(data.get("rooms"), dict) else {}
    lines = ["⚠️ *GIRO NÃO PUBLICADO* ⚠️", ""]

    missing_unit = any(i["field"] == "unit" for i in issues)
    missing_time = any(i["field"] == "time" for i in issues)

    if missing_unit and missing_time:
        lines.append("Faltam o *nome da unidade* e o *horário oficial*.")
    elif missing_unit:
        lines.append("Falta o *nome da unidade*.")
    elif missing_time:
        lines.append("Falta o *horário oficial* do giro.")

    lines.append("")
    lines.append("Por favor, responda com as informações que faltam:")
    lines.append("")

    if missing_unit:
        lines.append("📍 Nome da unidade — ex: _UPA BROTAS_ ou _PA SÃO MARCOS_")
    if missing_time:
        lines.append("🕐 Horário oficial — ex: _14:30_ ou _Horário: 14h30_")

    lines.append("")

    red = rooms.get("red_room")
    yellow = rooms.get("yellow_room")
    if red or yellow:
        lines.append("_Pré-leitura detectada:_")
        if red:
            lines.append(f"  Vermelha: {red.get('occupied', '?')}/{red.get('capacity', '?')}")
        if yellow:
            lines.append(f"  Amarela: {yellow.get('occupied', '?')}/{yellow.get('capacity', '?')}")
        lines.append("")

    lines.append("Enquanto os dados não forem completados, este giro *não aparecerá no painel*.")

    return "\n".join(lines)


@app.patch("/api/units/{unit_key}/reported-at", summary="Editar horário oficial do último giro de uma unidade")
async def patch_unit_reported_at(unit_key: str, payload: UpdateReportedAtPayload) -> dict[str, Any]:
    if not is_database_configured():
        return {
            "status": "disabled",
            "message": "Banco não configurado para persistência.",
        }

    updated_unit = await run_in_threadpool(update_unit_reported_at, unit_key, payload.reported_at.astimezone(timezone.utc).isoformat())
    if not updated_unit:
        raise HTTPException(status_code=404, detail="Unidade não encontrada para atualização do horário oficial.")

    return {
        "status": "ok",
        "unit": updated_unit,
    }


@app.patch("/api/pending-units/{event_id}/resolve", summary="Resolver pendência de unidade não identificada")
async def patch_pending_unit_resolution(event_id: int, payload: ResolvePendingUnitPayload) -> dict[str, Any]:
    if not is_database_configured():
        return {
            "status": "disabled",
            "message": "Banco não configurado para persistência.",
        }

    event_row = await run_in_threadpool(get_parsed_event, event_id)
    if not event_row:
        raise HTTPException(status_code=404, detail="Evento pendente não encontrado.")

    rebuilt_event = build_dashboard_event(
        event_row.get("raw_text") or "",
        event_row.get("source") or "manual",
        received_at=event_row.get("received_at"),
        unit_hint=payload.unit_name,
    )

    if not rebuilt_event.get("data", {}).get("unit_code"):
        raise HTTPException(status_code=400, detail="Não foi possível reconhecer a unidade informada. Tente outro nome ou alias.")

    updated_unit = await run_in_threadpool(resolve_pending_unit_confirmation, event_id, rebuilt_event)
    if not updated_unit:
        raise HTTPException(status_code=500, detail="Falha ao aplicar a resolução da pendência.")

    await manager.broadcast_json({"type": "refresh"})

    return {
        "status": "ok",
        "unit": updated_unit,
        "event": rebuilt_event,
    }


@app.post(
    "/api/webhook/telegram",
    summary="Webhook do bot Telegram",
    description="Endpoint para receber atualizações do bot Telegram. O texto da mensagem recebida é parseado e publicado imediatamente para o dashboard.",
)
async def telegram_webhook(
    payload: TelegramUpdatePayload,
    x_telegram_bot_api_secret_token: str | None = Header(default=None),
) -> dict[str, Any]:
    if TELEGRAM_WEBHOOK_SECRET and x_telegram_bot_api_secret_token != TELEGRAM_WEBHOOK_SECRET:
        raise HTTPException(status_code=401, detail="Secret do webhook do Telegram inválido.")

    incoming_message = payload.message or payload.edited_message or {}
    text = incoming_message.get("text")

    if not text:
        raise HTTPException(status_code=400, detail="Update do Telegram sem campo de texto utilizável.")

    normalized_text = text.strip().lower()
    chat = incoming_message.get("chat") or {}
    chat_id = chat.get("id")

    if normalized_text == "/start":
        reply_sent = False
        reply_error: str | None = None
        if chat_id is not None:
            try:
                send_telegram_message(chat_id, build_telegram_help_reply())
                reply_sent = bool(TELEGRAM_BOT_TOKEN)
            except RuntimeError as exc:
                reply_error = str(exc)

        return {
            "status": "accepted",
            "message": "Comando /start processado sem gerar evento operacional.",
            "telegram_reply": {
                "attempted": chat_id is not None,
                "sent": reply_sent,
                "error": reply_error,
            },
        }

    if normalized_text in {"/resumo", "/status", "/giro"}:
        status_rows = await run_in_threadpool(get_latest_status_by_unit) if is_database_configured() else []
        summary_text = build_system_summary_text(status_rows)

        reply_sent = False
        reply_error: str | None = None
        if chat_id is not None:
            try:
                send_telegram_message(chat_id, summary_text)
                reply_sent = bool(TELEGRAM_BOT_TOKEN)
            except RuntimeError as exc:
                reply_error = str(exc)

        return {
            "status": "accepted",
            "message": "Comando de resumo processado.",
            "summary": summary_text,
            "telegram_reply": {
                "attempted": chat_id is not None,
                "sent": reply_sent,
                "error": reply_error,
            },
        }

    if normalized_text in {"/alertas", "/alerta"}:
        alert_rows = await run_in_threadpool(get_recent_alerts, 10) if is_database_configured() else []
        alerts_text = build_alerts_text(alert_rows)

        reply_sent = False
        reply_error: str | None = None
        if chat_id is not None:
            try:
                send_telegram_message(chat_id, alerts_text)
                reply_sent = bool(TELEGRAM_BOT_TOKEN)
            except RuntimeError as exc:
                reply_error = str(exc)

        return {
            "status": "accepted",
            "message": "Comando de alertas processado.",
            "alerts": alert_rows,
            "telegram_reply": {
                "attempted": chat_id is not None,
                "sent": reply_sent,
                "error": reply_error,
            },
        }

    if normalized_text.startswith("/"):
        reply_sent = False
        reply_error: str | None = None
        if chat_id is not None:
            try:
                send_telegram_message(chat_id, build_telegram_help_reply())
                reply_sent = bool(TELEGRAM_BOT_TOKEN)
            except RuntimeError as exc:
                reply_error = str(exc)

        return {
            "status": "accepted",
            "message": "Comando não operacional ignorado para o painel.",
            "telegram_reply": {
                "attempted": chat_id is not None,
                "sent": reply_sent,
                "error": reply_error,
            },
        }

    chat_title = chat.get("title") or chat.get("username") or chat.get("id")
    source = f"telegram:{chat_title}" if chat_title else "telegram"

    event = build_dashboard_event(text, source)

    if _event_requires_unit_confirmation(event):
        if is_database_configured():
            await run_in_threadpool(save_event, event)
        await manager.broadcast_json(event)

        reply_sent = False
        reply_error: str | None = None

        if chat_id is not None:
            try:
                send_telegram_message(chat_id, build_missing_unit_reply(event))
                reply_sent = bool(TELEGRAM_BOT_TOKEN)
            except RuntimeError as exc:
                reply_error = str(exc)

        return {
            "status": "accepted",
            "message": "Update do Telegram recebido, mas sem unidade reconhecida; solicitada confirmação ao remetente.",
            "event": event,
            "telegram_reply": {
                "attempted": chat_id is not None,
                "sent": reply_sent,
                "error": reply_error,
            },
        }

    event = await publish_event(event)

    reply_sent = False
    reply_error: str | None = None

    if chat_id is not None:
        try:
            send_telegram_message(chat_id, build_telegram_reply(event))
            reply_sent = bool(TELEGRAM_BOT_TOKEN)
        except RuntimeError as exc:
            reply_error = str(exc)

    return {
        "status": "accepted",
        "message": "Update do Telegram parseado e publicado para os clientes conectados.",
        "event": event,
        "telegram_reply": {
            "attempted": chat_id is not None,
            "sent": reply_sent,
            "error": reply_error,
        },
    }


@app.get("/api/stale-units", summary="UPAs com mais de N horas sem atualização")
async def get_stale_units(hours: float = 6.0) -> dict[str, Any]:
    """Retorna unidades que não enviaram giro nas últimas `hours` horas.
    Usa updated_at (quando a mensagem foi recebida pelo sistema) e não received_at
    (horário extraído do texto da mensagem) para evitar falsos positivos."""
    if not is_database_configured():
        return {"status": "disabled", "stale_units": []}

    units = await run_in_threadpool(get_latest_status_by_unit)
    now = datetime.now(timezone.utc)
    threshold = now - __import__("datetime").timedelta(hours=hours)

    stale: list[dict[str, Any]] = []
    for u in units:
        # Usar updated_at (momento real de recebimento) em vez de received_at (horário do texto)
        ts_raw = u.get("updated_at") or u.get("received_at")
        if not ts_raw:
            # Unidade nunca enviou giro — considerar como muito atrasada
            stale.append({
                "unit_code": u.get("unit_code"),
                "displayed_name": u.get("displayed_name"),
                "received_at": None,
                "hours_ago": 999.0,
            })
            continue
        if isinstance(ts_raw, str):
            try:
                ts_dt = datetime.fromisoformat(ts_raw.replace("Z", "+00:00"))
            except ValueError:
                continue
        else:
            ts_dt = ts_raw
        if ts_dt.tzinfo is None:
            ts_dt = ts_dt.replace(tzinfo=timezone.utc)
        if ts_dt < threshold:
            hours_ago = round((now - ts_dt).total_seconds() / 3600, 1)
            stale.append({
                "unit_code": u.get("unit_code"),
                "displayed_name": u.get("displayed_name"),
                "received_at": ts_raw,
                "hours_ago": hours_ago,
            })

    stale.sort(key=lambda x: x["hours_ago"], reverse=True)
    return {
        "status": "ok",
        "threshold_hours": hours,
        "count": len(stale),
        "stale_units": stale,
    }


# ── Admin API ────────────────────────────────────────────────────────────

ADMIN_USER = os.getenv("ADMIN_USER", "").strip()
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "").strip()
# Token de sessão derivado das credenciais (stateless)
ADMIN_SESSION_TOKEN = (
    hashlib.sha256(f"{ADMIN_USER}:{ADMIN_PASSWORD}:giro-admin-session".encode()).hexdigest()
    if ADMIN_USER and ADMIN_PASSWORD else ""
)


def _check_admin_auth(authorization: str | None = Header(default=None)) -> None:
    """Valida o header Authorization: Bearer <session_token>."""
    if not ADMIN_SESSION_TOKEN:
        return
    if not authorization:
        raise HTTPException(status_code=401, detail="Token de sessão ausente.")
    parts = authorization.split(" ", 1)
    token = parts[1] if len(parts) == 2 and parts[0].lower() == "bearer" else parts[0]
    if not hmac.compare_digest(token, ADMIN_SESSION_TOKEN):
        raise HTTPException(status_code=401, detail="Sessão inválida. Faça login novamente.")


@app.post("/api/admin/auth", summary="Login admin com usuário e senha")
async def admin_login(body: AdminLoginPayload) -> dict[str, Any]:
    if not ADMIN_USER or not ADMIN_PASSWORD:
        raise HTTPException(status_code=503, detail="Credenciais admin não configuradas no servidor.")
    user_ok = hmac.compare_digest(body.username, ADMIN_USER)
    pass_ok = hmac.compare_digest(body.password, ADMIN_PASSWORD)
    if not (user_ok and pass_ok):
        raise HTTPException(status_code=401, detail="Usuário ou senha incorretos.")
    return {"status": "ok", "token": ADMIN_SESSION_TOKEN}


@app.get("/api/admin/events", summary="Listar eventos recentes para admin")
async def admin_list_events(limit: int = 30, authorization: str | None = Header(default=None)) -> dict[str, Any]:
    _check_admin_auth(authorization)
    if not is_database_configured():
        raise HTTPException(status_code=503, detail="Banco não configurado.")
    events = await run_in_threadpool(get_latest_events, limit)
    return {"status": "ok", "events": events}


@app.get("/api/admin/events/{event_id}", summary="Detalhe de um evento")
async def admin_get_event(event_id: int, authorization: str | None = Header(default=None)) -> dict[str, Any]:
    _check_admin_auth(authorization)
    if not is_database_configured():
        raise HTTPException(status_code=503, detail="Banco não configurado.")
    ev = await run_in_threadpool(get_event_detail, event_id)
    if not ev:
        raise HTTPException(status_code=404, detail="Evento não encontrado.")
    return {"status": "ok", "event": ev}


@app.patch("/api/admin/events/{event_id}", summary="Editar evento (UPA, horário, quartos, especialistas)")
async def admin_patch_event(event_id: int, payload: AdminUpdateEventPayload, authorization: str | None = Header(default=None)) -> dict[str, Any]:
    _check_admin_auth(authorization)
    if not is_database_configured():
        raise HTTPException(status_code=503, detail="Banco não configurado.")
    fields = payload.model_dump(exclude_none=True)
    if "reported_at" in fields:
        fields["reported_at"] = payload.reported_at.astimezone(timezone.utc).isoformat()
    updated = await run_in_threadpool(admin_update_event, event_id, **fields)
    if not updated:
        raise HTTPException(status_code=404, detail="Evento não encontrado.")
    await manager.broadcast_json({"type": "refresh"})
    return {"status": "ok", "event": updated}


@app.delete("/api/admin/events/{event_id}", summary="Apagar evento e fazer rollback")
async def admin_delete_event(event_id: int, authorization: str | None = Header(default=None)) -> dict[str, Any]:
    _check_admin_auth(authorization)
    if not is_database_configured():
        raise HTTPException(status_code=503, detail="Banco não configurado.")
    deleted = await run_in_threadpool(delete_event, event_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Evento não encontrado.")
    await manager.broadcast_json({"type": "refresh"})
    return {"status": "ok", "message": "Evento apagado. Dashboard atualizado com o giro anterior."}


@app.get("/api/admin", response_class=HTMLResponse, summary="Painel admin mobile")
async def admin_panel() -> str:
    return ADMIN_HTML


ADMIN_HTML = r"""
<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no"/>
<title>Giro Admin</title>
<style>
:root{--bg:#0f172a;--card:#1e293b;--border:#334155;--text:#e2e8f0;--muted:#94a3b8;--accent:#3b82f6;--danger:#ef4444;--success:#22c55e;--warn:#f59e0b}
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;background:var(--bg);color:var(--text);padding:12px;-webkit-tap-highlight-color:transparent}
h1{font-size:20px;margin-bottom:12px;display:flex;align-items:center;gap:8px}
h1 span{font-size:14px;color:var(--muted);font-weight:400}
.toolbar{display:flex;gap:8px;margin-bottom:12px;flex-wrap:wrap}
.toolbar select,.toolbar input,.toolbar button{padding:8px 12px;border-radius:8px;border:1px solid var(--border);background:var(--card);color:var(--text);font-size:14px}
.toolbar button{background:var(--accent);border:none;font-weight:600;cursor:pointer;white-space:nowrap}
.toolbar button:active{opacity:.7}
.card{background:var(--card);border:1px solid var(--border);border-radius:12px;padding:14px;margin-bottom:10px}
.card-header{display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:6px}
.card-title{font-weight:700;font-size:15px}
.card-id{font-size:12px;color:var(--muted)}
.card-meta{font-size:13px;color:var(--muted);margin-bottom:8px}
.card-meta b{color:var(--text)}
.badge{display:inline-block;padding:2px 8px;border-radius:6px;font-size:11px;font-weight:700}
.badge-critical{background:var(--danger);color:#fff}
.badge-ok{background:var(--success);color:#000}
.badge-pending{background:var(--warn);color:#000}
.card-actions{display:flex;gap:8px;margin-top:10px;flex-wrap:wrap}
.card-actions button{padding:7px 14px;border-radius:8px;border:none;font-size:13px;font-weight:600;cursor:pointer}
.btn-edit{background:#6366f1;color:#fff}
.btn-delete{background:var(--danger);color:#fff}
.btn-raw{background:var(--border);color:var(--text)}
.btn-edit:active,.btn-delete:active,.btn-raw:active{opacity:.7}
.raw-text{background:var(--bg);border:1px solid var(--border);border-radius:8px;padding:10px;margin-top:8px;font-size:12px;white-space:pre-wrap;word-break:break-word;max-height:200px;overflow-y:auto;display:none}
.modal-overlay{position:fixed;inset:0;background:rgba(0,0,0,.7);display:flex;align-items:flex-end;justify-content:center;z-index:100;display:none}
.modal-overlay.open{display:flex}
.modal{background:var(--card);border-radius:16px 16px 0 0;padding:20px;width:100%;max-width:500px;max-height:85vh;overflow-y:auto}
.modal h2{font-size:17px;margin-bottom:14px}
.field{margin-bottom:14px}
.field label{display:block;font-size:12px;font-weight:700;color:var(--muted);margin-bottom:4px;text-transform:uppercase;letter-spacing:.05em}
.field input,.field select{width:100%;padding:10px;border-radius:8px;border:1px solid var(--border);background:var(--bg);color:var(--text);font-size:14px}
.modal-actions{display:flex;gap:10px;margin-top:16px}
.modal-actions button{flex:1;padding:12px;border-radius:10px;border:none;font-size:15px;font-weight:700;cursor:pointer}
.btn-save{background:var(--accent);color:#fff}
.btn-cancel{background:var(--border);color:var(--text)}
.toast{position:fixed;top:16px;left:50%;transform:translateX(-50%);background:var(--success);color:#000;padding:10px 20px;border-radius:10px;font-weight:700;font-size:14px;z-index:200;display:none}
.toast.error{background:var(--danger);color:#fff}
.empty{text-align:center;color:var(--muted);padding:40px 0}
.confirm-overlay{position:fixed;inset:0;background:rgba(0,0,0,.7);display:flex;align-items:center;justify-content:center;z-index:150;display:none}
.confirm-overlay.open{display:flex}
.confirm-box{background:var(--card);border-radius:14px;padding:24px;width:90%;max-width:340px;text-align:center}
.confirm-box p{margin-bottom:16px;font-size:15px}
.confirm-box .confirm-actions{display:flex;gap:10px}
.confirm-box .confirm-actions button{flex:1;padding:12px;border-radius:10px;border:none;font-size:15px;font-weight:700;cursor:pointer}
#loadMore{width:100%;padding:12px;border-radius:10px;border:1px solid var(--border);background:var(--card);color:var(--accent);font-size:14px;font-weight:600;cursor:pointer;margin-top:4px}
.login-screen{display:flex;flex-direction:column;align-items:center;justify-content:center;min-height:80vh;gap:16px}
.login-screen h2{font-size:22px}
.login-screen input{width:260px;padding:14px;border-radius:10px;border:1px solid var(--border);background:var(--card);color:var(--text);font-size:16px;text-align:center}
.login-screen button{width:260px;padding:14px;border-radius:10px;border:none;background:var(--accent);color:#fff;font-size:16px;font-weight:700;cursor:pointer}
.login-screen button:active{opacity:.7}
.login-error{color:var(--danger);font-size:14px;min-height:20px}
.logout-btn{background:none;border:1px solid var(--border);color:var(--muted);padding:8px 12px;border-radius:8px;font-size:13px;cursor:pointer}
.section-title{font-size:13px;color:var(--accent);margin:14px 0 6px;border-top:1px solid var(--border);padding-top:12px}
.field-row{display:flex;gap:10px}
.field.half{flex:1}
.field.half input{width:100%}
.toggles{flex-wrap:wrap;gap:14px;margin-bottom:8px}
.toggle{display:flex;align-items:center;gap:6px;font-size:14px;cursor:pointer;user-select:none}
.toggle input[type=checkbox]{width:20px;height:20px;accent-color:var(--accent)}
</style>
</head>
<body>

<div id="loginScreen" class="login-screen" style="display:none">
    <h2>🔒 Giro Admin</h2>
    <input type="text" id="loginUser" placeholder="Usuário" autocomplete="username"/>
    <input type="password" id="loginPass" placeholder="Senha" autocomplete="current-password"/>
    <button onclick="doLogin()">Entrar</button>
    <div class="login-error" id="loginError"></div>
</div>

<div id="appMain" style="display:none">
<input type="hidden" id="tokenInput"/>
<h1>⚙️ Giro Admin <span>v1</span></h1>
<div class="toolbar">
    <select id="limitSel"><option value="15">15</option><option value="30" selected>30</option><option value="50">50</option><option value="100">100</option></select>
    <button onclick="loadEvents()">🔄 Carregar</button>
    <button class="logout-btn" onclick="doLogout()">🚪 Sair</button>
</div>
<div id="eventList"></div>
<button id="loadMore" style="display:none" onclick="loadMore()">Carregar mais...</button>
</div><!-- /appMain -->

<div class="modal-overlay" id="editModal">
    <div class="modal">
        <h2>✏️ Editar Giro</h2>
        <input type="hidden" id="editId"/>
        <div class="field">
            <label>UPA / Unidade</label>
            <input type="text" id="editUpa"/>
        </div>
        <div class="field">
            <label>Unit Code</label>
            <select id="editUnitCode"><option value="">— não alterar —</option></select>
        </div>
        <div class="field">
            <label>Data/Hora oficial</label>
            <input type="datetime-local" id="editTime"/>
        </div>

        <h3 class="section-title">🔴 Sala Vermelha</h3>
        <div class="field-row">
            <div class="field half"><label>Ocupados</label><input type="number" id="editRedOcc" min="0"/></div>
            <div class="field half"><label>Capacidade</label><input type="number" id="editRedCap" min="0"/></div>
        </div>

        <h3 class="section-title">🟡 Sala Amarela</h3>
        <div class="field">
            <label>Modo</label>
            <select id="editYellowMode">
                <option value="total">Total</option>
                <option value="split">Separado (M/F)</option>
            </select>
        </div>
        <div id="yellowTotalFields">
            <div class="field-row">
                <div class="field half"><label>Ocupados</label><input type="number" id="editYellowOcc" min="0"/></div>
                <div class="field half"><label>Capacidade</label><input type="number" id="editYellowCap" min="0"/></div>
            </div>
        </div>
        <div id="yellowSplitFields" style="display:none">
            <div class="field-row">
                <div class="field half"><label>Masc. Ocup.</label><input type="number" id="editYellowMaleOcc" min="0"/></div>
                <div class="field half"><label>Masc. Cap.</label><input type="number" id="editYellowMaleCap" min="0"/></div>
            </div>
            <div class="field-row">
                <div class="field half"><label>Fem. Ocup.</label><input type="number" id="editYellowFemOcc" min="0"/></div>
                <div class="field half"><label>Fem. Cap.</label><input type="number" id="editYellowFemCap" min="0"/></div>
            </div>
        </div>

        <h3 class="section-title">🟣 Isolamento</h3>
        <div class="field">
            <label>Modo</label>
            <select id="editIsoMode">
                <option value="">— não alterar —</option>
                <option value="total">Total</option>
                <option value="split">Separado (M/F/Ped)</option>
            </select>
        </div>
        <div id="isoTotalFields">
            <div class="field-row">
                <div class="field half"><label>Total Ocup.</label><input type="number" id="editIsoTotalOcc" min="0"/></div>
                <div class="field half"><label>Total Cap.</label><input type="number" id="editIsoTotalCap" min="0"/></div>
            </div>
        </div>
        <div id="isoSplitFields">
            <div class="field-row">
                <div class="field half"><label>Masc. Ocup.</label><input type="number" id="editIsoMaleOcc" min="0"/></div>
                <div class="field half"><label>Masc. Cap.</label><input type="number" id="editIsoMaleCap" min="0"/></div>
            </div>
            <div class="field-row">
                <div class="field half"><label>Fem. Ocup.</label><input type="number" id="editIsoFemOcc" min="0"/></div>
                <div class="field half"><label>Fem. Cap.</label><input type="number" id="editIsoCap" min="0"/></div>
            </div>
            <div class="field-row">
                <div class="field half"><label>Ped. Ocup.</label><input type="number" id="editIsoPedOcc" min="0"/></div>
                <div class="field half"><label>Ped. Cap.</label><input type="number" id="editIsoPedCap" min="0"/></div>
            </div>
        </div>

        <h3 class="section-title">👨‍⚕️ Especialistas</h3>
        <div class="field-row toggles">
            <label class="toggle"><input type="checkbox" id="editOrtho"/><span>🦴 Ortopedista</span></label>
            <label class="toggle"><input type="checkbox" id="editSurgeon"/><span>🔪 Cirurgião</span></label>
            <label class="toggle"><input type="checkbox" id="editPsych"/><span>🧠 Psiquiatra</span></label>
        </div>

        <div class="modal-actions">
            <button class="btn-cancel" onclick="closeEdit()">Cancelar</button>
            <button class="btn-save" onclick="saveEdit()">💾 Salvar</button>
        </div>
    </div>
</div>

<div class="confirm-overlay" id="confirmModal">
    <div class="confirm-box">
        <p id="confirmMsg"></p>
        <div class="confirm-actions">
            <button class="btn-cancel" onclick="closeConfirm()">Cancelar</button>
            <button class="btn-delete" onclick="execConfirm()">🗑️ Apagar</button>
        </div>
    </div>
</div>

<div class="toast" id="toast"></div>

<script>
const BASE = location.pathname.replace(/\/api\/admin\/?$/, '');
const API = BASE + '/api';
let knownUnits = [];
let allEvents = [];
let pendingConfirmId = null;

function tk() { return document.getElementById('tokenInput').value.trim(); }

function toast(msg, isError) {
    const t = document.getElementById('toast');
    t.textContent = msg;
    t.className = 'toast' + (isError ? ' error' : '');
    t.style.display = 'block';
    setTimeout(() => t.style.display = 'none', 3000);
}

async function api(method, path, body) {
    const url = API + path;
    const opts = { method, headers: {} };
    const t = tk();
    if (t) opts.headers['Authorization'] = 'Bearer ' + t;
    if (body) {
        opts.headers['Content-Type'] = 'application/json';
        opts.body = JSON.stringify(body);
    }
    const res = await fetch(url, opts);
    const data = await res.json();
    if (res.status === 401) {
        showLogin('Token inválido ou expirado.');
        throw new Error('Não autorizado');
    }
    if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`);
    return data;
}

function fmtDate(iso) {
    if (!iso) return '—';
    const d = new Date(iso);
    return d.toLocaleString('pt-BR', { timeZone: 'America/Bahia', day:'2-digit', month:'2-digit', hour:'2-digit', minute:'2-digit' });
}

function fmtRoom(o, c) {
    if (o == null && c == null) return '—';
    return `${String(o??0).padStart(2,'0')}/${String(c??0).padStart(2,'0')}`;
}

function renderEvents() {
    const container = document.getElementById('eventList');
    if (allEvents.length === 0) {
        container.innerHTML = '<div class="empty">Nenhum evento encontrado.</div>';
        return;
    }
    container.innerHTML = allEvents.map(ev => {
        const d = ev.payload?.data || {};
        const r = d.rooms || {};
        const sp = d.specialists || {};
        const crit = ev.is_critical;
        const badge = !ev.unit_code ? '<span class="badge badge-pending">pendente</span>'
            : crit ? '<span class="badge badge-critical">CRÍTICO</span>'
            : '<span class="badge badge-ok">ok</span>';
        return `
        <div class="card" data-id="${ev.id}">
            <div class="card-header">
                <div class="card-title">${ev.canonical_unit_name || ev.upa_name || ev.reported_upa_name || '???'} ${badge}</div>
                <div class="card-id">#${ev.id}</div>
            </div>
            <div class="card-meta">
                🕐 <b>${fmtDate(ev.received_at)}</b> · ${ev.source || '?'}<br/>
                🔴 ${fmtRoom(r.red_room?.occupied, r.red_room?.capacity)}
                🟡 ${(r.yellow_male || r.yellow_female)
                    ? 'M' + fmtRoom(r.yellow_male?.occupied, r.yellow_male?.capacity) + ' F' + fmtRoom(r.yellow_female?.occupied, r.yellow_female?.capacity)
                    : fmtRoom(r.yellow_room?.occupied, r.yellow_room?.capacity)}
                🟣 ${r.isolation_mode === 'split'
                    ? 'M' + fmtRoom(r.isolation_male?.occupied, r.isolation_male?.capacity) + ' F' + fmtRoom(r.isolation_female?.occupied, r.isolation_female?.capacity)
                    : fmtRoom(r.isolation_total?.occupied, r.isolation_total?.capacity)}
                · 🦴${sp.has_orthopedist?'✅':'❌'} 🔪${sp.has_surgeon?'✅':'❌'} 🧠${sp.has_psychiatrist?'✅':'❌'}
            </div>
            <div class="card-actions">
                <button class="btn-edit" onclick="openEdit(${ev.id})">✏️ Editar</button>
                <button class="btn-delete" onclick="confirmDelete(${ev.id})">🗑️ Apagar</button>
                <button class="btn-raw" onclick="toggleRaw(this)">📝 Bruto</button>
            </div>
            <div class="raw-text">${escHtml(d.raw_text || ev.raw_text || '(sem texto)')}</div>
        </div>`;
    }).join('');
}

function escHtml(s) {
    return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}

function toggleRaw(btn) {
    const raw = btn.closest('.card').querySelector('.raw-text');
    const show = raw.style.display === 'none' || !raw.style.display;
    raw.style.display = show ? 'block' : 'none';
    btn.textContent = show ? '🔽 Fechar' : '📝 Bruto';
}

async function loadEvents() {
    try {
        const limit = parseInt(document.getElementById('limitSel').value) || 30;
        const data = await api('GET', `/admin/events?limit=${limit}`);
        allEvents = data.events || [];
        renderEvents();

        // Carregar unidades para o select
        try {
            const uData = await api('GET', '/units');
            knownUnits = (uData.units || []).map(u => ({ code: u.code, name: u.canonical_name }));
        } catch(_) {}

        toast(`${allEvents.length} evento(s) carregado(s)`);
    } catch (e) {
        toast('Erro: ' + e.message, true);
    }
}

function loadMore() {
    const sel = document.getElementById('limitSel');
    sel.value = String(parseInt(sel.value) * 2);
    loadEvents();
}

async function openEdit(id) {
    try {
        const data = await api('GET', `/admin/events/${id}`);
        const ev = data.event;
        document.getElementById('editId').value = id;
        document.getElementById('editUpa').value = ev.upa_name || ev.canonical_unit_name || '';

        // Popular select de unit_code
        const sel = document.getElementById('editUnitCode');
        sel.innerHTML = '<option value="">— não alterar —</option>' +
            knownUnits.map(u => `<option value="${u.code}" ${u.code === ev.unit_code ? 'selected' : ''}>${u.name}</option>`).join('');

        // Preencher datetime-local
        if (ev.received_at) {
            const d = new Date(ev.received_at);
            const local = new Date(d.getTime() - d.getTimezoneOffset() * 60000);
            document.getElementById('editTime').value = local.toISOString().slice(0, 16);
        } else {
            document.getElementById('editTime').value = '';
        }

        // Ler dados do payload (fonte de verdade)
        const rooms = ev.payload?.data?.rooms || {};
        const specs = ev.payload?.data?.specialists || {};

        // Vermelha
        document.getElementById('editRedOcc').value = rooms.red_room?.occupied ?? '';
        document.getElementById('editRedCap').value = rooms.red_room?.capacity ?? '';

        // Amarela - detectar se é split (tem yellow_male)
        const yellowIsSplit = !!(rooms.yellow_male || rooms.yellow_female);
        document.getElementById('editYellowMode').value = yellowIsSplit ? 'split' : 'total';
        updateYellowVisibility(yellowIsSplit ? 'split' : 'total');
        document.getElementById('editYellowOcc').value = rooms.yellow_room?.occupied ?? '';
        document.getElementById('editYellowCap').value = rooms.yellow_room?.capacity ?? '';
        document.getElementById('editYellowMaleOcc').value = rooms.yellow_male?.occupied ?? '';
        document.getElementById('editYellowMaleCap').value = rooms.yellow_male?.capacity ?? '';
        document.getElementById('editYellowFemOcc').value = rooms.yellow_female?.occupied ?? '';
        document.getElementById('editYellowFemCap').value = rooms.yellow_female?.capacity ?? '';

        // Isolamento
        const isoMode = rooms.isolation_mode || ev.isolation_mode || '';
        const isoSel = document.getElementById('editIsoMode');
        isoSel.value = isoMode === 'split' ? 'split' : (isoMode ? 'total' : '');
        updateIsoVisibility(isoSel.value);
        document.getElementById('editIsoTotalOcc').value = rooms.isolation_total?.occupied ?? '';
        document.getElementById('editIsoTotalCap').value = rooms.isolation_total?.capacity ?? '';
        document.getElementById('editIsoMaleOcc').value = rooms.isolation_male?.occupied ?? '';
        document.getElementById('editIsoMaleCap').value = rooms.isolation_male?.capacity ?? '';
        document.getElementById('editIsoFemOcc').value = rooms.isolation_female?.occupied ?? '';
        document.getElementById('editIsoCap').value = rooms.isolation_female?.capacity ?? '';
        document.getElementById('editIsoPedOcc').value = rooms.isolation_pediatric?.occupied ?? '';
        document.getElementById('editIsoPedCap').value = rooms.isolation_pediatric?.capacity ?? '';

        // Especialistas
        document.getElementById('editOrtho').checked = !!specs.has_orthopedist;
        document.getElementById('editSurgeon').checked = !!specs.has_surgeon;
        document.getElementById('editPsych').checked = !!specs.has_psychiatrist;

        document.getElementById('editModal').classList.add('open');
    } catch (e) {
        toast('Erro ao carregar: ' + e.message, true);
    }
}

function updateYellowVisibility(mode) {
    document.getElementById('yellowTotalFields').style.display = (mode === 'split') ? 'none' : '';
    document.getElementById('yellowSplitFields').style.display = (mode === 'split') ? '' : 'none';
}

function updateIsoVisibility(mode) {
    document.getElementById('isoTotalFields').style.display = (mode === 'split') ? 'none' : '';
    document.getElementById('isoSplitFields').style.display = (mode === 'split') ? '' : 'none';
}

function closeEdit() {
    document.getElementById('editModal').classList.remove('open');
}

async function saveEdit() {
    const id = document.getElementById('editId').value;
    const body = {};
    const upa = document.getElementById('editUpa').value.trim();
    const code = document.getElementById('editUnitCode').value;
    const time = document.getElementById('editTime').value;
    if (upa) body.upa_name = upa;
    if (code) body.unit_code = code;
    if (time) body.reported_at = new Date(time).toISOString();

    // Quartos - enviar somente se preenchidos
    function numOrNull(id) { const v = document.getElementById(id).value; return v !== '' ? parseInt(v) : null; }
    // Amarela — se split, enviar male/female e recalcular total
    const yellowMode = document.getElementById('editYellowMode').value;
    if (yellowMode === 'split') {
        const mO = numOrNull('editYellowMaleOcc'), mC = numOrNull('editYellowMaleCap');
        const fO = numOrNull('editYellowFemOcc'), fC = numOrNull('editYellowFemCap');
        if (mO !== null) body.yellow_male_occupied = mO;
        if (mC !== null) body.yellow_male_capacity = mC;
        if (fO !== null) body.yellow_female_occupied = fO;
        if (fC !== null) body.yellow_female_capacity = fC;
        // Total = soma
        body.yellow_occupied = (mO ?? 0) + (fO ?? 0);
        body.yellow_capacity = (mC ?? 0) + (fC ?? 0);
    } else {
        const yO = numOrNull('editYellowOcc'), yC = numOrNull('editYellowCap');
        if (yO !== null) body.yellow_occupied = yO;
        if (yC !== null) body.yellow_capacity = yC;
    }

    const roomFields = [
        ['editRedOcc','red_occupied'],['editRedCap','red_capacity'],
        ['editIsoTotalOcc','isolation_total_occupied'],['editIsoTotalCap','isolation_total_capacity'],
        ['editIsoMaleOcc','isolation_male_occupied'],['editIsoMaleCap','isolation_male_capacity'],
        ['editIsoFemOcc','isolation_female_occupied'],['editIsoCap','isolation_female_capacity'],
        ['editIsoPedOcc','isolation_pediatric_occupied'],['editIsoPedCap','isolation_pediatric_capacity'],
    ];
    roomFields.forEach(([elId, key]) => {
        const v = numOrNull(elId);
        if (v !== null) body[key] = v;
    });

    const isoMode = document.getElementById('editIsoMode').value;
    if (isoMode) body.isolation_mode = isoMode;

    // Especialistas - sempre enviar
    body.has_orthopedist = document.getElementById('editOrtho').checked;
    body.has_surgeon = document.getElementById('editSurgeon').checked;
    body.has_psychiatrist = document.getElementById('editPsych').checked;

    if (Object.keys(body).length === 0) {
        toast('Nada para alterar', true);
        return;
    }

    try {
        await api('PATCH', `/admin/events/${id}`, body);
        closeEdit();
        toast('✅ Evento atualizado!');
        loadEvents();
    } catch (e) {
        toast('Erro: ' + e.message, true);
    }
}

function confirmDelete(id) {
    pendingConfirmId = id;
    document.getElementById('confirmMsg').textContent = `Apagar evento #${id}? O dashboard voltará ao giro anterior desta UPA.`;
    document.getElementById('confirmModal').classList.add('open');
}

function closeConfirm() {
    pendingConfirmId = null;
    document.getElementById('confirmModal').classList.remove('open');
}

async function execConfirm() {
    if (!pendingConfirmId) return;
    const id = pendingConfirmId;
    closeConfirm();
    try {
        await api('DELETE', `/admin/events/${id}`);
        toast('🗑️ Evento apagado!');
        loadEvents();
    } catch (e) {
        toast('Erro: ' + e.message, true);
    }
}

// ── Login gate ──
function showLogin(msg) {
    document.getElementById('appMain').style.display = 'none';
    document.getElementById('loginScreen').style.display = 'flex';
    document.getElementById('loginError').textContent = msg || '';
}
function showApp() {
    document.getElementById('loginScreen').style.display = 'none';
    document.getElementById('appMain').style.display = 'block';
    loadEvents();
}
async function doLogin() {
    const user = document.getElementById('loginUser').value.trim();
    const pass = document.getElementById('loginPass').value.trim();
    if (!user || !pass) return;
    try {
        const res = await fetch(API + '/admin/auth', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({username: user, password: pass}),
        });
        const data = await res.json();
        if (!res.ok) throw new Error(data.detail || 'Erro');
        const token = data.token;
        document.getElementById('tokenInput').value = token;
        localStorage.setItem('giro_admin_token', token);
        showApp();
    } catch(e) {
        showLogin('❌ ' + (e.message || 'Credenciais incorretas.'));
    }
}
async function doLogout() {
    localStorage.removeItem('giro_admin_token');
    document.getElementById('tokenInput').value = '';
    showLogin('');
}

window.addEventListener('DOMContentLoaded', async () => {
    const saved = localStorage.getItem('giro_admin_token');
    if (saved) {
        document.getElementById('tokenInput').value = saved;
        try {
            // Testar se o token salvo ainda é válido fazendo uma chamada autenticada
            await api('GET', '/admin/events?limit=1');
            showApp();
        } catch(_) {
            showLogin('Sessão expirada. Faça login novamente.');
        }
    } else {
        showLogin('');
    }
    // Enter no campo de senha faz login
    document.getElementById('loginPass').addEventListener('keydown', e => {
        if (e.key === 'Enter') doLogin();
    });
    // Listeners dos selects de modo
    document.getElementById('editYellowMode').addEventListener('change', e => {
        updateYellowVisibility(e.target.value);
    });
    document.getElementById('editIsoMode').addEventListener('change', e => {
        updateIsoVisibility(e.target.value);
    });
});
</script>
</body>
</html>
"""


@app.websocket("/ws/dashboard")
async def dashboard_ws(websocket: WebSocket) -> None:
    await manager.connect(websocket)

    if app.state.last_dashboard_event is not None:
        await websocket.send_json(app.state.last_dashboard_event)

    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(websocket)
    except Exception:
        manager.disconnect(websocket)
