from __future__ import annotations

import re
import unicodedata
from datetime import datetime, timedelta, timezone
from typing import Any

UPA_NAME_PATTERNS = [
    re.compile(r"(?im)^\s*(?:🏥\s*)?unidade\s*[:\-]?\s*([A-ZÀ-ÖØ-öø-ÿ0-9º°()\-./ ]+)$"),
    re.compile(r"(?im)^\s*(?:📍\s*)?unidade\s+([A-ZÀ-ÖØ-öø-ÿ0-9º°()\-./ ]+)$"),
    re.compile(r"(?im)^\s*((?:UPA|PA)\s+[A-ZÀ-ÖØ-Þ0-9][A-ZÀ-ÖØ-öø-ÿ0-9º°()\-./ ]+)$"),
    re.compile(r"(?im)^\s*(UPA\s+[A-ZÀ-ÖØ-Þ0-9][A-ZÀ-ÖØ-öø-ÿ0-9\- ]+)$"),
    re.compile(r"(?im)\b(UPA\s+[A-ZÀ-ÖØ-Þ0-9][A-ZÀ-ÖØ-öø-ÿ0-9\- ]+)\b"),
]

RATIO_PATTERN = re.compile(r"\(?\s*(\d{1,2})\s*/\s*(\d{1,2})\s*\)?")
COUNT_PATTERN = re.compile(r"\(?\s*(\d{1,2})\s*\)?")
# Padrão de data dd/mm/yyyy (3 componentes separados por / . ou -)
# Usado para remover datas de uma linha antes de buscar ratios,
# evitando que "08/03/2026" seja lido como ratio 8/3.
DATE_IN_LINE_PATTERN = re.compile(r"\d{1,2}\s*[/.\-]\s*\d{1,2}\s*[/.\-]\s*\d{2,4}")
TIME_PATTERNS = [
    # 1) Com label + HH:MM ou HHhMM – mais confiável
    re.compile(
        r"(?i)(?:⏰|🕐|🕑|🕒|🕓|🕔|🕕|🕖|🕗|🕘|🕙|🕚|🕛)?\s*(?:hor[aá]rio|hora|hr|hrs?)\s*[:\-]?\s*(\d{1,2})\s*[:h]\s*(\d{2})(?:\s*(?:h|hs|horas?|min))?"
    ),
    # 2) Standalone HH:MM
    re.compile(
        r"(?i)(?:⏰|🕐|🕑|🕒|🕓|🕔|🕕|🕖|🕗|🕘|🕙|🕚|🕛)?\s*(\d{1,2})\s*:\s*(\d{2})(?:\s*(?:h|hs|horas?|min))?\b"
    ),
    # 3) Standalone NNhNN: "09h05", "14h30min" (sem label, usa h como separador)
    re.compile(
        r"(?i)(?:⏰|🕐|🕑|🕒|🕓|🕔|🕕|🕖|🕗|🕘|🕙|🕚|🕛)?\s*(\d{1,2})\s*h\s*(\d{2})(?:\s*min(?:utos?)?)?"
    ),
    # 4) Hora-só com label: "Hora: 06h", "HORARIO: 00H", "⏰HORARIO: 06H"
    re.compile(
        r"(?i)(?:⏰|🕐|🕑|🕒|🕓|🕔|🕕|🕖|🕗|🕘|🕙|🕚|🕛)?\s*(?:hor[aá]rio|hora|hr|hrs?)\s*[:\-]?\s*(\d{1,2})\s*[hH](?:\s*(?:horas?|hs))?(?!\s*\d)"
    ),
]
# Padrão de horário de plantão/escala: "07:00 ÀS 19:00", "07h às 19h" etc.
SCHEDULE_EXCLUDE_PATTERN = re.compile(
    r"(?i)\d{1,2}\s*(?:[:h]\s*\d{0,2})?\s*(?:h|hs|horas?)?\s+[àáa]s?\s+\d{1,2}\s*(?:[:h]|h)"
)
DATE_PATTERN = re.compile(r"(?i)\b(?:data\s*[:\-]?\s*)?(\d{1,2})[./\-](\d{1,2})[./\-](\d{2,4})\b")
NO_YELLOW_PATTERNS = [
    re.compile(r"(?i)nao\s+dispoe\s+de\s+leito\s+de\s+amarela"),
    re.compile(r"(?i)n[aã]o\s+disp[oõ]e\s+de\s+leito\s+de\s+amarela"),
    re.compile(r"(?i)sem\s+leitos?\s+de\s+sala\s+amarela"),
    re.compile(r"(?i)sem\s+sala\s+amarela"),
]
FIXED_NO_YELLOW_PATTERNS = [
    re.compile(r"(?i)orlando\s+imbassahy"),
    re.compile(r"(?i)(?:upa\s+)?periperi"),
    re.compile(r"(?i)adroaldo\s+albergaria"),
]
MESSAGE_FORMATTING_PATTERN = re.compile(r"[*_`~]+")
TIME_HINT_PATTERN = re.compile(r"(?i)(?:⏰|🕐|🕑|🕒|🕓|🕔|🕕|🕖|🕗|🕘|🕙|🕚|🕛|hora|hor[aá]rio|hr|hrs?)")

