"""Alerta curto de UPA sem giro, entregue no WhatsApp do gestor.

Canal ADICIONAL ao watcher de Telegram do admin (``STALE_ALERT_HOURS``, 10h
por padrão). O Telegram é onde o admin acompanha o dia a dia; o WhatsApp do
gestor é onde só entra o que já passou do ponto de virar cobrança. Misturar os
dois transformaria o canal em spam e ele seria silenciado — que é a única
falha irreversível deste código.

Três travas, nesta ordem:
1. limiar POR TURNO, o mesmo SLA do /cobranca (``reports.GIRO_SLA``): diurno
   (07h–19h) viola acima de 6h, noturno (19h–07h) acima de 12h. Um número só
   não servia: 8h de silêncio às 10h da manhã é grave, 8h de madrugada é o
   combinado. ``WHATSAPP_ALERT_HOURS`` continua existindo como OVERRIDE
   opcional (limiar fixo) — vazio/ausente = SLA por turno;
2. cooldown por unidade (``WHATSAPP_ALERT_COOLDOWN_HOURS``, default 6h),
   com estado persistido em ``whatsapp_alert_state``;
3. nasce DESLIGADO — sem ``WHATSAPP_ALERT_ENABLED=true`` e sem
   ``WHATSAPP_ALERT_TO`` nada sai: o módulo apenas loga o que enviaria.

O envio usa o gateway whatsmeow já em produção
(``aldinokemal2104/go-whatsapp-web-multidevice``): ``POST /send/message`` com
JSON ``{"phone": ..., "message": ...}`` e HTTP basic auth. O campo opcional
``mentions`` (array de números só com dígitos, openapi v9) faz a menção
fantasma de quem posta o giro da unidade — sem precisar de ``@`` no texto.
Nenhuma exceção escapa de :func:`dispatch_stale_alert` — o watcher e o webhook
não podem cair porque o WhatsApp está fora do ar.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import re
from datetime import datetime, timedelta
from typing import Any, Iterable
from urllib import request

from reports import as_utc, classify_gap

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


def _env_float_optional(name: str) -> float | None:
    """Valor só quando o operador escreveu um — vazio/ausente devolve None."""
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return None
    try:
        return float(raw)
    except ValueError:
        logger.warning("%s inválido (%r); ignorando o override", name, raw)
        return None


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
# Override opcional: limiar FIXO em horas, no lugar do SLA por turno. Existe
# para o dono conseguir apertar/afrouxar o canal sem deploy — mas configurá-lo
# desliga a regra de turno, então o default é vazio.
WHATSAPP_ALERT_HOURS_OVERRIDE = _env_float_optional("WHATSAPP_ALERT_HOURS")
# Compatibilidade: valor de referência do limiar fixo (12h) para quem só quer
# um número. O caminho de decisão usa WHATSAPP_ALERT_HOURS_OVERRIDE.
WHATSAPP_ALERT_HOURS = _env_float("WHATSAPP_ALERT_HOURS", 12.0)
# Janela mínima entre dois avisos sobre a MESMA unidade.
WHATSAPP_ALERT_COOLDOWN_HOURS = _env_float("WHATSAPP_ALERT_COOLDOWN_HOURS", 6.0)

GATEWAY_TIMEOUT_SECONDS = 10

if WHATSAPP_ALERT_HOURS_OVERRIDE is not None:
    logger.warning(
        "WHATSAPP_ALERT_HOURS=%s está ATIVO: o alerta usa limiar fixo e ignora o "
        "SLA por turno (diurno 6h / noturno 12h). Apague a variável para voltar ao SLA.",
        WHATSAPP_ALERT_HOURS_OVERRIDE,
    )


def is_enabled() -> bool:
    """True quando há autorização e destino. Falso = dry-run."""
    return bool(WHATSAPP_ALERT_ENABLED and WHATSAPP_ALERT_TO)


# ---------------------------------------------------------------------------
# Telefone / menção
# ---------------------------------------------------------------------------

_NON_DIGITS = re.compile(r"\D+")


def normalize_phone(value: Any) -> str:
    """Só os dígitos do número, no formato que o gateway aceita em `mentions`.

    Aceita o que o WhatsApp entrega em qualquer variante — JID completo
    (``5571988887777@s.whatsapp.net``), JID de dispositivo
    (``5571988887777:12@s.whatsapp.net``), LID, número formatado
    (``+55 (71) 98888-7777``) — e devolve ``5571988887777``. Só o que vem
    ANTES do ``@`` conta: depois da arroba vem o servidor, não o telefone.

    Devolve string vazia quando não sobra número utilizável — quem chama trata
    isso como "sem contato" e cai no coordenador.
    """
    if value is None:
        return ""
    text = str(value).strip()
    if not text:
        return ""
    local_part = text.split("@", 1)[0]
    local_part = local_part.split(":", 1)[0]
    digits = _NON_DIGITS.sub("", local_part)
    # Fora de 8–15 dígitos não é telefone: abaixo é ruído do payload, acima é
    # id de grupo antigo ("557181082189-1462561641") que perdeu o sufixo
    # @g.us pelo caminho — mencionar isso não notificaria ninguém. 15 é o teto
    # do E.164; DDI+DDD+número no Brasil tem 12–13.
    return digits if 8 <= len(digits) <= 15 else ""


def select_stale_offenders(
    status_rows: list[dict[str, Any]],
    now: datetime,
    *,
    threshold_hours: float | None = None,
    last_sent_by_unit: dict[str, datetime] | None = None,
    cooldown_hours: float | None = None,
) -> list[dict[str, Any]]:
    """Unidades que estouraram o SLA do turno e estão fora do cooldown.

    Função pura: recebe as linhas de ``db.get_latest_status_by_unit`` e o
    estado já lido, devolve os candidatos ordenados da mais parada para a
    menos. Quem decide gravar estado é quem envia.

    O limite de cada unidade sai de ``reports.classify_gap`` — a MESMA função
    que o /cobranca usa para julgar violação, inclusive a regra de fronteira
    (silêncio que vira o turno é julgado pelo turno onde passou mais tempo).
    Duplicar a regra aqui significaria o alerta e a cobrança discordando sobre
    quem está devendo, e o gestor perderia a confiança nos dois.

    `threshold_hours` (ou ``WHATSAPP_ALERT_HOURS``) força limiar fixo, sem
    turno — é o modo de escape, não o padrão.
    """
    threshold = WHATSAPP_ALERT_HOURS_OVERRIDE if threshold_hours is None else threshold_hours
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

        gap = classify_gap(moment, now, ongoing=True)
        age_hours = gap["hours"]
        if threshold is None:
            if not gap["violated"]:
                continue
            limit_hours = gap["limit_hours"]
        else:
            if age_hours < threshold:
                continue
            limit_hours = float(threshold)

        previous = as_utc(last_sent.get(unit_key))
        if previous is not None and now - previous < timedelta(hours=cooldown):
            continue

        offenders.append(
            {
                "unit_key": unit_key,
                "unit_code": row.get("unit_code"),
                "unit_name": row.get("displayed_name") or row.get("canonical_name") or unit_key,
                "age_hours": age_hours,
                "shift": gap["shift"],
                "limit_hours": limit_hours,
            }
        )

    return sorted(offenders, key=lambda item: item["age_hours"], reverse=True)


def send_gateway_message(
    phone: str,
    message: str,
    *,
    mentions: Iterable[str] | None = None,
    url: str | None = None,
    auth: str | None = None,
    timeout: int = GATEWAY_TIMEOUT_SECONDS,
) -> None:
    """POST /send/message no gateway. Levanta exceção se o envio falhar.

    `mentions` é a menção fantasma do openapi v9: array de números só com
    dígitos. O campo só entra no corpo quando há alguém para mencionar — um
    ``"mentions": []`` num gateway de versão antiga é convite a 400.
    """
    base_url = (WHATSAPP_GW_URL if url is None else url).rstrip("/")
    credentials = WHATSAPP_GW_AUTH if auth is None else auth

    headers = {"Content-Type": "application/json"}
    if credentials:
        token = base64.b64encode(credentials.encode("utf-8")).decode("ascii")
        headers["Authorization"] = f"Basic {token}"

    body: dict[str, Any] = {"phone": phone, "message": message}
    cleaned = [digits for digits in (normalize_phone(item) for item in (mentions or [])) if digits]
    if cleaned:
        body["mentions"] = list(dict.fromkeys(cleaned))

    req = request.Request(
        url=f"{base_url}/send/message",
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    with request.urlopen(req, timeout=timeout) as response:
        response.read()


def dispatch_stale_alert(message: str, mentions: Iterable[str] | None = None) -> bool:
    """Entrega o aviso. Devolve True apenas quando o gateway aceitou.

    Nunca levanta: desligado, destino vazio ou gateway fora do ar são todos
    "não enviou" — e quem chama trata os três do mesmo jeito (não grava
    cooldown, tenta de novo na próxima varredura).
    """
    if not message.strip():
        return False

    mention_list = [digits for digits in (normalize_phone(item) for item in (mentions or [])) if digits]
    mention_list = list(dict.fromkeys(mention_list))

    if not is_enabled():
        motivo = "destino não configurado" if WHATSAPP_ALERT_ENABLED else "canal desligado"
        logger.info(
            "[dry-run] alerta WhatsApp NÃO enviado (%s). Enviaria para %r mencionando %s:\n%s",
            motivo,
            WHATSAPP_ALERT_TO or "(vazio)",
            mention_list or "(ninguém)",
            message,
        )
        return False

    try:
        send_gateway_message(WHATSAPP_ALERT_TO, message, mentions=mention_list)
    except Exception as exc:  # noqa: BLE001 - o watcher não pode cair por isto
        logger.warning("Falha ao enviar alerta WhatsApp pelo gateway: %s", exc)
        return False

    logger.info(
        "Alerta WhatsApp enviado para %s (%d caracteres, %d menção(ões))",
        WHATSAPP_ALERT_TO,
        len(message),
        len(mention_list),
    )
    return True
