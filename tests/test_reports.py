from __future__ import annotations

import os
import re
import unittest
from datetime import datetime, timedelta, timezone

os.environ.setdefault("JWT_SECRET", "test-secret-reports")
os.environ.setdefault("CPF_ENCRYPTION_KEY", "OmaP3i0nC2P9MwJv5wDhlb0aBpfNn5Y73I9c8wL2cIc=")
os.environ.setdefault("CPF_HASH_PEPPER", "test-pepper")

from fastapi.testclient import TestClient  # noqa: E402

import main  # noqa: E402
from main import build_priority_buckets, build_system_summary_text, build_telegram_help_reply, is_report_chat_authorized
from reports import (
    GIRO_SLA,
    MAX_COBRANCA_SNIPPETS,
    MESSAGE_CHUNK_LIMIT,
    build_compliance_messages,
    build_over_capacity_report_text,
    build_unparsed_report_text,
    build_vacancy_report_text,
    classify_gap,
    compute_gap_ranking,
    compute_over_capacity_ranking,
    fmt_duration,
    fmt_local,
    parse_bot_command,
    parse_days_arg,
    redact_line,
    shift_split_hours,
    short_unit_name,
    split_message,
    traffic_light,
)
from units import UNIT_REGISTRY


NOW = datetime(2026, 8, 3, 18, 32, tzinfo=timezone.utc)  # 15:32 em America/Sao_Paulo
_LOCAL_TZ = timezone(timedelta(hours=-3))  # America/Sao_Paulo não tem horário de verão


def _local(day: int, hour: int, minute: int = 0) -> datetime:
    """Instante escrito em hora LOCAL — é como o SLA por turno é definido."""
    return datetime(2026, 8, day, hour, minute, tzinfo=_LOCAL_TZ)


def _giros(unit_code: str, *moments: tuple) -> list[dict]:
    """Giros de uma unidade a partir de horários locais (dia, hora[, minuto])."""
    return [{"unit_code": unit_code, "created_at": _local(*moment)} for moment in moments]


def _everyone_else_is_up_to_date(**last_by_unit: datetime) -> dict:
    """Rede inteira com giro recente, menos as unidades passadas explicitamente.

    Sem isso toda unidade do registro entraria como "nunca postou" e viraria
    ruído em cima do caso sob teste.
    """
    baseline = {unit["code"]: NOW - timedelta(hours=1) for unit in UNIT_REGISTRY}
    baseline.update(last_by_unit)
    return baseline


def _room(occupied: int, capacity: int) -> dict:
    return {
        "ratio": f"{occupied:02d}/{capacity:02d}",
        "occupied": occupied,
        "capacity": capacity,
        "has_capacity": capacity > occupied,
        "is_over_capacity": occupied > capacity,
    }


def _status_row(
    unit_code: str,
    canonical_name: str,
    *,
    rooms: dict | None = None,
    age_hours: float | None = 1.0,
    has_orthopedist: bool = False,
) -> dict:
    updated_at = None if age_hours is None else NOW - timedelta(hours=age_hours)
    return {
        "unit_key": unit_code,
        "unit_code": unit_code,
        "canonical_name": canonical_name,
        "displayed_name": canonical_name,
        "source": "test",
        "received_at": updated_at,
        "updated_at": updated_at,
        "has_orthopedist": has_orthopedist,
        "has_surgeon": False,
        "has_psychiatrist": False,
        "payload": {"data": {"upa_name": canonical_name, "rooms": rooms or {}}},
    }


def _full_network_rows() -> list[dict]:
    """As 16 unidades cadastradas, todas com sala vermelha lotada."""
    return [
        _status_row(unit["code"], unit["canonical_name"], rooms={"red_room": _room(4, 4)})
        for unit in UNIT_REGISTRY
    ]


class MessageSplittingTests(unittest.TestCase):
    def test_short_message_is_not_split(self) -> None:
        self.assertEqual(split_message("linha única"), ["linha única"])

    def test_long_message_is_split_into_parts_within_limit(self) -> None:
        text = "\n".join(f"linha {index} " + "x" * 80 for index in range(200))
        parts = split_message(text)
        self.assertGreater(len(parts), 1)
        for part in parts:
            self.assertLessEqual(len(part), MESSAGE_CHUNK_LIMIT + 20)  # +prefixo de parte

    def test_split_preserves_content_and_marks_parts(self) -> None:
        text = "\n".join(f"linha {index}" for index in range(600))
        parts = split_message(text)
        self.assertTrue(parts[0].startswith("▸ parte 1/"))
        joined = "\n".join(part.split("\n", 1)[1] for part in parts)
        self.assertIn("linha 0", joined)
        self.assertIn("linha 599", joined)

    def test_single_line_longer_than_limit_is_hard_cut(self) -> None:
        parts = split_message("y" * (MESSAGE_CHUNK_LIMIT * 2 + 10))
        self.assertGreaterEqual(len(parts), 2)

    def test_excess_parts_are_truncated_with_visible_notice(self) -> None:
        text = "\n".join("z" * 100 for _ in range(1000))
        parts = split_message(text)
        self.assertEqual(len(parts), 4)
        self.assertIn("mensagem cortada", parts[-1])

    def test_current_resumo_fits_after_chunking(self) -> None:
        """Regressão do bug de produção: /resumo com 16 unidades passa de 4.096."""
        summary = build_system_summary_text(_full_network_rows())
        self.assertGreater(len(summary), 3000)
        parts = split_message(summary)
        for part in parts:
            self.assertLess(len(part), 4096)