CORRIDOR_HEADER_PATTERN = re.compile(r"(?im)^\s*corredor\b\s*:?")
SECTION_BREAK_PATTERN = re.compile(
    r"(?im)^\s*(?:UPA\b|SALA\b|OBS\b|OBSERVA(?:ÇÃO|CAO)?\b|ORTOPED(?:IA|ISTA)\b|CIRURGI(?:A|ÃO|AO)\b|PSIQUIATR(?:IA|A)\b|CL[ÍI]NICA\b)"
)
AGE_PATTERN = re.compile(r"\b\d{1,3}\s*(?:anos?|a)\b", re.IGNORECASE)
INITIALS_PATTERN = re.compile(r"^(?:[A-ZÀ-ÖØ-Þ]\.?\s*){2,}")
LEADING_NOISE_PATTERN = re.compile(r"^[\-•*\d\.)\s]+")

SPECIALIST_RULES = {
    "has_orthopedist": {
        "keywords": ["ortopedia", "ortopedista"],
        "available": [
            re.compile(r"(?i)ortoped(?:ia|ista)\s*[:\-]?\s*(sim|presente|dispon[ií]vel)"),
            re.compile(r"(?i)com\s+ortoped(?:ia|ista)"),
            re.compile(r"(?im)^\s*[\(\[]?\s*✅\s*[\)\]]?\s*ortoped(?:ia|ista)"),
            re.compile(r"(?im)ortoped(?:ia|ista)[ \t]*[\)\]]?[ \t]*[:\-]?[ \t]*[\(\[]?[ \t]*✅"),
        ],
        "unavailable": [
            re.compile(r"(?i)sem\s+ortoped(?:ia|ista)"),
            re.compile(r"(?i)ortoped(?:ia|ista)\s*[:\-]?\s*(n[aã]o|ausente|indispon[ií]vel)"),
            re.compile(r"(?im)^\s*[\(\[]?\s*❌\s*[\)\]]?\s*ortoped(?:ia|ista)"),
            re.compile(r"(?im)ortoped(?:ia|ista)[ \t]*[\)\]]?[ \t]*[:\-]?[ \t]*[\(\[]?[ \t]*❌"),
        ],
    },
    "has_surgeon": {
        "keywords": ["cirurgia", "cirurgiao", "cirurgião", "cirurgia geral"],
        "available": [
            re.compile(r"(?i)cirurgi(?:[aã]o|ao|a)\s*[:\-]?\s*(sim|presente|dispon[ií]vel)"),
            re.compile(r"(?i)com\s+cirurgi(?:[aã]o|ao|a)"),
            re.compile(r"(?im)^\s*[\(\[]?\s*✅\s*[\)\]]?\s*cirurgi(?:[aã]o|ao|a)"),
            re.compile(r"(?im)cirurgi(?:[aã]o|ao|a)[ \t]*[\)\]]?[ \t]*[:\-]?[ \t]*[\(\[]?[ \t]*✅"),
        ],
        "unavailable": [
            re.compile(r"(?i)sem\s+cirurgi(?:[aã]o|ao|a)"),
            re.compile(r"(?i)cirurgi(?:[aã]o|ao|a)\s*[:\-]?\s*(n[aã]o|ausente|indispon[ií]vel)"),
            re.compile(r"(?im)^\s*[\(\[]?\s*❌\s*[\)\]]?\s*cirurgi(?:[aã]o|ao|a)"),
            re.compile(r"(?im)cirurgi(?:[aã]o|ao|a)[ \t]*[\)\]]?[ \t]*[:\-]?[ \t]*[\(\[]?[ \t]*❌"),
        ],
    },
    "has_psychiatrist": {
        "keywords": ["psiquiatria", "psiquiatra"],
        "available": [
            re.compile(r"(?i)psiquiatr(?:a|ia)\s*[:\-]?\s*(sim|presente|dispon[ií]vel)"),
            re.compile(r"(?i)com\s+psiquiatr(?:a|ia)"),
            re.compile(r"(?im)^\s*[\(\[]?\s*✅\s*[\)\]]?\s*psiquiatr(?:a|ia)"),
            re.compile(r"(?im)psiquiatr(?:a|ia)[ \t]*[\)\]]?[ \t]*[:\-]?[ \t]*[\(\[]?[ \t]*✅"),
        ],
        "unavailable": [
            re.compile(r"(?i)sem\s+psiquiatr(?:a|ia)"),
            re.compile(r"(?i)psiquiatr(?:a|ia)\s*[:\-]?\s*(n[aã]o|ausente|indispon[ií]vel)"),
            re.compile(r"(?im)^\s*[\(\[]?\s*❌\s*[\)\]]?\s*psiquiatr(?:a|ia)"),
            re.compile(r"(?im)psiquiatr(?:a|ia)[ \t]*[\)\]]?[ \t]*[:\-]?[ \t]*[\(\[]?[ \t]*❌"),
        ],
    },
}


