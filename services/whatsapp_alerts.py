"""Alerta curto de UPA sem giro, entregue no WhatsApp do gestor.

Canal ADICIONAL ao watcher de Telegram do admin (``STALE_ALERT_HOURS``, 10h
por padrão). Aqui o limiar é PRÓPRIO e mais alto: o Telegram é onde o admin
acompanha o dia a dia, o WhatsApp do gestor é onde só entra o que já passou
do ponto de virar cobrança. Misturar os dois limiares transformaria o canal em
spam e ele seria silenciado — que é a única falha irreversível deste código.

Três travas, nesta ordem:
1. limiar alto (``WHATSAPP_ALERT_HOURS``, default 12h);
2. cooldown por unidade (``WHATSAPP_ALERT_COOLDOWN_HOURS``, default 6h),
   com estado persistido em ``whatsapp_alert_state``;
3. nasce DESLIGADO — sem ``WHATSAPP_ALERT_ENABLED=true`` e sem
   ``WHATSAPP_ALERT_TO`` nada sai: o módulo apenas loga o que enviaria.

O envio usa o gateway whatsmeow já em produção
(``aldinokemal2104/go-whatsapp-web-multidevice``): ``POST /send/message`` com
JSON ``{"phone": ..., "message": ...}`` e HTTP basic auth. Nenhuma exceção
escapa de :func:`dispatch_stale_alert` — o watcher e o webhook não podem cair
porque o WhatsApp está fora do ar.
"""

from __future__ import annotations

import base64
import json
import logging
import os
from datetime import datetime, timedelta
from typing import Any
from urllib import request

from reports import as_utc

logger = logging.getLogger(__name__)


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().casefold() in {"1", "true", "yes", "y", "on", "sim"}


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, "").strip() or default)
    except ValueError:
        logger.warning("%s inválido; usando %s", name, default)
        return default


# Interruptor geral. Default false de propósito: o canal só liga quando o dono
# decidir, nunca por um deploy.
WHATSAPP_ALERT_ENABLED = _env_bool("WHATSAPP_ALERT_ENABLED", False)
# Destino único (número com DDI, ex.: 5571999999999, ou JID completo).
WHATSAPP_ALERT_TO = os.getenv("WHATSAPP_ALERT_TO", "").strip()
# URL do gateway. O default é o endereço visto DO HOST; a API roda em
# container na rede giro-de-leitos_default e o whatsmeow-gw publica só em
# 127.0.0.1:3080 do host — em produção configure
# WHATSAPP_GW_URL=http://host.docker.internal:3080 (o container da API já sobe
# com --add-host=host.docker.internal:host-gateway).
WHATSAPP_GW_URL = os.getenv("WHATSAPP_GW_URL", "http://127.0.0.1:3080").strip().rstrip("/")
# Basic auth do gateway, no formato "usuario:segredo" (env APP_BASIC_AUTH do
# container whatsmeow-gw).
WHATSAPP_GW_AUTH = os.getenv("WHATSAPP_GW_AUTH", "").strip()
# Limiar próprio, separado do STALE_ALERT_HOURS do Telegram.
WHATSAPP_ALERT_HOURS = _env_float("WHATSAPP_ALERT_HOURS", 12.0)
# Janela mínima entre dois avisos sobre a MESMA unidade.
WHATSAPP_ALERT_COOLDOWN_HOURS = _env_float("WHATSAPP_ALERT_COOLDOWN_HOURS", 6.0)

GATEWAY_TIMEOUT_SECONDS = 10


def is_enabled() -> bool:
    """True quando há autorização e destino. Falso = dry-run."""
    return bool(WHATSAPP_ALERT_ENABLED and WHATSAPP_ALERT_TO)


def select_stale_offenders(
    status_rows: list[dict[str, Any]],
    now: datetime,
    *,
    threshold_hours: float | None = None,
    last_sent_by_unit: dict[str, datetime] | None = None,
    cooldown_hours: float | None = None,
) -> list[dict[str, Any]]:
    """Unidades que passaram do limiar e estão fora do cooldown.

    Função pura: recebe as linhas de ``db.get_latest_status_by_unit`` e o
    estado já lido, devolve os candidatos ordenados da mais parada para a
    menos. Quem decide gravar estado é quem envia.
    """
    threshold = WHATSAPP_ALERT_HOURS if threshold_hours is None else threshold_hours
    cooldown = WHATSAPP_ALERT_COOLDOWN_HOURS if cooldown_hours is None else cooldown_hours
    last_sent = last_sent_by_unit or {}

    offenders: list[dict[str, Any]] = []
    for row in status_rows:
        unit_key = row.get("unit_key")
        moment = as_utc(row.get("updated_at") or row.get("received_at"))
        # Sem carimbo não dá para afirmar "há X horas sem giro" — e este canal
        # não comporta um aviso que o gestor não consiga conferir.
        if not unit_key or moment is None:
            continue

        age_hours = (now - moment).total_seconds() / 3600
        if age_hours < threshold:
            continue

        previous = as_utc(last_sent.get(unit_key))
        if previous is not None and now - previous < timedelta(hours=cooldown):
            continue

        offenders.append(
            {
                "unit_key": unit_key,
                "unit_code": row.get("unit_code"),
                "unit_name": row.get("displayed_name") or row.get("canonical_name") or unit_key,
                "age_hours": age_hours,
            }
        )

    return sorted(offenders, key=lambda item: item["age_hours"], reverse=True)


def send_gateway_message(
    phone: str,
    message: str,
    *,
    url: str | None = None,
    auth: str | None = None,
    timeout: int = GATEWAY_TIMEOUT_SECONDS,
) -> None:
    """POST /send/message no gateway. Levanta exceção se o envio falhar."""
    base_url = (WHATSAPP_GW_URL if url is None else url).rstrip("/")
    credentials = WHATSAPP_GW_AUTH if auth is None else auth

    headers = {"Content-Type": "application/json"}
    if credentials:
        token = base64.b64encode(credentials.encode("utf-8")).decode("ascii")
        headers["Authorization"] = f"Basic {token}"

    req = request.Request(
        url=f"{base_url}/send/message",
        data=json.dumps({"phone": phone, "message": message}, ensure_ascii=False).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    with request.urlopen(req, timeout=timeout) as response:
        response.read()


def dispatch_stale_alert(message: str) -> bool:
    """Entrega o aviso. Devolve True apenas quando o gateway aceitou.

    Nunca levanta: desligado, destino vazio ou gateway fora do ar são todos
    "não enviou" — e quem chama trata os três do mesmo jeito (não grava
    cooldown, tenta de novo na próxima varredura).
    """
    if not message.strip():
        return False

    if not is_enabled():
        motivo = "destino não configurado" if WHATSAPP_ALERT_ENABLED else "canal desligado"
        logger.info(
            "[dry-run] alerta WhatsApp NÃO enviado (%s). Enviaria para %r:\n%s",
            motivo,
            WHATSAPP_ALERT_TO or "(vazio)",
            message,
        )
        return False

    try:
        send_gateway_message(WHATSAPP_ALERT_TO, message)
    except Exception as exc:  # noqa: BLE001 - o watcher não pode cair por isto
        logger.warning("Falha ao enviar alerta WhatsApp pelo gateway: %s", exc)
        return False

    logger.info("Alerta WhatsApp enviado para %s (%d caracteres)", WHATSAPP_ALERT_TO, len(message))
    return True