class CommandParsingTests(unittest.TestCase):
    def test_plain_command(self) -> None:
        self.assertEqual(parse_bot_command("/vagas"), ("vagas", ""))

    def test_command_with_bot_suffix(self) -> None:
        self.assertEqual(parse_bot_command("/vagas@girodeleitos_bot"), ("vagas", ""))

    def test_command_is_case_insensitive_and_keeps_args(self) -> None:
        self.assertEqual(parse_bot_command("/VAGAS Amarela"), ("vagas", "amarela"))

    def test_command_with_suffix_and_args(self) -> None:
        self.assertEqual(parse_bot_command("/cobranca@girodeleitos_bot 7d"), ("cobranca", "7d"))

    def test_resumo_with_bot_suffix_regression(self) -> None:
        """Em grupo o Telegram entrega /resumo@bot — antes caía na ajuda."""
        self.assertEqual(parse_bot_command("/resumo@girodeleitos_bot"), ("resumo", ""))

    def test_free_text_is_not_a_command(self) -> None:
        self.assertIsNone(parse_bot_command("UNIDADE: UPA VALÉRIA"))
        self.assertIsNone(parse_bot_command(""))

    def test_bare_slash_still_falls_into_help(self) -> None:
        self.assertEqual(parse_bot_command("/"), ("", ""))

    def test_days_argument_parsing(self) -> None:
        self.assertEqual(parse_days_arg(""), 7)
        self.assertEqual(parse_days_arg("30d"), 30)
        self.assertEqual(parse_days_arg("15"), 15)
        self.assertEqual(parse_days_arg("hoje"), 1)
        self.assertEqual(parse_days_arg("mes"), 30)
        self.assertEqual(parse_days_arg("9999"), 90)
        self.assertEqual(parse_days_arg("qualquer coisa"), 7)


class FormattingTests(unittest.TestCase):
    def test_timestamps_render_in_sao_paulo(self) -> None:
        self.assertEqual(fmt_local(NOW), "03/08 15:32")

    def test_duration_formats(self) -> None:
        self.assertEqual(fmt_duration(0.75), "45min")
        self.assertEqual(fmt_duration(10.8), "10h48")
        self.assertEqual(fmt_duration(50), "2d 2h")
        self.assertEqual(fmt_duration(None), "—")

    def test_traffic_light_thresholds(self) -> None:
        self.assertEqual(traffic_light(1.0), "🟢")
        self.assertEqual(traffic_light(7.0), "🟡")
        self.assertEqual(traffic_light(11.0), "🔴")
        self.assertEqual(traffic_light(None), "🔴")

    def test_short_unit_names_fit_the_screen(self) -> None:
        for unit in UNIT_REGISTRY:
            self.assertLessEqual(len(short_unit_name(unit["code"])), 24)

    def test_short_name_resolves_from_canonical_name_alone(self) -> None:
        self.assertEqual(short_unit_name(None, "UPA PARQUE SÃO CRISTOVÃO"), "UPA P. SÃO CRISTÓVÃO")
        self.assertEqual(short_unit_name(None, "PA TANCREDO NEVES - RODRIGO ARGOLO"), "PA TANCREDO NEVES")

    def test_redaction_masks_long_digit_runs(self) -> None:
        self.assertNotIn("071993731970", redact_line("Ligar 071993731970 agora"))
        self.assertIn("04/07", redact_line("SALA VERMELHA 04/07"))