def _normalize_for_match(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value)
    normalized = "".join(ch for ch in normalized if not unicodedata.combining(ch))
    normalized = normalized.casefold()
    normalized = re.sub(r"[^a-z0-9/()\s]", " ", normalized)
    normalized = re.sub(r"\s+", " ", normalized).strip()
    return normalized


def _strip_message_formatting(value: str) -> str:
    cleaned = MESSAGE_FORMATTING_PATTERN.sub("", value)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned


def _build_capacity(occupied: int, capacity: int) -> dict[str, Any]:
    return {
        "occupied": occupied,
        "capacity": capacity,
        "has_capacity": occupied < capacity,
        "is_over_capacity": occupied > capacity,
        "ratio": f"{occupied:02d}/{capacity:02d}",
    }


LOCAL_TIMEZONE = timezone(timedelta(hours=-3))


def _extract_ratio(line: str) -> tuple[int, int] | None:
    # Remove padrões de data (dd/mm/yyyy) para evitar confusão com ratios
    # Ex: "DATA: 08/03/2026" → "DATA: " (sem o 08/03 que casaria como ratio)
    cleaned = DATE_IN_LINE_PATTERN.sub("", line)
    match = RATIO_PATTERN.search(cleaned)
    if not match:
        return None
    return int(match.group(1)), int(match.group(2))


def _extract_count(line: str) -> int | None:
    if RATIO_PATTERN.search(line):
        return None
    match = COUNT_PATTERN.search(line)
    if not match:
        return None
    return int(match.group(1))


def _find_line_index(lines: list[str], *needles: str) -> int | None:
    normalized_needles = [_normalize_for_match(needle) for needle in needles]
    for index, line in enumerate(lines):
        normalized_line = _normalize_for_match(line)
        if all(needle in normalized_line for needle in normalized_needles):
            return index
    return None


