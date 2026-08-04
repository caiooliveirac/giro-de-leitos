"""Restrição de UPA anunciada no grupo do WhatsApp das unidades.

Quem DECIDE a restrição é a chefia, no painel `/tabela` (repo `tabela`), e
quem já é avisado disso é o grupo dos reguladores, no Telegram — imediatamente
e a cada 2h. Este módulo fecha o outro lado do circuito: o grupo do WhatsApp
onde as UPAs postam o giro, que até aqui descobria o redirecionamento pelo
telefone, depois do paciente já ter sido recusado.

Três diferenças deliberadas em relação ao aviso do Telegram:

1. **Cadência de 4h, não de 2h.** O grupo dos reguladores é ferramenta de
   trabalho de quem regula e aguenta a repetição; o grupo das UPAs é onde o
   giro é postado, e um lembrete de 2 em 2 horas ali vira ruído — grupo
   silenciado é a única falha irreversível deste código. As janelas continuam
   ancoradas em 07:00/19:00 para cair na virada de turno.
2. **Tom de comunicado, não de ordem.** O regulador lê "não encaminhe"; a UPA
   precisa ler que a demanda dela está sendo redirecionada A PEDIDO da
   Coordenação de Unidades Fixas — a decisão não é do SAMU nem do plantão.
3. **Sem PIN, sem escrita.** Aqui só se LÊ `GET /tabela/api/upas/restrictions`
   (público, o mesmo contrato que o bot Plantões SAMU consome). O `tabela`
   continua sendo o dono do dado; este serviço é mais um leitor.

A detecção de mudança é por comparação de snapshot, não por webhook: o
`tabela` não conhece este serviço e não deve passar a conhecer. A cada
`WHATSAPP_RESTRICTION_POLL_MINUTES` (2 por padrão) a lista vigente é comparada
com a última que o grupo viu, e a diferença vira aviso — atraso máximo de um
poll, sem acoplar os dois repositórios.

Travas, na mesma ordem de `whatsapp_alerts`:

1. nasce DESLIGADO — sem ``WHATSAPP_RESTRICTION_ENABLED=true`` e sem
   ``WHATSAPP_GROUP_TO`` nada sai, o módulo só loga o que enviaria;
2. API fora do ar NUNCA vira "liberou geral": falha de leitura devolve None e
   o tick inteiro é pulado (ver :func:`fetch_restrictions`);
3. o snapshot só avança para o que o gateway ACEITOU, item por item — aviso
   que não saiu é retentado no próximo tick em vez de virar silêncio.

O envio reaproveita o gateway whatsmeow de ``services.whatsapp_alerts``: o
campo ``phone`` do ``POST /send/message`` aceita o JID do grupo
(``120363XXXXXXXXX@g.us``) do mesmo jeito que aceita um número.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from typing import Any
from urllib import request

from reports import REPORT_TZ, as_utc, fmt_local
from services.whatsapp_alerts import (
    WHATSAPP_GROUP_TO,
    _env_bool,
    _env_float,
    send_gateway_message,
)

logger = logging.getLogger(__name__)


# Interruptor geral. Default false pelo mesmo motivo do alerta de silêncio: o
# canal liga quando o dono decidir, nunca por um deploy.
WHATSAPP_RESTRICTION_ENABLED = _env_bool("WHATSAPP_RESTRICTION_ENABLED", False)
# O destino é o `WHATSAPP_GROUP_TO` de `whatsapp_alerts`: o JID do grupo das
# UPAs (120363XXXXXXXXX@g.us), o mesmo grupo de onde o giro é ingerido
# (descoberto com GIRO_GROUP_JID no adapter).
# Contrato público do `tabela`. Trocável para apontar ao LAB sem deploy.
TABELA_RESTRICTIONS_URL = os.getenv(
    "TABELA_RESTRICTIONS_URL", "https://mnrs.com.br/tabela/api/upas/restrictions"
).strip()
# Espaçamento do lembrete, em horas, ancorado em 07:00/19:00.
WHATSAPP_RESTRICTION_DIGEST_HOURS = _env_float("WHATSAPP_RESTRICTION_DIGEST_HOURS", 4.0)
# De quanto em quanto tempo a lista é relida — o teto do atraso de uma mudança.
WHATSAPP_RESTRICTION_POLL_MINUTES = _env_float("WHATSAPP_RESTRICTION_POLL_MINUTES", 2.0)

FETCH_TIMEOUT_SECONDS = 10
# Virada de turno da regulação: é dela que as janelas do lembrete descendem.
DIGEST_ANCHOR_HOUR = 7
# Se a API estava fora às 19:00 e voltou 19:07, o lembrete da virada ainda sai.
# Depois disso a janela é considerada perdida — lembrete de 4h que chega 40min
# atrasado só confunde quem lê.
SLOT_GRACE_MINUTES = 15
# Teto de linhas do lembrete. Restrição em massa é notícia de outra natureza e
# não cabe num balão de WhatsApp.
MAX_DIGEST_UNITS = 10

ASSINATURA = "Giro de Leitos · aviso automático"
COORDENACAO = "Coordenação de Unidades Fixas"


def is_enabled() -> bool:
    """True quando há autorização e grupo. Falso = dry-run."""
    return bool(WHATSAPP_RESTRICTION_ENABLED and WHATSAPP_GROUP_TO)


# ---------------------------------------------------------------------------
# Leitura do contrato público do `tabela`
# ---------------------------------------------------------------------------


def fetch_restrictions(
    url: str | None = None, *, timeout: int = FETCH_TIMEOUT_SECONDS
) -> list[dict[str, Any]] | None:
    """As restrições vigentes agora, ou None quando não deu para saber.

    O None não é preciosismo: `[]` significa "nenhuma UPA restrita" e dispara
    os avisos de liberação. Se um timeout ou um 502 virasse `[]`, uma
    instabilidade de rede anunciaria ao grupo que todas as unidades voltaram a
    receber — exatamente a mensagem errada, no canal errado, e sem como
    desfazer. Falhou, o tick inteiro é pulado e o grupo não ouve nada.
    """
    endpoint = (url or TABELA_RESTRICTIONS_URL).strip()
    if not endpoint:
        logger.warning("TABELA_RESTRICTIONS_URL vazia — aviso de restrição sem fonte")
        return None

    try:
        req = request.Request(endpoint, headers={"Accept": "application/json"}, method="GET")
        with request.urlopen(req, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except Exception as exc:  # noqa: BLE001 - qualquer falha de leitura é "não sei"
        logger.warning("Não foi possível ler as restrições em %s: %s", endpoint, exc)
        return None

    if not isinstance(payload, dict) or payload.get("ok") is False:
        logger.warning("Resposta inesperada de %s: %r", endpoint, payload)
        return None

    rows = payload.get("restrictions")
    if not isinstance(rows, list):
        logger.warning("Resposta sem lista de restrições em %s", endpoint)
        return None

    # A API do `tabela` devolve camelCase (unitName, untilLabel) — ler
    # snake_case aqui foi o bug histórico do notifier, e é o mesmo contrato.
    clean: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        identifier = row.get("id")
        name = (row.get("unitName") or row.get("unitKey") or "").strip()
        if identifier is None or not name:
            continue
        clean.append(
            {
                "id": str(identifier),
                "unit_key": row.get("unitKey"),
                "unit_name": name,
                "until": row.get("until"),
                "until_label": (row.get("untilLabel") or "").strip(),
                "autor": (row.get("autor") or "").strip(),
            }
        )
    return clean


# ---------------------------------------------------------------------------
# Diferença entre o que o grupo viu e o que está valendo
# ---------------------------------------------------------------------------


def snapshot_from(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """A lista vigente no formato em que o snapshot é guardado (id → dado)."""
    return {row["id"]: dict(row) for row in rows}


def diff_restrictions(
    previous: dict[str, Any],
    current: list[dict[str, Any]],
    now: datetime,
) -> list[dict[str, Any]]:
    """O que mudou desde o último aviso, como lista de eventos.

    Quatro tipos, e a distinção entre os dois últimos importa para o tom:

    - ``created``   — unidade que não estava restrita e passou a estar;
    - ``renewed``   — mesma restrição, prazo diferente (esticado ou encurtado);
    - ``expired``   — sumiu da lista e o prazo que o grupo conhecia já tinha
      passado: venceu sozinha, ninguém decidiu nada agora;
    - ``liberated`` — sumiu da lista ANTES do prazo: a coordenação liberou.

    Sem essa separação o grupo receberia "a coordenação liberou" toda vez que
    um prazo simplesmente venceu, e a mensagem perderia o sentido de decisão.
    """
    events: list[dict[str, Any]] = []
    current_by_id = snapshot_from(current)

    for identifier, row in current_by_id.items():
        seen = previous.get(identifier)
        if not isinstance(seen, dict):
            events.append({"kind": "created", "row": row})
        elif (seen.get("until") or "") != (row.get("until") or ""):
            events.append({"kind": "renewed", "row": row})

    for identifier, seen in previous.items():
        if identifier in current_by_id or not isinstance(seen, dict):
            continue
        deadline = as_utc(seen.get("until"))
        kind = "expired" if deadline is not None and deadline <= now else "liberated"
        events.append({"kind": kind, "row": seen})

    return events


# ---------------------------------------------------------------------------
# Janela do lembrete
# ---------------------------------------------------------------------------


def digest_hours(interval_hours: float | None = None) -> list[int]:
    """As horas locais em que o lembrete sai, ancoradas na virada de turno.

    4h a partir das 07:00 dá 07, 11, 15, 19, 23 e 03 — duas das janelas caem
    exatamente na troca de plantão, que é quando entra gente que não viu nada
    do turno anterior.
    """
    interval = int(round(interval_hours if interval_hours is not None else WHATSAPP_RESTRICTION_DIGEST_HOURS))
    if interval < 1:
        interval = 1
    if interval > 24:
        interval = 24
    return sorted({(DIGEST_ANCHOR_HOUR + step) % 24 for step in range(0, 24, interval)})


def resolve_digest_slot(now: datetime, interval_hours: float | None = None) -> str | None:
    """Chave da janela que este instante deve disparar, ou None fora dela.

    Determinística de propósito: a mesma hora sempre devolve a mesma chave, e
    é ela que a trava de idempotência reserva no banco.
    """
    local = now.astimezone(REPORT_TZ)
    if local.hour not in digest_hours(interval_hours):
        return None
    if local.minute > SLOT_GRACE_MINUTES:
        return None
    return f"digest:{local.strftime('%Y-%m-%d')}T{local.hour:02d}"


# ---------------------------------------------------------------------------
# Textos — WhatsApp puro, negrito com *asterisco* (não entende HTML)
# ---------------------------------------------------------------------------


def _deadline(row: dict[str, Any]) -> str:
    """O prazo como o painel escreve ('hoje 19:00').

    O rótulo vem pronto da API justamente para o painel, o Telegram e o
    WhatsApp falarem a MESMA frase. Só se ele faltar é que o horário é
    formatado aqui.
    """
    label = (row.get("until_label") or "").strip()
    return label or fmt_local(row.get("until"))


def build_restriction_change_text(kind: str, row: dict[str, Any]) -> str:
    """O comunicado de uma mudança. String vazia = nada a dizer."""
    name = (row.get("unit_name") or "").strip()
    if not name:
        return ""
    prazo = _deadline(row)

    if kind == "created":
        corpo = [
            f"🚫 *{name} — demanda redirecionada*",
            "",
            f"A pedido da {COORDENACAO}, esta unidade não receberá "
            f"encaminhamentos até *{prazo}*.",
            "As demandas serão redirecionadas para as demais unidades nesse período.",
        ]
    elif kind == "renewed":
        corpo = [
            f"🚫 *{name} — prazo atualizado*",
            "",
            f"O redirecionamento das demandas desta unidade segue até *{prazo}*, "
            f"a pedido da {COORDENACAO}.",
        ]
    elif kind == "liberated":
        corpo = [
            f"✅ *{name} — voltou a receber*",
            "",
            f"A {COORDENACAO} encerrou o redirecionamento antes do prazo. "
            "Os encaminhamentos para esta unidade voltam ao normal.",
        ]
    elif kind == "expired":
        corpo = [
            f"✅ *{name} — voltou a receber*",
            "",
            f"O prazo do redirecionamento venceu ({prazo}). "
            "Os encaminhamentos para esta unidade voltam ao normal.",
        ]
    else:
        return ""

    return "\n".join([*corpo, "", ASSINATURA])


def build_restrictions_digest(rows: list[dict[str, Any]], now: datetime) -> str:
    """O lembrete de 4h. String vazia quando não há nada restrito.

    Nada restrito = grupo em silêncio. Um "nenhuma unidade restrita" de 4 em 4
    horas seria seis mensagens por dia dizendo que está tudo normal, e o grupo
    aprenderia a ignorar o aviso justamente para o dia em que ele importa.
    """
    if not rows:
        return ""

    shown = rows[:MAX_DIGEST_UNITS]
    linhas = [
        "🚫 *Unidades com demanda redirecionada*",
        fmt_local(now),
        "",
    ]
    for row in shown:
        linhas.append(f"• {(row.get('unit_name') or '').strip()} — até *{_deadline(row)}*")

    restantes = len(rows) - len(shown)
    if restantes > 0:
        linhas.append(f"• +{restantes} unidade(s) também redirecionada(s)")

    linhas.extend(
        [
            "",
            f"A pedido da {COORDENACAO}, as demandas destas unidades estão sendo "
            "redirecionadas até o prazo indicado.",
            "",
            ASSINATURA,
        ]
    )
    return "\n".join(linhas)


# ---------------------------------------------------------------------------
# Envio
# ---------------------------------------------------------------------------


def dispatch_group_message(message: str) -> bool:
    """Entrega no grupo. True só quando o gateway aceitou.

    Nunca levanta: desligado, grupo vazio e gateway fora do ar são todos "não
    enviou", e quem chama trata os três igual — não avança o snapshot, tenta
    de novo no próximo tick.
    """
    if not message.strip():
        return False

    if not is_enabled():
        motivo = "grupo não configurado" if WHATSAPP_RESTRICTION_ENABLED else "canal desligado"
        logger.info(
            "[dry-run] aviso de restrição NÃO enviado (%s). Enviaria para %r:\n%s",
            motivo,
            WHATSAPP_GROUP_TO or "(vazio)",
            message,
        )
        return False

    try:
        send_gateway_message(WHATSAPP_GROUP_TO, message)
    except Exception as exc:  # noqa: BLE001 - o watcher não pode cair por isto
        logger.warning("Falha ao avisar o grupo sobre restrição de UPA: %s", exc)
        return False

    logger.info("Aviso de restrição enviado ao grupo %s (%d caracteres)", WHATSAPP_GROUP_TO, len(message))
    return True


# ---------------------------------------------------------------------------
# Uma varredura
# ---------------------------------------------------------------------------


def run_restriction_cycle(
    *,
    fetch=fetch_restrictions,
    read_snapshot,
    write_snapshot,
    claim_notice,
    release_notice,
    send=dispatch_group_message,
    channel_on=is_enabled,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Uma varredura completa: mudanças primeiro, lembrete depois.

    Recebe as dependências por parâmetro (banco e envio) porque é aqui que
    mora a ordem das decisões — a parte que precisa de teste e a que não pode
    depender de ter Postgres e WhatsApp por perto.

    Devolve o que aconteceu, para o log do watcher.
    """
    moment = now or datetime.now(timezone.utc)
    resultado: dict[str, Any] = {"changes_sent": 0, "digest_sent": False, "skipped": None}

    current = fetch()
    if current is None:
        # Trava 2: não sabemos o estado, então não afirmamos nada ao grupo.
        resultado["skipped"] = "fonte indisponível"
        return resultado

    previous = read_snapshot()
    if previous is None:
        # Primeira execução desta instalação: o que já estava restrito não é
        # novidade para ninguém — semeia calado e passa a valer daqui.
        write_snapshot(snapshot_from(current))
        resultado["skipped"] = "snapshot semeado"
        logger.info(
            "Snapshot de restrições semeado com %d unidade(s) — nada anunciado", len(current)
        )
        return resultado

    canal_ligado = channel_on()

    def _resolvido(entregue: bool) -> bool:
        """O aviso saiu do caminho — entregue, ou descartado com o canal off.

        A diferença entre "não entregou porque o gateway caiu" e "não entregou
        porque o canal está desligado" decide se o snapshot avança. Tratar
        dry-run como falha faria o mesmo "enviaria" ser logado a cada 2min para
        sempre, e no dia em que o canal fosse ligado o grupo receberia de uma
        vez a fila de mudanças velhas. Dry-run é simulação: o snapshot anda.
        """
        return entregue or not canal_ligado

    # 1. Mudanças. O snapshot avança item a item: o que o gateway recusou
    #    continua pendente e volta a ser tentado na próxima varredura.
    snapshot = dict(previous)
    for event in diff_restrictions(previous, current, moment):
        row = event["row"]
        entregue = send(build_restriction_change_text(event["kind"], row))
        if not _resolvido(entregue):
            continue
        if event["kind"] in {"created", "renewed"}:
            snapshot[row["id"]] = row
        else:
            snapshot.pop(row["id"], None)
        if entregue:
            resultado["changes_sent"] += 1

    if snapshot != previous:
        write_snapshot(snapshot)

    # 2. Lembrete da janela.
    slot = resolve_digest_slot(moment)
    if not slot or not current:
        return resultado

    if not claim_notice(slot):
        return resultado

    if send(build_restrictions_digest(current, moment)):
        resultado["digest_sent"] = True
    elif canal_ligado:
        # Devolve a janela: ainda dá tempo de tentar dentro da tolerância.
        # Com o canal desligado a janela FICA reservada — em dry-run o
        # lembrete é simulado uma vez por janela, não a cada varredura.
        release_notice(slot)

    return resultado