class VacancyReportTests(unittest.TestCase):
    def _buckets_and_rows(self, rows: list[dict]) -> tuple[dict, list[dict]]:
        return build_priority_buckets(rows), rows

    def test_no_red_vacancy_states_it_and_lists_over_capacity(self) -> None:
        rows = _full_network_rows()
        rows[0]["payload"]["data"]["rooms"]["red_room"] = _room(4, 2)  # acima da capacidade
        buckets, rows = self._buckets_and_rows(rows)
        text = build_vacancy_report_text(buckets, rows, now=NOW)
        self.assertIn("sem vaga na rede", text)
        self.assertIn("ACIMA da capacidade", text)
        self.assertIn("04/02", text)

    def test_yellow_and_isolation_vacancies_appear(self) -> None:
        rows = _full_network_rows()
        rows[1]["payload"]["data"]["rooms"].update(
            {
                "yellow_male": _room(4, 7),
                "yellow_female": _room(4, 6),
                "isolation_mode": "split",
                "isolation_female": _room(0, 1),
            }
        )
        buckets, rows = self._buckets_and_rows(rows)
        text = build_vacancy_report_text(buckets, rows, now=NOW)
        self.assertIn("🟡 AMARELA — 5 vaga(s)", text)
        self.assertIn("🦠 ISOLAMENTO ADULTO — 1 vaga(s)", text)

    def test_pediatric_isolation_is_excluded(self) -> None:
        rows = _full_network_rows()
        rows[2]["payload"]["data"]["rooms"].update(
            {
                "isolation_mode": "split",
                "isolation_pediatric": _room(0, 4),
            }
        )
        buckets, rows = self._buckets_and_rows(rows)
        text = build_vacancy_report_text(buckets, rows, now=NOW)
        self.assertIn("🦠 ISOLAMENTO ADULTO — sem vaga na rede", text)

    def test_pediatric_other_beds_are_excluded(self) -> None:
        rows = _full_network_rows()
        rows[3]["payload"]["data"]["rooms"]["other_beds"] = [
            {**_room(0, 6), "label": "internamento pediatria"}
        ]
        buckets, rows = self._buckets_and_rows(rows)
        text = build_vacancy_report_text(buckets, rows, now=NOW)
        self.assertIn("🏥 INTERNAMENTO / EXTRA — sem vaga na rede", text)

    def test_stale_unit_is_flagged_with_age(self) -> None:
        rows = _full_network_rows()
        rows[4]["age_hours"] = None
        rows[4]["updated_at"] = NOW - timedelta(hours=10.8)
        rows[4]["received_at"] = rows[4]["updated_at"]
        buckets, rows = self._buckets_and_rows(rows)
        text = build_vacancy_report_text(buckets, rows, now=NOW)
        self.assertIn("Dado velho", text)
        self.assertIn("10h48", text)

    def test_unit_that_never_posted_says_so_instead_of_999h(self) -> None:
        rows = _full_network_rows()
        rows[5]["updated_at"] = None
        rows[5]["received_at"] = None
        buckets, rows = self._buckets_and_rows(rows)
        text = build_vacancy_report_text(buckets, rows, now=NOW)
        self.assertIn("nunca postou", text)
        self.assertNotIn("999", text)

    def test_report_fits_a_single_message_with_full_network(self) -> None:
        rows = _full_network_rows()
        for index, row in enumerate(rows):
            row["payload"]["data"]["rooms"] = {
                "red_room": _room(4, 4),
                "yellow_male": _room(index, index + 3),
                "yellow_female": _room(index, index + 2),
                "isolation_mode": "split",
                "isolation_female": _room(0, 2),
                "isolation_male": _room(0, 2),
                "other_beds": [{**_room(0, 5), "label": "internamento"}],
            }
            row["has_orthopedist"] = True
        buckets, rows = self._buckets_and_rows(rows)
        text = build_vacancy_report_text(buckets, rows, now=NOW)
        self.assertLessEqual(len(text), 4000)
        self.assertEqual(len(split_message(text)), 1)

    def test_no_patient_data_leaks_into_the_report(self) -> None:
        rows = _full_network_rows()
        rows[0]["payload"]["data"]["rooms"]["red_room"] = {
            **_room(2, 4),
            "patients": [{"sigla": "J.N.N", "age": "71a", "clinical_summary": "AVC"}],
        }
        buckets, rows = self._buckets_and_rows(rows)
        text = build_vacancy_report_text(buckets, rows, now=NOW)
        self.assertNotIn("J.N.N", text)
        self.assertNotIn("AVC", text)


class ShiftSlaTests(unittest.TestCase):
    """Turnos e limites do SLA (diurno 07–19 > 6h · noturno 19–07 > 12h)."""

    def test_sla_thresholds_live_in_one_constant(self) -> None:
        self.assertEqual(GIRO_SLA["day_start_hour"], 7)
        self.assertEqual(GIRO_SLA["day_end_hour"], 19)
        self.assertEqual(GIRO_SLA["day_max_gap_hours"], 6.0)
        self.assertEqual(GIRO_SLA["night_max_gap_hours"], 12.0)

    def test_overshoot_smaller_than_a_minute_is_not_charged(self) -> None:
        """6h00m20s sairia no texto como '6h00 acima do limite de 6h'."""
        start = _local(3, 8, 0)
        self.assertFalse(classify_gap(start, start + timedelta(hours=6, seconds=20))["violated"])
        self.assertTrue(classify_gap(start, start + timedelta(hours=6, minutes=3))["violated"])

    def test_split_adds_up_to_the_total_duration(self) -> None:
        day, night = shift_split_hours(_local(2, 16, 0), _local(3, 10, 0))
        self.assertAlmostEqual(day + night, 18.0, places=2)
        self.assertAlmostEqual(day, 3.0 + 3.0, places=2)  # 16–19 e 07–10
        self.assertAlmostEqual(night, 12.0, places=2)  # 19–07

    def test_daytime_gap_over_six_hours_is_a_violation(self) -> None:
        gap = classify_gap(_local(3, 8, 0), _local(3, 15, 0))  # 7h, todo diurno
        self.assertEqual(gap["shift"], "diurno")
        self.assertAlmostEqual(gap["hours"], 7.0, places=2)
        self.assertEqual(gap["limit_hours"], 6.0)
        self.assertTrue(gap["violated"])

    def test_daytime_gap_within_six_hours_is_clean(self) -> None:
        gap = classify_gap(_local(3, 8, 0), _local(3, 13, 30))  # 5h30
        self.assertEqual(gap["shift"], "diurno")
        self.assertFalse(gap["violated"])

    def test_nighttime_gap_gets_the_twelve_hour_limit(self) -> None:
        gap = classify_gap(_local(2, 20, 0), _local(3, 6, 0))  # 10h, todo noturno
        self.assertEqual(gap["shift"], "noturno")
        self.assertEqual(gap["limit_hours"], 12.0)
        self.assertFalse(gap["violated"])

    def test_a_full_night_is_exactly_the_limit_and_does_not_violate(self) -> None:
        """O turno noturno dura 12h: gap 100% noturno nunca estoura sozinho."""
        gap = classify_gap(_local(2, 19, 0), _local(3, 7, 0))
        self.assertEqual(gap["shift"], "noturno")
        self.assertAlmostEqual(gap["hours"], 12.0, places=2)
        self.assertFalse(gap["violated"])

    def test_crossing_gap_is_judged_by_the_predominant_shift(self) -> None:
        """16:00 → 23:00: 3h diurnas, 4h noturnas. Vale o limite noturno."""
        gap = classify_gap(_local(3, 16, 0), _local(3, 23, 0))
        self.assertEqual(gap["shift"], "noturno")
        self.assertAlmostEqual(gap["day_hours"], 3.0, places=2)
        self.assertAlmostEqual(gap["night_hours"], 4.0, places=2)
        self.assertFalse(gap["violated"])  # 7h < 12h — como diurno teria sido violação

    def test_crossing_gap_mostly_diurnal_violates_at_six_hours(self) -> None:
        """05:00 → 14:00: 2h noturnas, 7h diurnas. Vale o limite diurno."""
        gap = classify_gap(_local(3, 5, 0), _local(3, 14, 0))
        self.assertEqual(gap["shift"], "diurno")
        self.assertTrue(gap["violated"])

    def test_crossing_gap_over_the_night_limit_violates_once(self) -> None:
        """19:00 → 08:00 = 13h (12 noturnas + 1 diurna): uma violação noturna."""
        gap = classify_gap(_local(2, 19, 0), _local(3, 8, 0))
        self.assertEqual(gap["shift"], "noturno")
        self.assertAlmostEqual(gap["hours"], 13.0, places=2)
        self.assertTrue(gap["violated"])

    def test_exact_tie_between_shifts_favours_the_night_limit(self) -> None:
        """13:00 → 01:00: 6h em cada turno. Empate vai para o mais tolerante."""
        gap = classify_gap(_local(3, 13, 0), _local(4, 1, 0))
        self.assertEqual(gap["shift"], "noturno")
        self.assertFalse(gap["violated"])