def _find_section_index(lines: list[str], *needles: str, exclude_terms: tuple[str, ...] = ()) -> int | None:
    normalized_needles = [_normalize_for_match(needle) for needle in needles]
    normalized_excludes = [_normalize_for_match(term) for term in exclude_terms]

    best_index: int | None = None
    for index, line in enumerate(lines):
        normalized_line = _normalize_for_match(line)
        if not all(needle in normalized_line for needle in normalized_needles):
            continue
        if any(term in normalized_line for term in normalized_excludes):
            continue

        ratio = _extract_ratio(line)
        if ratio is None:
            for candidate in _collect_section_lines(lines, index, limit=3):
                ratio = _extract_ratio(candidate)
                if ratio is not None:
                    break

        if ratio is not None:
            return index

        best_index = index

    return best_index


def _is_section_header(line: str) -> bool:
    normalized = _normalize_for_match(line)
    header_terms = [
        "sala vermelha",
        "sala amarela",
        "sala medicacao",
        "internamento pediatria",
        "internamento pediatrico",
        "isolamento",
        "isolamentos",
        "exames",
        "obito",
        "obitario",
        "triagem",
        "atendimento",
        "unidade",
        "upa ",
        "pa ",
    ]
    return any(term in normalized for term in header_terms)


def _collect_section_lines(lines: list[str], start_index: int, limit: int = 8) -> list[str]:
    collected: list[str] = []
    for line in lines[start_index + 1 :]:
        if not line.strip():
            if collected:
                break
            continue
        if collected and _is_section_header(line):
            break
        collected.append(line)
        if len(collected) >= limit:
            break
    return collected


def _extract_upa_name(text: str) -> str | None:
    candidate_texts = [text]
    stripped_text = _strip_message_formatting(text)
    if stripped_text != text:
        candidate_texts.append(stripped_text)

    for candidate_text in candidate_texts:
        for pattern in UPA_NAME_PATTERNS:
            match = pattern.search(candidate_text)
            if match:
                return re.sub(r"\s+", " ", _strip_message_formatting(match.group(1))).strip()

        for raw_line in candidate_text.splitlines():
            line = _strip_message_formatting(raw_line)
            if not line:
                continue

            explicit_unit_match = re.search(r"(?i)(?:🏥|📍)?\s*unidade\s*[:\-]?\s*(.+)$", line)
            if explicit_unit_match:
                candidate_name = re.sub(r"\s+", " ", explicit_unit_match.group(1)).strip()
                normalized_candidate = _normalize_for_match(candidate_name)
                if normalized_candidate.startswith("sem ") or normalized_candidate.startswith("nao ") or normalized_candidate.startswith("não "):
                    continue
                return candidate_name

            normalized_line = _normalize_for_match(line)
            if normalized_line.startswith("upa ") or normalized_line.startswith("pa "):
                return re.sub(r"\s+", " ", line).strip()

    return None


def _extract_red_room(lines: list[str]) -> dict[str, Any] | None:
    red_index = _find_section_index(lines, "sala", "vermelha", exclude_terms=("atualizacao", "amarela"))
    if red_index is None:
        red_index = _find_line_index(lines, "sala", "vermelha")
    if red_index is None:
        return None

    ratio = _extract_ratio(lines[red_index])
    if ratio is None:
        for line in _collect_section_lines(lines, red_index, limit=2):
            ratio = _extract_ratio(line)
            if ratio is not None:
                break

    if ratio is None:
        return None

    return _build_capacity(*ratio)


