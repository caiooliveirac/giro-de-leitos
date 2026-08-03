"""Relatórios de gestão entregues pelo bot do Telegram.

Módulo puro: não fala com o banco nem com a rede. Recebe linhas já lidas
(`db.py`) e devolve texto pronto para o celular — curto, com emoji e sem
nenhum dado de paciente (só contagens).

Convenções desta camada:
- todo horário exibido é convertido para REPORT_TIMEZONE (America/Sao_Paulo);
- toda mensagem passa por `split_message` antes de ir para o Telegram;
- nomes de unidade saem encurtados (`short_unit_name`) para caber na tela.
"""

from __future__ import annotations

import os
import re
from datetime import datetime, time, timedelta, timezone
from typing import Any, Iterable

from units import UNIT_REGISTRY

try:  # zoneinfo existe na imagem python:3.12-slim; o fallback é só defensivo
    from zoneinfo import ZoneInfo

    REPORT_TZ = ZoneInfo(os.getenv("REPORT_TIMEZONE", "America/Sao_Paulo"))
except Exception:  # pragma: no cover - só ocorre em imagem sem tzdata
    REPORT_TZ = timezone(timedelta(hours=-3))


# Limite de caracteres por mensagem enviada ao Telegram. O limite duro da API é
# 4.096; 3.500 dá folga para o prefixo de parte e para emojis multibyte.
MESSAGE_CHUNK_LIMIT = 3500

# Máximo de partes por resposta. Acima disso a mensagem é cortada — mas com
# aviso visível, nunca em silêncio.
MAX_MESSAGE_PARTS = 4

# Semáforo de tempo sem giro (alinhado com STALE_ALERT_HOURS=10 do watcher).
FRESH_HOURS = 6.0
STALE_HOURS = 10.0

# Dois giros da mesma unidade dentro desta janela contam como um só: reenvio
# no grupo é comum e derrubaria a média artificialmente.
DUPLICATE_WINDOW_MINUTES = 5.0

# SLA de atualização do giro, por turno. CONSTANTE ÚNICA de threshold: mexer
# aqui muda ranking, snippets de cobrança e todos os rodapés de uma vez.
# Futuro (fase 2): virar tabela por unidade, mesma forma chaveada por unit_code.
GIRO_SLA = {
    "day_start_hour": 7,       # início do turno diurno, hora local
    "day_end_hour": 19,        # fim do diurno = início do noturno
    "day_max_gap_hours": 6.0,  # diurno (07h–19h): gap acima disso é violação
    "night_max_gap_hours": 12.0,  # noturno (19h–07h): gap acima disso é violação
    # A cobrança é escrita em minutos. Sem esta folga, um gap de 6h00m20s vira
    # "6h00 acima do limite de 6h" no texto que vai para o coordenador —
    # indefensável, e o erro é do arredondamento, não da unidade.
    "tolerance_minutes": 1.0,
}

# Guarda do varredor de turnos: unidade que nunca postou gera intervalo de
# meses e o laço precisa de um teto.
_SHIFT_SCAN_MAX_DAYS = 400

# Quantas unidades ganham balão individual de cobrança e quantas violações são
# listadas por unidade. Acima disso a mensagem avisa o que ficou de fora —
# corte silencioso viraria "está tudo aqui" sem estar.
MAX_COBRANCA_SNIPPETS = 8
MAX_VIOLATIONS_LISTED = 6

# Teto de duração atribuída a um único giro no cálculo de lotação (/lotacao).
# Escolha: 6h, o mesmo limiar de "dado velho" de /api/stale-units. Depois de 6h
# sem giro novo, o último snapshot não descreve mais a unidade — continuar
# integrando transformaria silêncio em "tempo lotado".
OVER_CAPACITY_MAX_INTERVAL_HOURS = 6.0


# ---------------------------------------------------------------------------
# Nomes curtos
# ---------------------------------------------------------------------------

_SHORT_UNIT_NAMES = {
    "upa_bairro_da_paz_orlando_imbassahy": "UPA B. DA PAZ",
    "upa_barris": "UPA BARRIS",
    "upa_brotas": "UPA BROTAS",
    "upa_helio_machado": "UPA HÉLIO MACHADO",
    "upa_paripe": "UPA PARIPE",
    "upa_periperi": "UPA PERIPERI",
    "pa_pernambues": "PA PERNAMBUÉS",
    "upa_piraja_santo_inacio": "UPA PIRAJÁ",
    "upa_san_martin": "UPA SAN MARTIN",
    "upa_santo_antonio": "UPA STO. ANTÔNIO",
    "upa_parque_sao_cristovao": "UPA P. SÃO CRISTÓVÃO",
    "pa_sao_marcos": "PA SÃO MARCOS",
    "pa_tancredo_neves_rodrigo_argolo": "PA TANCREDO NEVES",
    "upa_valeria": "UPA VALÉRIA",
    "centro_marback_alfredo_bureau": "12º C. MARBACK",
    "centro_maria_conceicao_santiago_imbassahy": "16º C. MARIA CONCEIÇÃO",
}

UNIT_NAME_BY_CODE = {unit["code"]: unit["canonical_name"] for unit in UNIT_REGISTRY}
_CODE_BY_CANONICAL_NAME = {unit["canonical_name"].casefold(): unit["code"] for unit in UNIT_REGISTRY}


def short_unit_name(unit_code: str | None, fallback: str | None = None) -> str:
    """Nome curto da unidade, para caber na largura da tela do celular.

    Aceita o código ou só o nome canônico — parte dos buckets de vagas carrega
    apenas o nome exibido.
    """
    code = unit_code or _CODE_BY_CANONICAL_NAME.get((fallback or "").strip().casefold())
    if code and code in _SHORT_UNIT_NAMES:
        return _SHORT_UNIT_NAMES[code]
    name = (fallback or UNIT_NAME_BY_CODE.get(unit_code or "") or unit_code or "unidade").strip()
    name = name.split(" - ")[0].strip()
    return name if len(name) <= 24 else name[:23] + "…"