class GapRankingTests(unittest.TestCase):
    def test_gap_between_two_giros(self) -> None:
        events = [
            {"unit_code": "upa_barris", "created_at": NOW - timedelta(hours=5)},
            {"unit_code": "upa_barris", "created_at": NOW - timedelta(hours=2)},
        ]
        ranking = compute_gap_ranking(events, {"upa_barris": NOW - timedelta(hours=2)}, NOW, 7)
        barris = next(item for item in ranking if item["unit_code"] == "upa_barris")
        self.assertAlmostEqual(barris["worst_gap_hours"], 3.0, places=2)
        self.assertAlmostEqual(barris["current_gap_hours"], 2.0, places=2)
        self.assertEqual(barris["giros"], 2)

    def test_resends_within_five_minutes_count_as_one(self) -> None:
        events = [
            {"unit_code": "upa_barris", "created_at": NOW - timedelta(hours=4)},
            {"unit_code": "upa_barris", "created_at": NOW - timedelta(hours=4) + timedelta(minutes=2)},
            {"unit_code": "upa_barris", "created_at": NOW - timedelta(hours=1)},
        ]
        ranking = compute_gap_ranking(events, {}, NOW, 7)
        barris = next(item for item in ranking if item["unit_code"] == "upa_barris")
        self.assertEqual(barris["giros"], 2)
        self.assertAlmostEqual(barris["worst_gap_hours"], 3.0, places=2)

    def test_unit_without_giros_does_not_divide_by_zero(self) -> None:
        ranking = compute_gap_ranking([], {}, NOW, 7)
        self.assertEqual(len(ranking), len(UNIT_REGISTRY))
        for item in ranking:
            self.assertIsNone(item["avg_gap_hours"])
            self.assertIsNone(item["current_gap_hours"])
            self.assertTrue(item["never_posted"])
            self.assertEqual(item["violations"], [])

    def test_violations_list_date_start_duration_and_shift(self) -> None:
        events = _giros("upa_barris", (3, 8, 0), (3, 15, 0))  # 7h diurnas
        ranking = compute_gap_ranking(events, {"upa_barris": _local(3, 15, 0)}, NOW, 7)
        barris = next(item for item in ranking if item["unit_code"] == "upa_barris")
        self.assertEqual(barris["violation_count"], 1)
        violation = barris["violations"][0]
        self.assertEqual(violation["start"], _local(3, 8, 0))
        self.assertAlmostEqual(violation["hours"], 7.0, places=2)
        self.assertEqual(violation["shift"], "diurno")
        self.assertFalse(violation["ongoing"])

    def test_open_silence_counts_as_one_ongoing_violation(self) -> None:
        events = _giros("upa_barris", (3, 5, 0), (3, 6, 0))  # gap de 1h, sem violação
        ranking = compute_gap_ranking(events, {"upa_barris": _local(3, 6, 0)}, NOW, 7)
        barris = next(item for item in ranking if item["unit_code"] == "upa_barris")
        self.assertEqual(barris["violation_count"], 1)
        self.assertTrue(barris["violations"][0]["ongoing"])
        self.assertAlmostEqual(barris["violations"][0]["hours"], 9.533, places=2)

    def test_the_same_silence_is_never_counted_twice(self) -> None:
        """Gap fechado 08→15 e gap em curso 15→agora: 2 violações distintas."""
        events = _giros("upa_barris", (3, 8, 0), (3, 15, 0))
        ranking = compute_gap_ranking(events, {"upa_barris": _local(3, 15, 0)}, NOW, 7)
        barris = next(item for item in ranking if item["unit_code"] == "upa_barris")
        # 15:00 → 15:32 ainda não estoura: segue só a violação fechada.
        self.assertEqual(barris["violation_count"], 1)
        total = sum(item["hours"] for item in barris["violations"])
        self.assertAlmostEqual(total, 7.0, places=2)

    def test_ranking_is_sorted_by_average_gap_desc(self) -> None:
        events = []
        for hours, code in ((9, "upa_santo_antonio"), (3, "upa_san_martin")):
            base = NOW - timedelta(hours=hours * 3)
            for step in range(4):
                events.append({"unit_code": code, "created_at": base + timedelta(hours=hours * step)})
        ranking = [item for item in compute_gap_ranking(events, {}, NOW, 7) if item["giros"] > 1]
        self.assertEqual(ranking[0]["unit_code"], "upa_santo_antonio")
        self.assertEqual(ranking[1]["unit_code"], "upa_san_martin")