def _extract_reported_datetime(text: str, fallback: datetime | None = None) -> datetime | None:
    base = fallback.astimezone(LOCAL_TIMEZONE) if fallback else datetime.now(LOCAL_TIMEZONE)

    date_match = DATE_PATTERN.search(text)
    if date_match:
        day = int(date_match.group(1))
        month = int(date_match.group(2))
        year = int(date_match.group(3))
        if year < 100:
            year += 2000
    else:
        day = base.day
        month = base.month
        year = base.year

    candidate_blocks: list[str] = []
    for line in text.splitlines():
        stripped = _strip_message_formatting(line)
        if not stripped:
            continue
        normalized = _normalize_for_match(stripped)
        if TIME_HINT_PATTERN.search(stripped) or "data" in normalized:
            candidate_blocks.append(stripped)

    for line in text.splitlines():
        stripped = _strip_message_formatting(line)
        if stripped:
            candidate_blocks.append(stripped)
        if len(candidate_blocks) >= 12:
            break

    candidate_blocks.append(_strip_message_formatting(text))

    seen_blocks: set[str] = set()
    time_components: tuple[int, int] | None = None
    for block in candidate_blocks:
        block_key = block.strip()
        if not block_key or block_key in seen_blocks:
            continue
        seen_blocks.add(block_key)

        # Pular linhas que contêm horário de plantão/escala (ex: "07:00 ÀS 19:00")
        if SCHEDULE_EXCLUDE_PATTERN.search(block):
            continue

        for pattern in TIME_PATTERNS:
            time_match = pattern.search(block)
            if time_match:
                hour = int(time_match.group(1))
                try:
                    minute = int(time_match.group(2))
                except (IndexError, TypeError):
                    minute = 0
                if 0 <= hour <= 23 and 0 <= minute <= 59:
                    time_components = (hour, minute)
                    break
        if time_components is not None:
            break

    if time_components is None:
        return fallback

    hour, minute = time_components

    try:
        local_dt = datetime(year, month, day, hour, minute, tzinfo=LOCAL_TIMEZONE)
    except ValueError:
        return fallback

    # Sanity check: se a data+hora extraída do texto ficou mais de 18h no
    # passado em relação ao momento atual, o remetente provavelmente esqueceu
    # de trocar a data após a meia-noite.  Ajusta +1 dia se isso resolver.
    now_local = datetime.now(LOCAL_TIMEZONE)
    drift = (now_local - local_dt).total_seconds()
    if drift > 18 * 3600:
        adjusted = local_dt + timedelta(days=1)
        if abs((now_local - adjusted).total_seconds()) < 18 * 3600:
            local_dt = adjusted

    return local_dt.astimezone(timezone.utc)


def _has_explicit_no_yellow(lines: list[str]) -> bool:
    for line in lines:
        for pattern in NO_YELLOW_PATTERNS:
            if pattern.search(line):
                normalized = _normalize_for_match(line)
                # "SEM LEITOS DE SALA AMARELA E VERMELHA DISPONÍVEIS" fala de
                # ocupação (lotada), NÃO de inexistência da sala amarela.
                # Só contar como "sem amarela" se NÃO mencionar disponibilidade
                # nem vermelha na mesma linha.
                if "disponiv" in normalized or "vermelha" in normalized or "lotad" in normalized:
                    continue
                return True
    return False


def _unit_has_fixed_no_yellow(upa_name: str | None) -> bool:
    if not upa_name:
        return False
    return any(pattern.search(upa_name) for pattern in FIXED_NO_YELLOW_PATTERNS)


