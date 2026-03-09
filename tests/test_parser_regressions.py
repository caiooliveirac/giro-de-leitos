from __future__ import annotations

import unittest

from main import build_dashboard_event, build_missing_unit_reply, build_telegram_help_reply
from parser_service import parse_whatsapp_message


VALERIA_TEXT = """*Boa noite!!*

*UNIDADE: UPA VALÉRIA*


🗓️ *DATA: 07.03.2026*     
⏰  *00:44horas*


*ATENDIMENTO:*

✅ CLÍNICA
❌ ORTOPEDIA
✅ PEDIATRIA
✅ PSIQUIATRIA
❌ CIRURGIA
✅ DENTISTA (07h às 19h)


🔴 *SALA VERMELHA: (04/04)*
"""


SAO_CRISTOVAO_TEXT = """*Boa Noite!!!*

*UNIDADE: UPA PARQUE SÃO CRISTÓVÃO*


🗓️ *DATA: 07.03.2026*
⏰  *01:29HORAS*


*ATENDIMENTO:*

✅ CLÍNICA
❌ ORTOPEDIA

🔴 *SALA VERMELHA (04/04)*
"""


PERNAMBUES_TEXT = """Bom Dia!!

📍UNIDADE: PA PERNAMBUÉS - EDSON TEIXEIRA BARBOSA
🗓️DATA : 07/03/2026
hora: 10:46

🔴 SALA VERMELHA: (4/2)
🟡 SALA AMARELA:
(4/4) FEMININA
(4/3) MASCULINA
"""


ORLANDO_TEXT = """*PA DR ORLANDO IMBASSAHY*
🗓️DATA: 06/03/26
⏰HORARIO:01:48H

🔴 SALA VERMELHA (01/01)
🟡 UNIDADE NÃO DISPOE DE LEITO DE AMARELA
"""


PERIPERI_TEXT = """Atualização da Sala Vermelha

Unidade: *UPA Adroaldo Albergaria*
Data: 07/03/2026
Hora: 05:30

🔴 *SALA VERMELHA*
(04/03)

ISOLAMENTO: (01/01) adulto
"""


UNKNOWN_UNIT_TEXT = """🗓 07/03/2026
⏰ HORÁRIO: 12:47H

❌❌UNIDADE SEM CIRURGIÃO, OPERANDO COM 3º CLÍNICO❌❌

ATENDIMENTO: ✅

🔴 SALA VERMELHA (03/03)

🟡 SALA AMARELA
(05/05) MASCULINA
(05/05) FEMININA
"""


class ParserRegressionTests(unittest.TestCase):
    def test_extracts_time_with_clock_emoji_and_horas_suffix_for_valeria(self) -> None:
        parsed = parse_whatsapp_message(VALERIA_TEXT)
        self.assertEqual(parsed["reported_at"], "2026-03-07T03:44:00+00:00")

    def test_extracts_time_with_clock_emoji_and_horas_suffix_for_sao_cristovao(self) -> None:
        parsed = parse_whatsapp_message(SAO_CRISTOVAO_TEXT)
        self.assertEqual(parsed["reported_at"], "2026-03-07T04:29:00+00:00")

    def test_pernambues_alias_does_not_warn_about_missing_unit(self) -> None:
        event = build_dashboard_event(PERNAMBUES_TEXT, source="test")
        self.assertEqual(event["data"]["unit_code"], "pa_pernambues")
        self.assertNotIn("Nome da UPA não identificado no payload.", event["data"]["warnings"])

    def test_orlando_alias_does_not_warn_about_missing_unit_or_yellow(self) -> None:
        event = build_dashboard_event(ORLANDO_TEXT, source="test")
        self.assertEqual(event["data"]["unit_code"], "upa_bairro_da_paz_orlando_imbassahy")
        self.assertNotIn("Nome da UPA não identificado no payload.", event["data"]["warnings"])
        self.assertNotIn("Capacidade da Sala Amarela não identificada.", event["data"]["warnings"])

    def test_periperi_does_not_warn_about_missing_yellow(self) -> None:
        event = build_dashboard_event(PERIPERI_TEXT, source="test")
        self.assertEqual(event["data"]["unit_code"], "upa_periperi")
        self.assertNotIn("Capacidade da Sala Amarela não identificada.", event["data"]["warnings"])

    def test_unknown_unit_line_is_not_treated_as_real_unit_name(self) -> None:
        parsed = parse_whatsapp_message(UNKNOWN_UNIT_TEXT)
        self.assertIsNone(parsed["upa_name"])
        self.assertIn("Nome da UPA não identificado no payload.", parsed["warnings"])

    def test_missing_unit_reply_requests_confirmation(self) -> None:
        event = build_dashboard_event(UNKNOWN_UNIT_TEXT, source="test")
        self.assertIsNone(event["data"]["unit_code"])
        reply = build_missing_unit_reply(event)
        self.assertIn("Não consegui identificar a unidade", reply)
        self.assertIn("não será consolidado no painel", reply)

    def test_alias_in_raw_text_resolves_known_unit(self) -> None:
        event = build_dashboard_event("12º CENTRO MARBACK - ALFREDO BUREAU\n🔴 SALA VERMELHA (02/05)", source="test")
        self.assertEqual(event["data"]["unit_code"], "centro_marback_alfredo_bureau")

    def test_help_reply_mentions_start_options(self) -> None:
        reply = build_telegram_help_reply()
        self.assertIn("/resumo", reply)
        self.assertIn("UPA SAN MARTIN", reply)


if __name__ == "__main__":
    unittest.main()