class ComplianceReportTests(unittest.TestCase):
    """Os balões do /cobranca. UPA STO. ANTÔNIO viola, UPA SAN MARTIN não."""

    COORDINATORS = {
        "upa_santo_antonio": ["JOILSON GUIMARAES", "Fernanda Magalhães"],
        "upa_san_martin": ["Marcia Souza"],
    }

    def _ranking(self) -> list[dict]:
        events = [
            # Sto. Antônio: 8h diurnas (violação) + 14h atravessando a noite
            # (violação noturna) + silêncio em curso desde 06:00 (violação).
            *_giros("upa_santo_antonio", (2, 8, 0), (2, 16, 0), (3, 6, 0)),
            # San Martin: giros de 3 em 3 horas no diurno — sempre dentro do SLA.
            *_giros("upa_san_martin", (3, 9, 0), (3, 12, 0), (3, 15, 0)),
        ]
        last = _everyone_else_is_up_to_date(
            upa_santo_antonio=_local(3, 6, 0),
            upa_san_martin=_local(3, 15, 0),
        )
        return compute_gap_ranking(events, last, NOW, 7)

    def _messages(self, responsibles: dict | None = None) -> list[str]:
        return build_compliance_messages(
            self._ranking(),
            self.COORDINATORS if responsibles is None else responsibles,
            NOW,
            7,
        )

    def test_first_balloon_ranks_by_time_without_posting(self) -> None:
        header = self._messages()[0]
        self.assertIn("RANKING — MAIS TEMPO SEM POSTAR", header)
        self.assertIn("UPA STO. ANTÔNIO", header)
        self.assertIn("SEM GIRO AGORA há 9h32", header)  # gap atual: 06:00 -> 15:32
        self.assertIn("3 violações no período", header)
        self.assertIn("JOILSON GUIMARAES", header)

    def test_units_silent_right_now_come_before_the_ones_already_back(self) -> None:
        """Quem está estourado agora é o problema do dia; ficha grande vem depois."""
        events = [
            # Já normalizou, mas com 2 violações fechadas no período.
            *_giros("upa_barris", (2, 8, 0), (2, 16, 0), (3, 0, 0), (3, 15, 0)),
            # Em silêncio agora, com 1 violação só.
            *_giros("upa_paripe", (3, 6, 0)),
        ]
        last = _everyone_else_is_up_to_date(
            upa_barris=_local(3, 15, 0),
            upa_paripe=_local(3, 6, 0),
        )
        ranking = compute_gap_ranking(events, last, NOW, 7)
        header = build_compliance_messages(ranking, {}, NOW, 7)[0]
        self.assertLess(header.index("UPA PARIPE"), header.index("UPA BARRIS"))
        self.assertIn("🟢 em dia agora", header)

    def test_first_balloon_states_the_sla(self) -> None:
        header = self._messages()[0]
        self.assertIn("diurno (07h–19h) até 6h", header)
        self.assertIn("noturno (19h–07h) até 12h", header)

    def test_one_balloon_per_violating_unit(self) -> None:
        messages = self._messages()
        # ranking + 1 snippet (só Sto. Antônio viola) + consolidado
        self.assertEqual(len(messages), 3)
        self.assertIn("Olá, Joilson e Fernanda!", messages[1])
        self.assertIn("UPA STO. ANTÔNIO", messages[1])

    def test_unit_within_the_sla_gets_no_balloon(self) -> None:
        messages = self._messages()
        for message in messages:
            self.assertNotIn("UPA SAN MARTIN", message)
            self.assertNotIn("Marcia", message)
        self.assertIn(f"Sem nenhuma violação na janela: {len(UNIT_REGISTRY) - 1} unidades", messages[0])

    def test_snippet_lists_every_failure_with_date_time_and_shift(self) -> None:
        snippet = self._messages()[1]
        self.assertIn("silêncio em 02/08, das 08:00 às 16:00 (8h00, turno diurno; limite 6h)", snippet)
        self.assertIn("silêncio de 02/08 16:00 a 03/08 06:00 (14h00, turno noturno; limite 12h)", snippet)
        self.assertIn("sem atualização desde 03/08 06:00 — já são 9h32 (turno diurno; limite 6h)", snippet)
        self.assertIn("registramos 3 períodos sem atualização", snippet)

    def test_snippet_is_polite_and_ready_to_paste(self) -> None:
        """Sem cabeçalho de sistema: no Telegram o copiar leva tudo junto."""
        snippet = self._messages()[1]
        self.assertTrue(snippet.startswith("Olá,"))
        self.assertIn("Consegue verificar com a equipe? Obrigado!", snippet)
        self.assertIn("O combinado é atualizar o giro a cada 6h", snippet)
        for noise in ("📉", "🏆", "━", "👤", "/cobranca"):
            self.assertNotIn(noise, snippet)

    def test_snippet_addresses_the_team_when_there_is_no_coordinator(self) -> None:
        snippet = self._messages(responsibles={})[1]
        self.assertIn("Olá, equipe da UPA STO. ANTÔNIO!", snippet)
        self.assertIn("sem coordenador cadastrado", self._messages(responsibles={})[0])

    def test_last_balloon_consolidates_every_violation(self) -> None:
        consolidated = self._messages()[-1]
        self.assertIn("CONSOLIDADO", consolidated)
        self.assertIn("3 violações em 1 unidade", consolidated)
        self.assertIn("UPA STO. ANTÔNIO", consolidated)
        self.assertIn("JOILSON GUIMARAES", consolidated)
        self.assertIn("silêncio em 02/08, das 08:00 às 16:00", consolidated)

    def test_units_that_never_posted_are_charged_too(self) -> None:
        ranking = compute_gap_ranking([], {}, NOW, 7)
        messages = build_compliance_messages(ranking, {}, NOW, 7)
        self.assertIn("nunca postou giro", messages[0])
        self.assertIn("Não localizamos nenhum giro de leitos", messages[1])
        # Balão individual é limitado; o consolidado continua trazendo todas.
        self.assertEqual(len(messages), 2 + MAX_COBRANCA_SNIPPETS)
        self.assertIn("balão individual só para as", messages[0])
        self.assertIn(f"em {len(UNIT_REGISTRY)} unidades", messages[-1])

    def test_no_violation_in_the_window_is_a_single_balloon(self) -> None:
        ranking = compute_gap_ranking([], _everyone_else_is_up_to_date(), NOW, 7)
        messages = build_compliance_messages(ranking, self.COORDINATORS, NOW, 7)
        self.assertEqual(len(messages), 1)
        self.assertIn("Nenhuma violação de SLA no período", messages[0])
        self.assertIn("Nada a cobrar", messages[0])

    def test_unparsed_warning_rides_in_the_ranking_balloon(self) -> None:
        messages = build_compliance_messages(self._ranking(), self.COORDINATORS, NOW, 7, unparsed_count=3)
        self.assertIn("3 mensagens não parseadas", messages[0])
        for message in messages[1:]:
            self.assertNotIn("não parseada", message)

    def test_no_phone_numbers_in_any_balloon(self) -> None:
        for message in self._messages():
            self.assertIsNone(re.search(r"\d{10,}", message))

    def test_every_balloon_survives_the_telegram_limit(self) -> None:
        events = []
        for unit in UNIT_REGISTRY:
            for day, hour in ((1, 8), (1, 20), (2, 9), (2, 21), (3, 6)):
                events.extend(_giros(unit["code"], (day, hour)))
        ranking = compute_gap_ranking(events, {}, NOW, 7)
        responsibles = {unit["code"]: ["Fulano de Tal Sobrenome", "Ciclana Silva"] for unit in UNIT_REGISTRY}
        messages = build_compliance_messages(ranking, responsibles, NOW, 7, unparsed_count=3)
        for message in messages:
            parts = split_message(message)
            self.assertGreaterEqual(len(parts), 1)
            for part in parts:
                # 4096 é o teto duro do sendMessage; o prefixo "▸ parte i/n"
                # é somado depois do corte em MESSAGE_CHUNK_LIMIT.
                self.assertLessEqual(len(part), 4096)
                self.assertNotIn("mensagem cortada", part)