def _extract_gendered_yellow_details(
    lines: list[str],
    total_room: dict[str, Any] | None = None,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None, dict[str, Any] | None]:
    male_ratio: tuple[int, int] | None = None
    female_ratio: tuple[int, int] | None = None
    male_count: int | None = None
    female_count: int | None = None

    for index, line in enumerate(lines):
        normalized = _normalize_for_match(line)
        is_adult_yellow_line = (
            ("amarela" in normalized or "observacao" in normalized)
            and "pediatri" not in normalized
            and "psiquiatr" not in normalized
            and "medicacao" not in normalized
            and "verde" not in normalized
        )

        ratio = _extract_ratio(line)
        probe_line = line
        if ratio is None and is_adult_yellow_line and index + 1 < len(lines):
            probe_line = lines[index + 1]
            ratio = _extract_ratio(probe_line)
            normalized = f"{normalized} {_normalize_for_match(probe_line)}"

        if ratio is not None:
            if "feminin" in normalized:
                female_ratio = ratio
            elif "mascul" in normalized:
                male_ratio = ratio
            continue

        count = _extract_count(line)
        if count is None and is_adult_yellow_line and index + 1 < len(lines):
            probe_line = lines[index + 1]
            count = _extract_count(probe_line)
            normalized = f"{normalized} {_normalize_for_match(probe_line)}"

        if count is None:
            continue

        if "feminin" in normalized:
            female_count = count
        elif "mascul" in normalized:
            male_count = count

    if total_room and total_room.get("capacity") is not None and total_room.get("occupied") is not None:
        total_capacity = int(total_room["capacity"])
        total_occupied = int(total_room["occupied"])
        if male_ratio is None and male_count is not None and total_capacity == total_occupied:
            male_ratio = (male_count, male_count)
        if female_ratio is None and female_count is not None and total_capacity == total_occupied:
            female_ratio = (female_count, female_count)

        if male_ratio is None and female_ratio is not None and male_count is not None and total_capacity == total_occupied:
            male_ratio = (male_count, male_count)
        if female_ratio is None and male_ratio is not None and female_count is not None and total_capacity == total_occupied:
            female_ratio = (female_count, female_count)

    male_room = _build_capacity(*male_ratio) if male_ratio else None
    female_room = _build_capacity(*female_ratio) if female_ratio else None

    if male_ratio or female_ratio:
        occupied = (male_ratio[0] if male_ratio else 0) + (female_ratio[0] if female_ratio else 0)
        capacity = (male_ratio[1] if male_ratio else 0) + (female_ratio[1] if female_ratio else 0)
        return male_room, female_room, _build_capacity(occupied, capacity)

    return None, None, None


def _extract_gendered_yellow_from_all_lines(lines: list[str]) -> dict[str, Any] | None:
    _, _, total_room = _extract_gendered_yellow_details(lines)
    return total_room


def _extract_yellow_room(lines: list[str], upa_name: str | None = None) -> tuple[dict[str, Any] | None, dict[str, Any] | None, dict[str, Any] | None]:
    if _has_explicit_no_yellow(lines) or _unit_has_fixed_no_yellow(upa_name):
        return None, None, None

    global_male_room, global_female_room, global_gendered_match = _extract_gendered_yellow_details(lines)
    yellow_index = _find_section_index(lines, "sala", "amarela", exclude_terms=("atualizacao", "vermelha"))
    if yellow_index is None:
        yellow_index = _find_line_index(lines, "sala", "amarela")
    if yellow_index is None:
        fallback_room = _extract_yellow_room_fallback(lines)
        if fallback_room:
            global_male_room, global_female_room, global_gendered_match = _extract_gendered_yellow_details(lines, fallback_room)
        return global_male_room, global_female_room, global_gendered_match or fallback_room

    current_line_ratio = _extract_ratio(lines[yellow_index])
    if current_line_ratio is not None:
        total_room = _build_capacity(*current_line_ratio)
        global_male_room, global_female_room, global_gendered_match = _extract_gendered_yellow_details(lines, total_room)
        return global_male_room, global_female_room, global_gendered_match or total_room

    male_ratio: tuple[int, int] | None = None
    female_ratio: tuple[int, int] | None = None

    for line in _collect_section_lines(lines, yellow_index):
        normalized = _normalize_for_match(line)
        ratio = _extract_ratio(line)
        if ratio is None:
            continue
        if "mascul" in normalized:
            male_ratio = ratio
        elif "feminin" in normalized:
            female_ratio = ratio

    if male_ratio or female_ratio:
        occupied = (male_ratio[0] if male_ratio else 0) + (female_ratio[0] if female_ratio else 0)
        capacity = (male_ratio[1] if male_ratio else 0) + (female_ratio[1] if female_ratio else 0)
        return (
            _build_capacity(*male_ratio) if male_ratio else global_male_room,
            _build_capacity(*female_ratio) if female_ratio else global_female_room,
            _build_capacity(occupied, capacity),
        )

    return global_male_room, global_female_room, global_gendered_match or _extract_yellow_room_fallback(lines)