# ---------------------------------------------------------------------------
# Tempo e formatação
# ---------------------------------------------------------------------------


def as_utc(value: Any) -> datetime | None:
    """Converte datetime/ISO string para datetime tz-aware em UTC."""
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    return None


def fmt_local(value: Any, *, with_date: bool = True) -> str:
    """Horário no fuso de exibição (America/Sao_Paulo): '03/08 15:32'."""
    moment = as_utc(value)
    if moment is None:
        return "—"
    local = moment.astimezone(REPORT_TZ)
    return local.strftime("%d/%m %H:%M") if with_date else local.strftime("%H:%M")


def fmt_duration(hours: float | None) -> str:
    """Duração compacta: '45min', '10h48', '2d 3h'."""
    if hours is None:
        return "—"
    if hours < 0:
        hours = 0.0
    if hours < 1:
        return f"{int(round(hours * 60))}min"
    if hours >= 48:
        days = int(hours // 24)
        return f"{days}d {int(hours - days * 24)}h"
    whole = int(hours)
    minutes = int(round((hours - whole) * 60))
    if minutes == 60:
        whole, minutes = whole + 1, 0
    return f"{whole}h{minutes:02d}"


def traffic_light(hours: float | None) -> str:
    """🟢 < 6h · 🟡 6h–10h · 🔴 ≥ 10h (ou nunca postou)."""
    if hours is None:
        return "🔴"
    if hours < FRESH_HOURS:
        return "🟢"
    if hours < STALE_HOURS:
        return "🟡"
    return "🔴"


def fmt_date(value: Any) -> str:
    """Só o dia, no fuso de exibição: '03/08'."""
    moment = as_utc(value)
    return "—" if moment is None else moment.astimezone(REPORT_TZ).strftime("%d/%m")


def threshold_label(hours: float) -> str:
    """'6h' para limite redondo, '6h30' para quebrado."""
    return f"{int(hours)}h" if float(hours).is_integer() else fmt_duration(hours)


def shift_split_hours(start: datetime, end: datetime) -> tuple[float, float]:
    """Divide o intervalo entre os dois turnos locais: (diurno, noturno).

    Diurno é a janela GIRO_SLA[day_start_hour]–[day_end_hour] de cada dia
    local; tudo o que sobra é noturno. A soma das duas parcelas é sempre a
    duração total do intervalo.
    """
    if end <= start:
        return 0.0, 0.0
    start_local = start.astimezone(REPORT_TZ)
    end_local = end.astimezone(REPORT_TZ)
    total = (end_local - start_local).total_seconds() / 3600

    day_hours = 0.0
    day = start_local.date()
    for _ in range(_SHIFT_SCAN_MAX_DAYS):
        if day > end_local.date():
            break
        window_start = datetime.combine(day, time(GIRO_SLA["day_start_hour"]), tzinfo=REPORT_TZ)
        window_end = datetime.combine(day, time(GIRO_SLA["day_end_hour"]), tzinfo=REPORT_TZ)
        overlap = min(end_local, window_end) - max(start_local, window_start)
        if overlap.total_seconds() > 0:
            day_hours += overlap.total_seconds() / 3600
        day += timedelta(days=1)

    return day_hours, max(0.0, total - day_hours)


def classify_gap(start: datetime, end: datetime, *, ongoing: bool = False) -> dict[str, Any]:
    """Descreve um silêncio entre `start` e `end` e diz se ele fere o SLA.

    REGRA DE FRONTEIRA (a "regra mais simples documentada" do requisito): um
    gap que cruza a virada de turno é julgado INTEIRO pelo turno em que passou
    a MAIOR parte do tempo. Um gap = um turno = um limite, então o mesmo
    silêncio nunca é punido duas vezes nem precisa de rateio proporcional para
    ser justo. Empate exato (metade em cada turno) vai para o noturno, que é o
    limite mais tolerante — na dúvida, a favor da unidade.
    """
    day_hours, night_hours = shift_split_hours(start, end)
    hours = day_hours + night_hours
    if day_hours > night_hours:
        shift, limit = "diurno", float(GIRO_SLA["day_max_gap_hours"])
    else:
        shift, limit = "noturno", float(GIRO_SLA["night_max_gap_hours"])
    return {
        "start": start,
        "end": end,
        "hours": hours,
        "day_hours": day_hours,
        "night_hours": night_hours,
        "shift": shift,
        "limit_hours": limit,
        "violated": hours > limit + GIRO_SLA["tolerance_minutes"] / 60,
        "ongoing": ongoing,
    }


# ---------------------------------------------------------------------------
# Envio em partes
# ---------------------------------------------------------------------------


def split_message(
    text: str,
    limit: int = MESSAGE_CHUNK_LIMIT,
    max_parts: int = MAX_MESSAGE_PARTS,
) -> list[str]:
    """Divide a mensagem em partes de até `limit` caracteres, quebrando por
    linha. É por aqui que o /resumo (4.843 chars hoje) deixa de bater no limite
    de 4.096 do sendMessage e voltar 400 — hoje o usuário fica sem resposta."""
    text = (text or "").strip()
    if not text:
        return []
    if len(text) <= limit:
        return [text]

    parts: list[str] = []
    current: list[str] = []
    current_len = 0

    def flush() -> None:
        nonlocal current, current_len
        if current:
            parts.append("\n".join(current).strip())
            current = []
            current_len = 0

    for line in text.split("\n"):
        # Linha sozinha maior que o limite: corta no braço.
        while len(line) > limit:
            flush()
            parts.append(line[:limit])
            line = line[limit:]
        extra = len(line) + (1 if current else 0)
        if current_len + extra > limit:
            flush()
            extra = len(line)
        current.append(line)
        current_len += extra

    flush()
    parts = [part for part in parts if part]

    if len(parts) > max_parts:
        kept = parts[:max_parts]
        kept[-1] = kept[-1][: limit - 40].rstrip() + "\n\n✂️ mensagem cortada — refine o filtro."
        parts = kept

    if len(parts) > 1:
        total = len(parts)
        parts = [f"▸ parte {index}/{total}\n{part}" for index, part in enumerate(parts, start=1)]

    return parts


# ---------------------------------------------------------------------------
# Dispatcher de comandos
# ---------------------------------------------------------------------------

_DAYS_PATTERN = re.compile(r"(\d{1,3})")


def parse_bot_command(text: str | None) -> tuple[str, str] | None:
    """'/Vagas@girodeleitos_bot Amarela' -> ('vagas', 'amarela').

    Devolve None quando o texto não é comando. O sufixo `@bot` é obrigatório na
    entrega do Telegram em grupo — sem removê-lo, todo comando caía na ajuda.
    """
    stripped = (text or "").strip()
    if not stripped.startswith("/"):
        return None
    head, _, rest = stripped.partition(" ")
    if "\n" in head:
        head, _, newline_rest = head.partition("\n")
        rest = f"{newline_rest} {rest}".strip()
    # Barra sozinha continua sendo "comando" (cai na ajuda), como antes.
    command = head[1:].split("@", 1)[0].strip().casefold()
    return command, rest.strip().casefold()


def parse_days_arg(args: str | None, *, default: int = 7, maximum: int = 90) -> int:
    """'7d' / '30' / 'hoje' / 'mes' -> número de dias da janela."""
    text = (args or "").strip().casefold()
    if not text:
        return default
    if text.startswith("hoje") or text.startswith("dia"):
        return 1
    if text.startswith("semana"):
        return 7
    if text.startswith("mes") or text.startswith("mês"):
        return 30
    match = _DAYS_PATTERN.search(text)
    if not match:
        return default
    return max(1, min(maximum, int(match.group(1))))


# ---------------------------------------------------------------------------
# Cálculo: intervalo entre giros (/cobranca)
# ---------------------------------------------------------------------------


def _dedupe_events(moments: list[datetime]) -> list[datetime]:
    """Colapsa reenvios: giros a menos de DUPLICATE_WINDOW_MINUTES contam como um."""
    kept: list[datetime] = []
    for moment in sorted(moments):
        if kept and (moment - kept[-1]).total_seconds() < DUPLICATE_WINDOW_MINUTES * 60:
            continue
        kept.append(moment)
    return kept


def compute_gap_ranking(
    events: Iterable[dict[str, Any]],
    last_by_unit: dict[str, Any],
    now: datetime,
    days: int,
) -> list[dict[str, Any]]:
    """Ranking de tempo sem postar giro, por unidade.

    `events` são giros da janela (unit_code + created_at — chegada real, nunca
    o horário digitado, que tem drift documentado). Devolve uma linha por
    unidade cadastrada, inclusive as que nunca postaram.

    Cada linha carrega a RELAÇÃO das violações de SLA (`violations`): os gaps
    entre giros consecutivos que estouraram o limite do turno, mais o silêncio
    em curso desde o último giro, se ele já estourou. As duas fontes não se
    sobrepõem — o gap em curso começa onde o último gap fechado terminou —,
    então nenhum silêncio é contado duas vezes.
    """
    window_start = now - timedelta(days=days)

    by_unit: dict[str, list[datetime]] = {}
    for event in events:
        code = event.get("unit_code")
        moment = as_utc(event.get("created_at"))
        if not code or moment is None:
            continue
        by_unit.setdefault(code, []).append(moment)

    ranking: list[dict[str, Any]] = []
    for unit_code in UNIT_NAME_BY_CODE:
        moments = _dedupe_events(by_unit.get(unit_code, []))
        gaps: list[float] = []
        violations: list[dict[str, Any]] = []
        for previous, current in zip(moments, moments[1:]):
            gap = classify_gap(previous, current)
            gaps.append(gap["hours"])
            if gap["violated"]:
                violations.append(gap)

        last_moment = as_utc(last_by_unit.get(unit_code)) or (moments[-1] if moments else None)
        current_gap = (now - last_moment).total_seconds() / 3600 if last_moment else None
        if last_moment is not None:
            open_gap = classify_gap(last_moment, now, ongoing=True)
            if open_gap["violated"]:
                violations.append(open_gap)

        ranking.append(
            {
                "unit_code": unit_code,
                "unit_name": short_unit_name(unit_code),
                "giros": len(moments),
                "avg_gap_hours": (sum(gaps) / len(gaps)) if gaps else None,
                "worst_gap_hours": max(gaps) if gaps else None,
                "current_gap_hours": current_gap,
                "last_giro_at": last_moment,
                "never_posted": last_moment is None,
                "violations": violations,
                "violation_count": len(violations),
                "window_start": window_start,
            }
        )

    ranking.sort(
        key=lambda item: (
            item["avg_gap_hours"] is None,
            -(item["avg_gap_hours"] or 0.0),
            item["unit_name"],
        )
    )
    return ranking


# ---------------------------------------------------------------------------
# Cálculo: tempo acima da capacidade (/lotacao)
# ---------------------------------------------------------------------------


def compute_over_capacity_ranking(
    events: Iterable[dict[str, Any]],
    now: datetime,
    days: int,
    max_interval_hours: float = OVER_CAPACITY_MAX_INTERVAL_HOURS,
) -> list[dict[str, Any]]:
    """Integra `is_over_capacity` entre giros consecutivos da mesma unidade.

    Cada giro vale pelo intervalo até o giro seguinte da unidade (ou até
    `now`, no caso do último), limitado a `max_interval_hours` — ver a
    justificativa em OVER_CAPACITY_MAX_INTERVAL_HOURS.
    """
    window_start = now - timedelta(days=days)

    by_unit: dict[str, list[dict[str, Any]]] = {}
    for event in events:
        code = event.get("unit_code")
        moment = as_utc(event.get("created_at"))
        if not code or moment is None:
            continue
        by_unit.setdefault(code, []).append(
            {
                "at": moment,
                "red_over": bool(event.get("red_over")),
                "yellow_over": bool(event.get("yellow_over")),
            }
        )

    ranking: list[dict[str, Any]] = []
    for unit_code, unit_events in by_unit.items():
        unit_events.sort(key=lambda item: item["at"])
        # Reenvio: mantém o primeiro de cada janela de 5 min.
        deduped: list[dict[str, Any]] = []
        for event in unit_events:
            if deduped and (event["at"] - deduped[-1]["at"]).total_seconds() < DUPLICATE_WINDOW_MINUTES * 60:
                continue
            deduped.append(event)

        red_hours = 0.0
        yellow_hours = 0.0
        any_hours = 0.0
        observed_hours = 0.0
        truncated = False

        for index, event in enumerate(deduped):
            start = max(event["at"], window_start)
            next_at = deduped[index + 1]["at"] if index + 1 < len(deduped) else now
            end = min(next_at, now)
            if end <= start:
                continue
            duration = (end - start).total_seconds() / 3600
            if duration > max_interval_hours:
                duration = max_interval_hours
                truncated = True
            observed_hours += duration
            if event["red_over"]:
                red_hours += duration
            if event["yellow_over"]:
                yellow_hours += duration
            if event["red_over"] or event["yellow_over"]:
                any_hours += duration

        if observed_hours <= 0:
            continue

        ranking.append(
            {
                "unit_code": unit_code,
                "unit_name": short_unit_name(unit_code),
                "red_hours": red_hours,
                "yellow_hours": yellow_hours,
                "over_hours": any_hours,
                "observed_hours": observed_hours,
                "share": any_hours / observed_hours if observed_hours else 0.0,
                "giros": len(deduped),
                "truncated": truncated,
            }
        )

    ranking.sort(key=lambda item: (-item["over_hours"], item["unit_name"]))
    return ranking


# ---------------------------------------------------------------------------
# Builder: /vagas
# ---------------------------------------------------------------------------

_SEPARATOR = "━━━━━━━━━━━━━━━━"


def _rooms_of(row: dict[str, Any]) -> dict[str, Any]:
    payload = row.get("payload") or {}
    data = payload.get("data") or {} if isinstance(payload, dict) else {}
    rooms = data.get("rooms") or {}
    return rooms if isinstance(rooms, dict) else {}


def _row_age_hours(row: dict[str, Any], now: datetime) -> float | None:
    moment = as_utc(row.get("updated_at") or row.get("received_at"))
    if moment is None:
        return None
    return (now - moment).total_seconds() / 3600


def _unit_label(row: dict[str, Any]) -> str:
    return short_unit_name(row.get("unit_code"), row.get("displayed_name") or row.get("canonical_name"))


def build_vacancy_report_text(
    buckets: dict[str, Any],
    status_rows: list[dict[str, Any]],
    now: datetime | None = None,
    section: str = "",
) -> str:
    """Onde tem vaga AGORA. `buckets` vem de main.build_priority_buckets."""
    now = now or datetime.now(timezone.utc)
    section = (section or "").strip().casefold()
    show_all = section in {"", "tudo", "todos"}

    def wanted(name: str) -> bool:
        return show_all or section.startswith(name[:3])

    totals = buckets.get("totals", {})
    lines = [f"🛏️ VAGAS AGORA · {fmt_local(now)}", _SEPARATOR]

    # ── Vermelha ────────────────────────────────────────────────────────────
    if wanted("vermelha"):
        red_units = buckets.get("red_priority") or []
        over_capacity: list[str] = []
        full: list[str] = []
        for row in status_rows:
            red_room = _rooms_of(row).get("red_room") or {}
            if not isinstance(red_room, dict) or red_room.get("capacity") is None:
                continue
            if red_room.get("is_over_capacity"):
                over_capacity.append(f"{_unit_label(row)}  {red_room.get('ratio') or ''}".strip())
            elif not red_room.get("has_capacity"):
                full.append(_unit_label(row))

        lines.append("")
        if red_units:
            lines.append(f"🔴 VERMELHA — {len(red_units)} unidade(s) com vaga")
            for name in red_units:
                lines.append(f"  ✅ {short_unit_name(None, name)}")
        else:
            lines.append("🔴 VERMELHA — sem vaga na rede")
        if over_capacity:
            lines.append(f"  ⚠️ {len(over_capacity)} ACIMA da capacidade:")
            lines.extend(f"    • {item}" for item in sorted(over_capacity))
        if full:
            lines.append(f"  🔒 lotadas ({len(full)}): {', '.join(sorted(full))}")

    # ── Amarela ─────────────────────────────────────────────────────────────
    if wanted("amarela"):
        male = buckets.get("yellow_male_priority") or []
        female = buckets.get("yellow_female_priority") or []
        total_yellow = totals.get("yellow_male_vacancies", 0) + totals.get("yellow_female_vacancies", 0)
        lines.append("")
        if male or female:
            lines.append(f"🟡 AMARELA — {total_yellow} vaga(s)")
            for item in male:
                lines.append(f"  👨 {short_unit_name(item.get('unit_key'), item['unit_name'])}  {item['vacancies']}  ({item.get('ratio') or '—'})")
            for item in female:
                lines.append(f"  👩 {short_unit_name(item.get('unit_key'), item['unit_name'])}  {item['vacancies']}  ({item.get('ratio') or '—'})")
        else:
            lines.append("🟡 AMARELA — sem vaga na rede")

    # ── Isolamento ──────────────────────────────────────────────────────────
    if wanted("isolamento") or section.startswith("iso"):
        isolation = buckets.get("isolation_priority") or []
        lines.append("")
        if isolation:
            lines.append(f"🦠 ISOLAMENTO ADULTO — {totals.get('isolation_vacancies', 0)} vaga(s)")
            grouped: dict[str, list[str]] = {}
            for item in isolation:
                icon = {"Feminino": "👩", "Masculino": "👨"}.get(item.get("label") or "", "⚧")
                name = short_unit_name(item.get("unit_key"), item["unit_name"])
                grouped.setdefault(name, []).append(f"{icon}{item['vacancies']}")
            for name, marks in grouped.items():
                lines.append(f"  • {name}  {' '.join(marks)}")
        else:
            lines.append("🦠 ISOLAMENTO ADULTO — sem vaga na rede")

    # ── Internamento / extra ────────────────────────────────────────────────
    if wanted("outros") or section.startswith("intern"):
        other = buckets.get("other_beds") or []
        lines.append("")
        if other:
            lines.append(f"🏥 INTERNAMENTO / EXTRA — {totals.get('other_beds_vacancies', 0)} vaga(s)")
            for item in other:
                name = short_unit_name(item.get("unit_key"), item["unit_name"])
                lines.append(f"  • {name}  {item.get('label') or 'internamento'} {item['vacancies']}  ({item.get('ratio') or '—'})")
        else:
            lines.append("🏥 INTERNAMENTO / EXTRA — sem vaga na rede")

    if show_all:
        orthopedist = buckets.get("with_orthopedist") or []
        lines.append("")
        if orthopedist:
            lines.append(f"🦴 Com ortopedista: {', '.join(short_unit_name(None, name) for name in orthopedist)}")
        else:
            lines.append("🦴 Com ortopedista: nenhuma")

        stale = []
        never = []
        for row in status_rows:
            age = _row_age_hours(row, now)
            if age is None:
                never.append(_unit_label(row))
            elif age >= FRESH_HOURS:
                stale.append((age, _unit_label(row)))
        if stale or never:
            lines.append("")
            lines.append(f"⏱️ Dado velho (>{FRESH_HOURS:.0f}h sem giro):")
            for age, name in sorted(stale, reverse=True)[:6]:
                lines.append(f"  {traffic_light(age)} {name} — há {fmt_duration(age)}")
            for name in sorted(never)[:6]:
                lines.append(f"  🔴 {name} — nunca postou")

    if not show_all:
        lines.append("")
        lines.append("Tudo: /vagas")

    return "\n".join(lines).strip()


# ---------------------------------------------------------------------------
# Builder: /cobranca
# ---------------------------------------------------------------------------

_RANK_ICONS = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣"]


def _responsible_line(unit_code: str, responsibles: dict[str, list[str]]) -> str:
    names = responsibles.get(unit_code) or []
    if not names:
        return "     👤 — sem coordenador cadastrado"
    shown = " · ".join(names[:2])
    if len(names) > 2:
        shown += f" +{len(names) - 2}"
    return f"     👤 {shown}"


def _sla_short() -> str:
    """'diurno (07h–19h) até 6h · noturno (19h–07h) até 12h'."""
    day_start, day_end = GIRO_SLA["day_start_hour"], GIRO_SLA["day_end_hour"]
    return (
        f"diurno ({day_start:02d}h–{day_end:02d}h) até {threshold_label(GIRO_SLA['day_max_gap_hours'])}"
        f" · noturno ({day_end:02d}h–{day_start:02d}h) até {threshold_label(GIRO_SLA['night_max_gap_hours'])}"
    )


def _sla_sentence() -> str:
    """A mesma regra em prosa, para dentro do texto que vai ser copiado."""
    day_start, day_end = GIRO_SLA["day_start_hour"], GIRO_SLA["day_end_hour"]
    return (
        f"O combinado é atualizar o giro a cada {threshold_label(GIRO_SLA['day_max_gap_hours'])}"
        f" no turno diurno ({day_start:02d}h–{day_end:02d}h) e a cada"
        f" {threshold_label(GIRO_SLA['night_max_gap_hours'])} no noturno"
        f" ({day_end:02d}h–{day_start:02d}h)."
    )


def _greeting(unit_name: str, names: list[str]) -> str:
    """'Olá, Joilson!' — primeiro nome, porque o cadastro vem em caixa alta."""
    firsts = [part.split()[0].capitalize() for part in names if part and part.split()]
    if not firsts:
        return f"Olá, equipe da {unit_name}!"
    if len(firsts) == 1:
        return f"Olá, {firsts[0]}!"
    return f"Olá, {firsts[0]} e {firsts[1]}!"


def _plural(count: int, singular: str, plural: str) -> str:
    return f"{count} {singular if count == 1 else plural}"


def _violation_bullet(violation: dict[str, Any]) -> str:
    """Uma falha em prosa — o texto vai inteiro para o WhatsApp de alguém.

    Gap que vira o dia sai com as duas datas: '17:45 às 05:43' sozinho esconde
    que o silêncio durou 36h e não 12h.
    """
    tail = f"turno {violation['shift']}; limite {threshold_label(violation['limit_hours'])}"
    duration = fmt_duration(violation["hours"])
    start_date = fmt_date(violation["start"])
    start_time = fmt_local(violation["start"], with_date=False)

    if violation["ongoing"]:
        return f"• sem atualização desde {start_date} {start_time} — já são {duration} ({tail})"
    end_date = fmt_date(violation["end"])
    end_time = fmt_local(violation["end"], with_date=False)
    if end_date == start_date:
        return f"• silêncio em {start_date}, das {start_time} às {end_time} ({duration}, {tail})"
    return f"• silêncio de {start_date} {start_time} a {end_date} {end_time} ({duration}, {tail})"


def _violation_bullets(violations: list[dict[str, Any]]) -> list[str]:
    """As violações mais recentes primeiro no corte, em ordem cronológica."""
    shown = violations[-MAX_VIOLATIONS_LISTED:]
    lines = [_violation_bullet(violation) for violation in shown]
    hidden = len(violations) - len(shown)
    if hidden > 0:
        lines.append(f"• (+ {_plural(hidden, 'ocorrência anterior', 'ocorrências anteriores')} no período)")
    return lines


def _is_silent_now(item: dict[str, Any]) -> bool:
    """A unidade está com o SLA estourado NESTE momento?"""
    return item["never_posted"] or any(violation["ongoing"] for violation in item["violations"])


def delinquent_units(ranking: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Unidades que devem cobrança, da mais urgente para a menos.

    Primeiro quem está estourado agora (nunca postou, depois maior tempo de
    silêncio em curso) — é o que o gestor precisa resolver hoje. Depois quem
    já normalizou, ordenado pelo tamanho da ficha no período.

    Unidade sem nenhuma violação na janela não entra e, portanto, não ganha
    balão de cobrança.
    """
    offenders = [item for item in ranking if item["violation_count"] > 0 or item["never_posted"]]
    offenders.sort(
        key=lambda item: (
            0 if item["never_posted"] else (1 if _is_silent_now(item) else 2),
            -item["violation_count"] if not _is_silent_now(item) else 0,
            -(item["current_gap_hours"] or 0.0),
            item["unit_name"],
        )
    )
    return offenders


def _build_ranking_message(
    ranking: list[dict[str, Any]],
    offenders: list[dict[str, Any]],
    responsibles: dict[str, list[str]],
    now: datetime,
    days: int,
    unparsed_count: int,
) -> str:
    """Balão 1: o ranking operacional, para o gestor ler — não para copiar."""
    lines = [
        "📉 COBRANÇA DE GIRO",
        f"🗓️ Janela: {days} dia(s) · até {fmt_local(now)}",
        f"⏱️ SLA: {_sla_short()}",
        _SEPARATOR,
        "",
        "🏆 RANKING — MAIS TEMPO SEM POSTAR",
    ]

    for index, item in enumerate(offenders):
        icon = _RANK_ICONS[index] if index < len(_RANK_ICONS) else "•"
        lines.append(f"{icon} {item['unit_name']}")
        if item["never_posted"]:
            lines.append("     🔴 nunca postou giro")
        elif _is_silent_now(item):
            ongoing = next(violation for violation in item["violations"] if violation["ongoing"])
            lines.append(
                f"     🔴 SEM GIRO AGORA há {fmt_duration(item['current_gap_hours'])}"
                f" (limite {ongoing['shift']} {threshold_label(ongoing['limit_hours'])})"
            )
        else:
            lines.append(f"     🟢 em dia agora ({fmt_duration(item['current_gap_hours'])} sem giro)")
        lines.append(f"     ⚠️ {_plural(item['violation_count'], 'violação', 'violações')} no período")
        lines.append(_responsible_line(item["unit_code"], responsibles))

    em_dia = len(ranking) - len(offenders)
    if em_dia > 0:
        lines.append("")
        lines.append(f"✅ Sem nenhuma violação na janela: {_plural(em_dia, 'unidade', 'unidades')}.")

    if unparsed_count:
        lines.append("")
        lines.append(
            f"⚠️ {_plural(unparsed_count, 'mensagem não parseada', 'mensagens não parseadas')}"
            f" na janela — ver /naoparseados {days}"
        )

    extras = len(offenders) - MAX_COBRANCA_SNIPPETS
    lines.extend(["", "👇 A seguir, 1 balão por unidade: toque e segure para", "   copiar e mandar ao coordenador."])
    if extras > 0:
        lines.append(
            f"   (balão individual só para as {MAX_COBRANCA_SNIPPETS} piores;"
            f" as outras {extras} estão no consolidado final)"
        )

    lines.extend(
        [
            "",
            "ℹ️ Gap que cruza a virada de turno é julgado pelo",
            "   turno onde passou a maior parte do tempo — um",
            "   silêncio nunca conta duas vezes. 👤 = coordenador",
            "   cadastrado, não necessariamente quem enviou o giro.",
            "   Base: horário de chegada do giro no sistema.",
        ]
    )
    return "\n".join(lines).strip()


def build_unit_charge_snippet(
    item: dict[str, Any],
    responsibles: dict[str, list[str]],
    days: int,
) -> str:
    """Balão por unidade: SÓ o texto a ser copiado.

    Nada de cabeçalho de sistema aqui — no Telegram o "copiar" leva a mensagem
    inteira, então qualquer enfeite iria junto para o WhatsApp do coordenador.
    """
    unit_name = item["unit_name"]
    lines = [_greeting(unit_name, responsibles.get(item["unit_code"]) or []), ""]

    if item["never_posted"]:
        lines.append(
            f"Não localizamos nenhum giro de leitos da {unit_name} nos nossos"
            f" registros. Precisamos que a unidade comece a postar o giro no grupo."
        )
    else:
        lines.append(
            f"Sobre o giro de leitos da {unit_name}: nos últimos"
            f" {_plural(days, 'dia', 'dias')} registramos"
            f" {_plural(item['violation_count'], 'período', 'períodos')}"
            f" sem atualização acima do combinado."
        )
        lines.append("")
        lines.extend(_violation_bullets(item["violations"]))

    lines.extend(["", _sla_sentence(), "", "Consegue verificar com a equipe? Obrigado!"])
    return "\n".join(lines).strip()


def _build_consolidated_message(
    offenders: list[dict[str, Any]],
    responsibles: dict[str, list[str]],
    now: datetime,
    days: int,
) -> str:
    """Último balão: todas as violações num texto só, para o gestor maior."""
    total = sum(item["violation_count"] for item in offenders)
    window_start = now - timedelta(days=days)
    lines = [
        f"CONSOLIDADO — CUMPRIMENTO DO GIRO DE LEITOS ({fmt_date(window_start)} a {fmt_date(now)})",
        "",
        f"SLA: {_sla_short()}.",
        f"Resultado: {_plural(total, 'violação', 'violações')}"
        f" em {_plural(len(offenders), 'unidade', 'unidades')}.",
        "",
    ]

    for index, item in enumerate(offenders, start=1):
        names = responsibles.get(item["unit_code"]) or []
        coordinator = " · ".join(names[:2]) if names else "sem coordenador cadastrado"
        if item["never_posted"]:
            situacao = "nunca postou giro"
        elif _is_silent_now(item):
            situacao = f"SEM GIRO AGORA há {fmt_duration(item['current_gap_hours'])}"
        else:
            situacao = f"em dia agora ({fmt_duration(item['current_gap_hours'])} sem giro)"
        lines.append(
            f"{index}. {item['unit_name']} — {_plural(item['violation_count'], 'violação', 'violações')}"
            f" · {situacao} · coord.: {coordinator}"
        )
        lines.extend(f"   {bullet}" for bullet in _violation_bullets(item["violations"]))
        lines.append("")

    lines.append("Contagem automática pelo horário de chegada do giro no sistema.")
    return "\n".join(lines).strip()


def build_compliance_messages(
    ranking: list[dict[str, Any]],
    responsibles: dict[str, list[str]],
    now: datetime,
    days: int,
    unparsed_count: int = 0,
) -> list[str]:
    """Os balões do /cobranca, na ordem em que são enviados.

    1. ranking das unidades em violação (para o gestor ler);
    2. um balão por unidade em violação, pronto para copiar e colar;
    3. consolidado de todas as violações, para o gestor maior.

    Sem violação na janela, devolve um balão único dizendo exatamente isso.
    """
    offenders = delinquent_units(ranking)

    if not offenders:
        return [
            "\n".join(
                [
                    "✅ COBRANÇA DE GIRO",
                    f"🗓️ Janela: {days} dia(s) · até {fmt_local(now)}",
                    _SEPARATOR,
                    "",
                    "Nenhuma violação de SLA no período.",
                    f"Todas as {len(ranking)} unidade(s) dentro do combinado:",
                    f"{_sla_short()}.",
                    "",
                    "Nada a cobrar — nenhum balão de cobrança gerado.",
                ]
            )
        ]

    messages = [_build_ranking_message(ranking, offenders, responsibles, now, days, unparsed_count)]
    messages.extend(
        build_unit_charge_snippet(item, responsibles, days)
        for item in offenders[:MAX_COBRANCA_SNIPPETS]
    )
    messages.append(_build_consolidated_message(offenders, responsibles, now, days))
    return messages


# ---------------------------------------------------------------------------
# Builder: /naoparseados
# ---------------------------------------------------------------------------

# Mascara sequências longas de dígitos (telefone, prontuário, CPF). Números
# curtos — razões tipo 04/07 e horários — ficam, porque são o diagnóstico.
_LONG_DIGITS = re.compile(r"\d{5,}")


def truncate(value: str, limit: int = 70) -> str:
    cleaned = re.sub(r"\s+", " ", (value or "").strip())
    return cleaned if len(cleaned) <= limit else cleaned[: limit - 1] + "…"


def redact_line(line: str, limit: int = 70) -> str:
    """Trecho de mensagem pronto para exibição: mascara números longos e corta.

    Aplicado ao *conteúdo* da mensagem. O campo `source` não passa por aqui —
    é metadado do sistema (id do grupo) e saber de qual grupo veio a falha é
    justamente o objetivo do relatório.
    """
    return truncate(_LONG_DIGITS.sub("#####", line or ""), limit)


def build_unparsed_report_text(
    summary: dict[str, Any],
    now: datetime,
    days: int,
) -> str:
    """Insight das mensagens que não passaram no parser."""
    total = summary.get("total", 0)
    lines = [
        "🧩 MENSAGENS NÃO PARSEADAS",
        f"🗓️ Janela: {days} dia(s) · {fmt_local(now)}",
        _SEPARATOR,
        "",
    ]

    if not total:
        lines.append("✅ Nenhuma mensagem fora do padrão na janela.")
        lines.append("")
        lines.append("ℹ️ Contam as que o sistema reconheceu como giro mas")
        lines.append("   não conseguiu publicar (unidade/horário ausentes).")
        return "\n".join(lines)

    lines.append(f"📌 Total: {total} mensagem(ns) fora do padrão")

    by_source = summary.get("by_source") or []
    if by_source:
        lines.append("")
        lines.append("📡 Por origem/grupo:")
        for item in by_source[:6]:
            lines.append(f"  • {truncate(str(item.get('source') or '—'), 44)} — {item.get('count', 0)}")

    by_reason = summary.get("by_reason") or []
    if by_reason:
        lines.append("")
        lines.append("❓ Por motivo:")
        for item in by_reason[:5]:
            lines.append(f"  • {redact_line(str(item.get('reason') or '—'), 52)} — {item.get('count', 0)}")

    samples = summary.get("samples") or []
    if samples:
        lines.append("")
        lines.append("🔍 Últimas ocorrências:")
        for sample in samples[:5]:
            lines.append(f"  🕐 {fmt_local(sample.get('created_at'))} · {truncate(str(sample.get('source') or '—'), 30)}")
            lines.append(f"     📄 {redact_line(str(sample.get('first_line') or ''))}")
            reason = sample.get("reason")
            if reason:
                lines.append(f"     ❓ {redact_line(str(reason), 62)}")

    lines.extend(
        [
            "",
            "ℹ️ Só a 1ª linha de cada mensagem é exibida, sem",
            "   dado de paciente. Janela maior: /naoparseados 30",
        ]
    )
    return "\n".join(lines).strip()


# ---------------------------------------------------------------------------
# Builder: /lotacao
# ---------------------------------------------------------------------------


def build_over_capacity_report_text(
    ranking: list[dict[str, Any]],
    now: datetime,
    days: int,
    max_interval_hours: float = OVER_CAPACITY_MAX_INTERVAL_HOURS,
) -> str:
    """Tempo acumulado acima da capacidade, por unidade."""
    lines = [
        "🚨 TEMPO ACIMA DA CAPACIDADE",
        f"🗓️ Janela: {days} dia(s) · {fmt_local(now)}",
        _SEPARATOR,
        "",
    ]

    com_lotacao = [item for item in ranking if item["over_hours"] > 0]
    if not com_lotacao:
        lines.append("✅ Nenhuma unidade acima da capacidade na janela.")
    else:
        for index, item in enumerate(com_lotacao[:10]):
            icon = _RANK_ICONS[index] if index < len(_RANK_ICONS) else "•"
            share = int(round(item["share"] * 100))
            lines.append(f"{icon} {item['unit_name']}  {fmt_duration(item['over_hours'])}  ({share}% do tempo)")
            detail = []
            if item["red_hours"] > 0:
                detail.append(f"🔴 vermelha {fmt_duration(item['red_hours'])}")
            if item["yellow_hours"] > 0:
                detail.append(f"🟡 amarela {fmt_duration(item['yellow_hours'])}")
            if detail:
                lines.append("     " + " · ".join(detail))

        limpas = [item for item in ranking if item["over_hours"] <= 0]
        if limpas:
            lines.append("")
            lines.append(f"✅ Sem excedente: {', '.join(sorted(item['unit_name'] for item in limpas)[:6])}")

    lines.extend(
        [
            "",
            "ℹ️ Cada giro vale até o giro seguinte da unidade,",
            f"   com teto de {max_interval_hours:.0f}h — depois disso o dado",
            "   está velho e o tempo deixa de ser contado.",
        ]
    )
    return "\n".join(lines).strip()


# ---------------------------------------------------------------------------
# Builder: alerta de silêncio por WhatsApp (gestor)
# ---------------------------------------------------------------------------

# Teto de unidades citadas nominalmente no aviso. O alerta é lido no celular,
# em pé, no corredor: passar disso vira relatório e para de ser lido. O que
# não couber é anunciado como "+N", nunca cortado em silêncio.
MAX_WHATSAPP_ALERT_UNITS = 8


def _coordinator_summary(unit_code: str | None, responsibles: dict[str, list[str]]) -> str:
    """'Joilson Santos', 'Joilson · Maria +2' ou 'sem coordenador'.

    Só o nome — telefone de coordenador nunca sai em mensagem automática
    (mesma regra de db.get_unit_responsibles).
    """
    names = responsibles.get(unit_code or "") or []
    if not names:
        return "sem coordenador"
    shown = " · ".join(names[:2])
    return f"{shown} +{len(names) - 2}" if len(names) > 2 else shown


def build_whatsapp_stale_alert_text(
    offenders: list[dict[str, Any]],
    responsibles: dict[str, list[str]],
    now: datetime,
    threshold_hours: float,
) -> str:
    """Aviso curto de UPA sem giro, em texto puro para o WhatsApp do gestor.

    Não é relatório: uma linha por unidade com horas e coordenador, e nada
    mais. Sem HTML (o WhatsApp não entende) — negrito é *asterisco*.

    `offenders` já vem filtrado por limiar e cooldown
    (services.whatsapp_alerts.select_stale_offenders); aqui só se formata.
    """
    ordered = sorted(offenders, key=lambda item: item.get("age_hours") or 0.0, reverse=True)
    shown = ordered[:MAX_WHATSAPP_ALERT_UNITS]
    lines = [
        f"🔴 *UPA sem giro há mais de {threshold_label(threshold_hours)}*",
        fmt_local(now),
        "",
    ]
    for item in shown:
        name = short_unit_name(item.get("unit_code"), item.get("unit_name"))
        coordinator = _coordinator_summary(item.get("unit_code"), responsibles)
        lines.append(f"• {name} — {fmt_duration(item.get('age_hours'))} · 👤 {coordinator}")

    remaining = len(ordered) - len(shown)
    if remaining > 0:
        lines.append(f"• +{remaining} unidade(s) também sem giro")

    lines.extend(["", "Giro de Leitos · aviso automático"])
    return "\n".join(lines).strip()


# ---------------------------------------------------------------------------
# Ajuda
# ---------------------------------------------------------------------------


def build_reports_help_lines() -> list[str]:
    return [
        "Relatórios de gestão:",
        "- /vagas — onde tem vaga agora",
        "- /cobranca [dias] — quem furou o SLA de giro (+ texto pronto)",
        "- /lotacao [dias] — tempo acima da capacidade",
        "- /naoparseados [dias] — giros fora do padrão",
    ]