class OverCapacityTests(unittest.TestCase):
    def test_consecutive_events_integrate_over_capacity_time(self) -> None:
        events = [
            {"unit_code": "pa_sao_marcos", "created_at": NOW - timedelta(hours=6), "red_over": True},
            {"unit_code": "pa_sao_marcos", "created_at": NOW - timedelta(hours=3), "red_over": False},
            {"unit_code": "pa_sao_marcos", "created_at": NOW - timedelta(hours=1), "red_over": False},
        ]
        ranking = compute_over_capacity_ranking(events, NOW, 7)
        item = ranking[0]
        self.assertAlmostEqual(item["red_hours"], 3.0, places=2)
        self.assertAlmostEqual(item["over_hours"], 3.0, places=2)
        self.assertAlmostEqual(item["observed_hours"], 6.0, places=2)

    def test_long_gap_is_capped(self) -> None:
        events = [
            {"unit_code": "pa_sao_marcos", "created_at": NOW - timedelta(hours=40), "red_over": True},
            {"unit_code": "pa_sao_marcos", "created_at": NOW - timedelta(hours=1), "red_over": False},
        ]
        ranking = compute_over_capacity_ranking(events, NOW, 7)
        self.assertAlmostEqual(ranking[0]["red_hours"], 6.0, places=2)
        self.assertTrue(ranking[0]["truncated"])

    def test_yellow_over_capacity_is_counted_separately(self) -> None:
        events = [
            {"unit_code": "upa_barris", "created_at": NOW - timedelta(hours=4), "yellow_over": True},
            {"unit_code": "upa_barris", "created_at": NOW - timedelta(hours=2), "yellow_over": False},
        ]
        ranking = compute_over_capacity_ranking(events, NOW, 7)
        self.assertAlmostEqual(ranking[0]["yellow_hours"], 2.0, places=2)
        self.assertEqual(ranking[0]["red_hours"], 0.0)

    def test_ranking_orders_by_time_over_capacity(self) -> None:
        events = [
            {"unit_code": "upa_barris", "created_at": NOW - timedelta(hours=5), "red_over": True},
            {"unit_code": "upa_barris", "created_at": NOW - timedelta(hours=4), "red_over": False},
            {"unit_code": "pa_sao_marcos", "created_at": NOW - timedelta(hours=5), "red_over": True},
            {"unit_code": "pa_sao_marcos", "created_at": NOW - timedelta(hours=1), "red_over": False},
        ]
        ranking = compute_over_capacity_ranking(events, NOW, 7)
        self.assertEqual(ranking[0]["unit_code"], "pa_sao_marcos")

    def test_report_text_documents_the_cap_and_fits(self) -> None:
        events = []
        for unit in UNIT_REGISTRY:
            for step in range(20):
                events.append(
                    {
                        "unit_code": unit["code"],
                        "created_at": NOW - timedelta(hours=4 * (20 - step)),
                        "red_over": step % 2 == 0,
                        "yellow_over": step % 3 == 0,
                    }
                )
        ranking = compute_over_capacity_ranking(events, NOW, 7)
        text = build_over_capacity_report_text(ranking, NOW, 7)
        self.assertIn("teto de 6h", text)
        self.assertIn("TEMPO ACIMA DA CAPACIDADE", text)
        self.assertLessEqual(len(text), 4000)

    def test_empty_period_says_so(self) -> None:
        text = build_over_capacity_report_text([], NOW, 7)
        self.assertIn("Nenhuma unidade acima da capacidade", text)