def _extract_yellow_room_fallback(lines: list[str]) -> dict[str, Any] | None:
    fallback_groups = [
        ("sala", "observacao"),
        ("observacao",),
    ]

    for needles in fallback_groups:
        line_index = _find_line_index(lines, *needles)
        if line_index is None:
            continue

        ratio = _extract_ratio(lines[line_index])
        if ratio is not None:
            return _build_capacity(*ratio)

        male_ratio: tuple[int, int] | None = None
        female_ratio: tuple[int, int] | None = None
        generic_ratio: tuple[int, int] | None = None

        for line in _collect_section_lines(lines, line_index):
            normalized = _normalize_for_match(line)
            ratio = _extract_ratio(line)
            if ratio is None:
                continue
            if "mascul" in normalized:
                male_ratio = ratio
            elif "feminin" in normalized:
                female_ratio = ratio
            elif generic_ratio is None:
                generic_ratio = ratio

        if male_ratio or female_ratio:
            occupied = (male_ratio[0] if male_ratio else 0) + (female_ratio[0] if female_ratio else 0)
            capacity = (male_ratio[1] if male_ratio else 0) + (female_ratio[1] if female_ratio else 0)
            return _build_capacity(occupied, capacity)

        if generic_ratio is not None:
            return _build_capacity(*generic_ratio)

    return None


def _extract_other_beds(lines: list[str]) -> list[dict[str, Any]]:
    sections = [
        (("internamento pediatria", "internamento pediatrico", "leitos pediatricos", "pediatria"), "internamento pediatria", "other_pediatria"),
        (("internamento",), "internamento", "other_internamento"),
        (("sala verde", "verde"), "internamento", "other_verde"),
        (("medicacao",), "medicação", "other_medicacao"),
        (("extra",), "extra", "other_extra"),
    ]
    extracted: dict[str, dict[str, Any]] = {}

    for index, line in enumerate(lines):
        normalized = _normalize_for_match(line)
        if "amarela" in normalized or "isolamento" in normalized:
            continue

        for needles, label, key in sections:
            if not any(needle in normalized for needle in needles):
                continue

            if label == "internamento pediatria" and "atendimento" in normalized:
                continue

            ratio = _extract_ratio(line)
            if ratio is None and index + 1 < len(lines):
                ratio = _extract_ratio(lines[index + 1])
            if ratio is None:
                continue

            existing = extracted.get(key)
            room = {
                "key": key,
                "label": label,
                **_build_capacity(*ratio),
            }
            if existing is None or room["capacity"] > existing["capacity"]:
                extracted[key] = room
            break

    return list(extracted.values())


def _extract_isolation_rooms(lines: list[str]) -> tuple[dict[str, Any] | None, dict[str, Any] | None, dict[str, Any] | None, dict[str, Any] | None]:
    isolation_index = _find_line_index(lines, "isolamento")
    if isolation_index is None:
        isolation_index = _find_line_index(lines, "isolamentos")
    if isolation_index is None:
        return None, None, None, None

    total_ratio = _extract_ratio(lines[isolation_index])
    male_ratio: tuple[int, int] | None = None
    female_ratio: tuple[int, int] | None = None
    pediatric_ratio: tuple[int, int] | None = None

    for line in _collect_section_lines(lines, isolation_index):
        normalized = _normalize_for_match(line)
        ratio = _extract_ratio(line)
        if ratio is None:
            continue
        if "mascul" in normalized:
            male_ratio = ratio
        elif "feminin" in normalized:
            female_ratio = ratio
        elif "pediatri" in normalized:
            pediatric_ratio = ratio
        elif total_ratio is None:
            total_ratio = ratio

    return (
        _build_capacity(*male_ratio) if male_ratio else None,
        _build_capacity(*female_ratio) if female_ratio else None,
        _build_capacity(*pediatric_ratio) if pediatric_ratio else None,
        _build_capacity(*total_ratio) if total_ratio else None,
    )


def _extract_corridor_block(text: str) -> list[str]:
    lines = text.splitlines()
    collecting = False
    collected: list[str] = []

    for raw_line in lines:
        line = raw_line.strip()
        if not line:
            if collecting:
                break
            continue

        if not collecting and CORRIDOR_HEADER_PATTERN.search(line):
            collecting = True
            tail = CORRIDOR_HEADER_PATTERN.sub("", line).strip(" :-")
            if tail:
                collected.append(tail)
            continue

        if collecting:
            if SECTION_BREAK_PATTERN.search(line):
                break
            collected.append(line)

    return collected


