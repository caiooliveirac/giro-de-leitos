from __future__ import annotations

import re
import unicodedata
from difflib import SequenceMatcher
from typing import Any

UNIT_REGISTRY: list[dict[str, Any]] = [
    {
        "code": "upa_bairro_da_paz_orlando_imbassahy",
        "canonical_name": "UPA BAIRRO DA PAZ - ORLANDO IMBASSAHY",
        "aliases": [
            "UPA BAIRRO DA PAZ - ORLANDO IMBASSAHY",
            "UPA BAIRRO DA PAZ",
            "BAIRRO DA PAZ",
            "PA DR ORLANDO IMBASSAHY",
            "DR ORLANDO IMBASSAHY",
            "ORLANDO IMBASSAHY",
            "ORLANDO IMBASSAHY UPA",
        ],
    },
    {
        "code": "upa_barris",
        "canonical_name": "UPA BARRIS",
        "aliases": ["UPA BARRIS", "BARRIS"],
    },
    {
        "code": "upa_brotas",
        "canonical_name": "UPA DE BROTAS",
        "aliases": ["UPA DE BROTAS", "UPA BROTAS", "BROTAS"],
    },
    {
        "code": "upa_helio_machado",
        "canonical_name": "UPA HELIO MACHADO",
        "aliases": ["UPA HELIO MACHADO", "HELIO MACHADO", "HÉLIO MACHADO"],
    },
    {
        "code": "upa_paripe",
        "canonical_name": "UPA PARIPE",
        "aliases": ["UPA PARIPE", "PARIPE"],
    },
    {
        "code": "upa_periperi",
        "canonical_name": "UPA PERIPERI",
        "aliases": ["UPA PERIPERI", "PERIPERI", "UPA ADROALDO ALBERGARIA", "ADROALDO ALBERGARIA", "DR ADROALDO ALBERGARIA"],
    },
    {
        "code": "pa_pernambues",
        "canonical_name": "PA PERNAMBUÉS",
        "aliases": [
            "PA PERNAMBUÉS",
            "PA PERNAMBUES",
            "PERNAMBUÉS",
            "PERNAMBUES",
            "EDSON TEIXEIRA BARBOSA",
            "PA PERNAMBUÉS - EDSON TEIXEIRA BARBOSA",
            "PA PERNAMBUES - EDSON TEIXEIRA BARBOSA",
        ],
    },
    {
        "code": "upa_piraja_santo_inacio",
        "canonical_name": "UPA PIRAJA SANTO INÁCIO",
        "aliases": ["UPA PIRAJA SANTO INÁCIO", "UPA PIRAJA SANTO INACIO", "PIRAJA SANTO INÁCIO", "PIRAJA SANTO INACIO", "PIRAJA SANTO INACIO"],
    },
    {
        "code": "upa_san_martin",
        "canonical_name": "UPA SAN MARTIN",
        "aliases": ["UPA SAN MARTIN", "SAN MARTIN"],
    },
    {
        "code": "upa_santo_antonio",
        "canonical_name": "UPA SANTO ANTÔNIO",
        "aliases": ["UPA SANTO ANTÔNIO", "UPA SANTO ANTONIO", "SANTO ANTÔNIO", "SANTO ANTONIO"],
    },
    {
        "code": "upa_parque_sao_cristovao",
        "canonical_name": "UPA PARQUE SÃO CRISTOVÃO",
        "aliases": ["UPA PARQUE SÃO CRISTOVÃO", "UPA PARQUE SAO CRISTOVAO", "PARQUE SÃO CRISTOVÃO", "PARQUE SAO CRISTOVAO"],
    },
    {
        "code": "pa_sao_marcos",
        "canonical_name": "PA SÃO MARCOS",
        "aliases": ["PA SÃO MARCOS", "PA SAO MARCOS", "SÃO MARCOS", "SAO MARCOS"],
    },
    {
        "code": "pa_tancredo_neves_rodrigo_argolo",
        "canonical_name": "PA TANCREDO NEVES - RODRIGO ARGOLO",
        "aliases": ["PA TANCREDO NEVES - RODRIGO ARGOLO", "PA TANCREDO NEVES", "TANCREDO NEVES", "RODRIGO ARGOLO"],
    },
    {
        "code": "upa_valeria",
        "canonical_name": "UPA VALÉRIA",
        "aliases": ["UPA VALÉRIA", "UPA VALERIA", "VALÉRIA", "VALERIA"],
    },
    {
        "code": "centro_marback_alfredo_bureau",
        "canonical_name": "12º CENTRO MARBACK - ALFREDO BUREAU",
        "aliases": ["12º CENTRO MARBACK - ALFREDO BUREAU", "12 CENTRO MARBACK - ALFREDO BUREAU", "PA ALFREDO BUREAU", "ALFREDO BUREAU", "MARBACK"],
    },
    {
        "code": "centro_maria_conceicao_santiago_imbassahy",
        "canonical_name": "16º CENTRO MARIA CONCEIÇÃO SANTIAGO IMBASSAHY",
        "aliases": ["16º CENTRO MARIA CONCEIÇÃO SANTIAGO IMBASSAHY", "16 CENTRO MARIA CONCEICAO SANTIAGO IMBASSAHY", "MARIA CONCEIÇÃO SANTIAGO IMBASSAHY", "MARIA CONCEICAO SANTIAGO IMBASSAHY", "SANTIAGO IMBASSAHY"],
    },
]