class UnparsedReportTests(unittest.TestCase):
    def test_empty_summary(self) -> None:
        text = build_unparsed_report_text({"total": 0}, NOW, 7)
        self.assertIn("Nenhuma mensagem fora do padrão", text)

    def test_summary_shows_counts_sources_and_samples(self) -> None:
        summary = {
            "total": 12,
            "by_source": [{"source": "whatsmeow", "count": 10}, {"source": "telegram", "count": 2}],
            "by_reason": [{"reason": "Não foi possível identificar a unidade", "count": 9}],
            "samples": [
                {
                    "created_at": NOW - timedelta(hours=2),
                    "source": "whatsmeow",
                    "first_line": "Bom dia, segue o giro 071993731970",
                    "reason": "Não foi possível identificar a unidade",
                }
            ],
        }
        text = build_unparsed_report_text(summary, NOW, 7)
        self.assertIn("12 mensagem(ns) fora do padrão", text)
        self.assertIn("whatsmeow", text)
        self.assertIn("Não foi possível identificar a unidade", text)
        self.assertIn("Bom dia, segue o giro", text)
        # telefone do texto original não pode vazar
        self.assertNotIn("071993731970", text)

    def test_fits_a_single_message(self) -> None:
        summary = {
            "total": 400,
            "by_source": [{"source": f"whatsapp-bridge:5571{index}", "count": index} for index in range(20)],
            "by_reason": [{"reason": "Motivo bem longo " * 5, "count": 3} for _ in range(10)],
            "samples": [
                {
                    "created_at": NOW,
                    "source": "whatsmeow",
                    "first_line": "linha muito longa " * 20,
                    "reason": "motivo muito longo " * 10,
                }
                for _ in range(10)
            ],
        }
        text = build_unparsed_report_text(summary, NOW, 7)
        self.assertLessEqual(len(text), 4000)


class AuthorizationTests(unittest.TestCase):
    def setUp(self) -> None:
        self._admin = main.TELEGRAM_ADMIN_CHAT_ID
        self._extra = main.TELEGRAM_REPORT_CHAT_IDS

    def tearDown(self) -> None:
        main.TELEGRAM_ADMIN_CHAT_ID = self._admin
        main.TELEGRAM_REPORT_CHAT_IDS = self._extra

    def test_admin_chat_is_authorized(self) -> None:
        main.TELEGRAM_ADMIN_CHAT_ID = "1438288563"
        main.TELEGRAM_REPORT_CHAT_IDS = ""
        self.assertTrue(is_report_chat_authorized(1438288563))
        self.assertTrue(is_report_chat_authorized("1438288563"))

    def test_other_chat_is_denied(self) -> None:
        main.TELEGRAM_ADMIN_CHAT_ID = "1438288563"
        main.TELEGRAM_REPORT_CHAT_IDS = ""
        self.assertFalse(is_report_chat_authorized(-100987654321))
        self.assertFalse(is_report_chat_authorized(None))

    def test_extra_allowlist_is_honored(self) -> None:
        main.TELEGRAM_ADMIN_CHAT_ID = "1438288563"
        main.TELEGRAM_REPORT_CHAT_IDS = "-100111, -100222"
        self.assertTrue(is_report_chat_authorized(-100222))

    def test_without_configuration_nothing_is_authorized(self) -> None:
        main.TELEGRAM_ADMIN_CHAT_ID = ""
        main.TELEGRAM_REPORT_CHAT_IDS = ""
        self.assertFalse(is_report_chat_authorized(1438288563))