def _sanitize_corridor_line(line: str) -> str | None:
    cleaned = LEADING_NOISE_PATTERN.sub("", line)
    cleaned = INITIALS_PATTERN.sub("", cleaned)
    cleaned = AGE_PATTERN.sub("", cleaned)
    cleaned = re.sub(r"\b(?:M|F)\b", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s{2,}", " ", cleaned).strip(" -:;,.")

    if not cleaned:
        return None

    if " - " in cleaned:
        cleaned = cleaned.split(" - ", 1)[-1].strip()
    elif " – " in cleaned:
        cleaned = cleaned.split(" – ", 1)[-1].strip()
    elif " — " in cleaned:
        cleaned = cleaned.split(" — ", 1)[-1].strip()

    return cleaned or None


def _extract_corridor_patients(text: str) -> list[dict[str, str]]:
    patients: list[dict[str, str]] = []
    for line in _extract_corridor_block(text):
        diagnosis = _sanitize_corridor_line(line)
        if diagnosis:
            patients.append({"diagnosis": diagnosis})
    return patients


def _extract_specialists(text: str) -> dict[str, bool]:
    result: dict[str, bool] = {}
    lines = text.splitlines()

    for field_name, rules in SPECIALIST_RULES.items():
        has_available = any(pattern.search(text) for pattern in rules["available"])
        has_unavailable = any(pattern.search(text) for pattern in rules["unavailable"])

        for line in lines:
            normalized_line = _normalize_for_match(line)
            if not any(keyword in normalized_line for keyword in rules.get("keywords", [])):
                continue
            if "❌" in line or " x " in f" {normalized_line} ":
                has_unavailable = True
            if "✅" in line:
                has_available = True

        result[field_name] = has_available and not has_unavailable

    return result


def parse_whatsapp_message(text: str) -> dict[str, Any]:
    lines = text.splitlines()
    upa_name = _extract_upa_name(text)
    red_room = _extract_red_room(lines)
    yellow_male, yellow_female, yellow_room = _extract_yellow_room(lines, upa_name)
    isolation_male, isolation_female, isolation_pediatric, isolation_total = _extract_isolation_rooms(lines)
    other_beds = _extract_other_beds(lines)
    corridor_patients = _extract_corridor_patients(text)
    specialists = _extract_specialists(text)
    isolation_mode = "split" if any([isolation_male, isolation_female, isolation_pediatric]) else ("unisex" if isolation_total else None)
    reported_at = _extract_reported_datetime(text)

    is_critical = bool(red_room and not red_room["has_capacity"])

    warnings: list[str] = []
    if not upa_name:
        warnings.append("Nome da UPA não identificado no payload.")
    if not red_room:
        warnings.append("Capacidade da Sala Vermelha não identificada.")
    if not yellow_room and not _unit_has_fixed_no_yellow(upa_name) and not _has_explicit_no_yellow(lines):
        warnings.append("Capacidade da Sala Amarela não identificada.")
    if not reported_at:
        warnings.append("Horário oficial não identificado no payload; usando horário de recebimento se houver.")

    return {
        "upa_name": upa_name,
        "is_critical": is_critical,
        "reported_at": reported_at.isoformat() if reported_at else None,
        "rooms": {
            "red_room": red_room,
            "yellow_room": yellow_room,
            "yellow_male": yellow_male,
            "yellow_female": yellow_female,
            "isolation_male": isolation_male,
            "isolation_female": isolation_female,
            "isolation_pediatric": isolation_pediatric,
            "isolation_total": isolation_total,
            "isolation_mode": isolation_mode,
            "other_beds": other_beds,
        },
        "corridor_patients": corridor_patients,
        "specialists": specialists,
        "parsed_at": datetime.now(timezone.utc).isoformat(),
        "warnings": warnings,
        "raw_text": text,
    }