def normalize_unit_text(value: str | None) -> str:
    if not value:
        return ""
    normalized = unicodedata.normalize("NFKD", value)
    normalized = "".join(ch for ch in normalized if not unicodedata.combining(ch))
    normalized = normalized.casefold()
    normalized = re.sub(r"[^a-z0-9\s]", " ", normalized)
    normalized = re.sub(r"\s+", " ", normalized).strip()
    return normalized


def seed_units() -> list[dict[str, Any]]:
    seeded: list[dict[str, Any]] = []
    for unit in UNIT_REGISTRY:
        aliases = [alias.strip() for alias in unit["aliases"] if alias.strip()]
        seeded.append(
            {
                "code": unit["code"],
                "canonical_name": unit["canonical_name"],
                "aliases": aliases,
            }
        )
    return seeded


def resolve_unit_name(candidate: str | None) -> dict[str, Any] | None:
    normalized_candidate = normalize_unit_text(candidate)
    if not normalized_candidate:
        return None

    best_match: dict[str, Any] | None = None
    best_score = 0.0

    for unit in UNIT_REGISTRY:
        for alias in unit["aliases"]:
            normalized_alias = normalize_unit_text(alias)
            if not normalized_alias:
                continue

            if normalized_candidate == normalized_alias:
                return {
                    "unit_code": unit["code"],
                    "canonical_name": unit["canonical_name"],
                    "matched_alias": alias,
                    "confidence": 1.0,
                    "method": "exact",
                }

            token_overlap = 0.0
            alias_tokens = set(normalized_alias.split())
            candidate_tokens = set(normalized_candidate.split())
            if alias_tokens:
                token_overlap = len(alias_tokens & candidate_tokens) / len(alias_tokens)

            contains_bonus = 0.0
            if normalized_alias in normalized_candidate or normalized_candidate in normalized_alias:
                contains_bonus = 0.2

            ratio = SequenceMatcher(None, normalized_candidate, normalized_alias).ratio()
            score = max(ratio, min(1.0, token_overlap + contains_bonus))

            if score > best_score:
                best_score = score
                best_match = {
                    "unit_code": unit["code"],
                    "canonical_name": unit["canonical_name"],
                    "matched_alias": alias,
                    "confidence": round(score, 4),
                    "method": "fuzzy",
                }

    if best_match and best_score >= 0.74:
        return best_match
    return None


def resolve_unit_from_text(text: str | None) -> dict[str, Any] | None:
    normalized_text = normalize_unit_text(text)
    if not normalized_text:
        return None

    for unit in UNIT_REGISTRY:
        for alias in unit["aliases"]:
            normalized_alias = normalize_unit_text(alias)
            if normalized_alias and normalized_alias in normalized_text:
                return {
                    "unit_code": unit["code"],
                    "canonical_name": unit["canonical_name"],
                    "matched_alias": alias,
                    "confidence": 0.96,
                    "method": "substring",
                }

    non_empty_lines = [line.strip() for line in (text or "").splitlines() if line.strip()]
    candidate_block = " ".join(non_empty_lines[:3])
    return resolve_unit_name(candidate_block)