class HelpTests(unittest.TestCase):
    def test_help_lists_the_new_reports(self) -> None:
        reply = build_telegram_help_reply()
        for command in ("/vagas", "/cobranca", "/lotacao", "/naoparseados", "/resumo"):
            self.assertIn(command, reply)


class WebhookDispatchTests(unittest.TestCase):
    """Fim a fim pelo webhook, sem banco e sem rede: o envio é interceptado."""

    ADMIN_CHAT = 1438288563
    GROUP_CHAT = -100987654321

    def setUp(self) -> None:
        self.client = TestClient(main.app)
        self.sent: list[tuple] = []
        self._original_send = main.send_telegram_message
        self._admin = main.TELEGRAM_ADMIN_CHAT_ID
        self._extra = main.TELEGRAM_REPORT_CHAT_IDS
        self._secret = main.TELEGRAM_WEBHOOK_SECRET
        main.send_telegram_message = lambda chat_id, text: self.sent.append((chat_id, text))
        main.TELEGRAM_ADMIN_CHAT_ID = str(self.ADMIN_CHAT)
        main.TELEGRAM_REPORT_CHAT_IDS = ""
        main.TELEGRAM_WEBHOOK_SECRET = ""

    def tearDown(self) -> None:
        main.send_telegram_message = self._original_send
        main.TELEGRAM_ADMIN_CHAT_ID = self._admin
        main.TELEGRAM_REPORT_CHAT_IDS = self._extra
        main.TELEGRAM_WEBHOOK_SECRET = self._secret

    def _post(self, text: str, chat_id: int) -> dict:
        response = self.client.post(
            "/api/webhook/telegram",
            json={"update_id": 1, "message": {"text": text, "chat": {"id": chat_id, "type": "group"}}},
        )
        self.assertEqual(response.status_code, 200)
        return response.json()

    def _last_text(self) -> str:
        return self.sent[-1][1]

    def test_report_commands_are_denied_outside_the_admin_chat(self) -> None:
        for command in ("/vagas", "/cobranca", "/lotacao", "/naoparseados"):
            with self.subTest(command=command):
                self.sent.clear()
                self._post(command, self.GROUP_CHAT)
                self.assertIn("Relatório restrito", self._last_text())

    def test_denied_reply_leaks_no_names(self) -> None:
        self._post("/cobranca", self.GROUP_CHAT)
        self.assertNotIn("👤", self._last_text())
        self.assertIsNone(re.search(r"\d{10,}", self._last_text()))

    def test_report_commands_answer_in_the_admin_chat(self) -> None:
        self._post("/vagas", self.ADMIN_CHAT)
        self.assertIn("VAGAS AGORA", self._last_text())

    def test_command_with_bot_suffix_reaches_the_handler(self) -> None:
        """R2: em grupo o Telegram entrega /vagas@bot — antes caía na ajuda."""
        self._post("/vagas@girodeleitos_bot", self.ADMIN_CHAT)
        self.assertIn("VAGAS AGORA", self._last_text())

    def test_resumo_with_bot_suffix_is_answered(self) -> None:
        payload = self._post("/resumo@girodeleitos_bot", self.GROUP_CHAT)
        self.assertIn("summary", payload)
        self.assertNotIn("Envie o giro com o nome da unidade", self._last_text())

    def test_a_list_reply_becomes_several_balloons(self) -> None:
        """É assim que o /cobranca entrega um snippet copiável por unidade."""
        main._reply_to_chat(self.ADMIN_CHAT, ["ranking", "", "snippet", "consolidado"])
        self.assertEqual([text for _, text in self.sent], ["ranking", "snippet", "consolidado"])

    def test_unknown_command_returns_help(self) -> None:
        self._post("/qualquercoisa", self.ADMIN_CHAT)
        self.assertIn("Comandos disponíveis", self._last_text())

    def test_free_text_is_still_treated_as_a_giro(self) -> None:
        payload = self._post("UNIDADE: UPA VALÉRIA\n🔴 SALA VERMELHA (04/04)", self.ADMIN_CHAT)
        self.assertIn("event", payload)
        self.assertEqual(payload["event"]["data"]["unit_code"], "upa_valeria")

    def test_every_reply_goes_through_the_chunked_sender(self) -> None:
        """Regressão do /resumo mudo: resposta longa vira várias partes."""
        chunks: list[str] = []
        original_chunk = main._send_telegram_chunk
        original_builder = main.build_system_summary_text
        original_token = main.TELEGRAM_BOT_TOKEN
        main.send_telegram_message = self._original_send
        main._send_telegram_chunk = lambda chat_id, text: chunks.append(text)
        main.build_system_summary_text = lambda rows: "\n".join(
            f"linha {index} de resumo bem comprida" * 3 for index in range(400)
        )
        main.TELEGRAM_BOT_TOKEN = "token-de-teste"
        try:
            self._post("/resumo", self.ADMIN_CHAT)
        finally:
            main._send_telegram_chunk = original_chunk
            main.build_system_summary_text = original_builder
            main.TELEGRAM_BOT_TOKEN = original_token

        self.assertGreater(len(chunks), 1)
        for chunk in chunks:
            self.assertLess(len(chunk), 4096)


if __name__ == "__main__":
    unittest.main()
