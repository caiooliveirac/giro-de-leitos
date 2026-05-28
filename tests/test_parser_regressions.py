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

    # --- Sala vermelha: pacientes individuais --------------------------------
    def test_red_room_patients_bulleted_with_age(self) -> None:
        text = (
            "🔴 SALA VERMELHA: (07/04)\n"
            "* OPO, 77a, AA, CRISE CONVULSIVA SEC\n"
            "* MLJN, 79a, MNR, TUMOR INFECTADO, DISPNEIA\n"
            "🟡 SALA AMARELA: (10/12)\n"
        )
        patients = parse_whatsapp_message(text)["rooms"]["red_room"]["patients"]
        self.assertEqual(len(patients), 2)
        self.assertEqual(patients[0]["sigla"], "OPO")
        self.assertEqual(patients[0]["age"], "77a")
        self.assertIn("CRISE CONVULSIVA", patients[0]["clinical_summary"])
        self.assertEqual(patients[1]["sigla"], "MLJN")

    def test_red_room_patients_asterisk_sigla_blank_separated(self) -> None:
        text = (
            "🔴 SALA VERMELHA: (02/07)\n\n"
            "• *JBD* 83 ANOS, HEMORRAGIA, HAS, EM AA.\n\n"
            "• *MS-DCL* 17 ANOS, EPILEPSIA, GLASGOW 14\n\n"
            "🟡 SALA AMARELA\n"
        )
        patients = parse_whatsapp_message(text)["rooms"]["red_room"]["patients"]
        self.assertEqual([p["sigla"] for p in patients], ["JBD", "MS-DCL"])

    def test_red_room_patients_numbered_and_dotted_sigla(self) -> None:
        text = (
            "🔴 SALA VERMELHA:(3/2)\n\n"
            "1- *J.N.N*, 71a, AA, GLASGOW: 15: SD: IR.\n"
            "2- *M.J.S*, 51a, AA, SD: AVC\n"
            "3- *C.S.B* (EM OBSERVAÇÃO)\n\n"
            "🟡 SALA\n"
        )
        patients = parse_whatsapp_message(text)["rooms"]["red_room"]["patients"]
        self.assertEqual([p["sigla"] for p in patients], ["J.N.N", "M.J.S", "C.S.B"])
        self.assertIsNone(patients[2]["age"])

    def test_red_room_note_line_is_not_a_patient(self) -> None:
        text = (
            "🔴 SALA VERMELHA: (01/02)\n"
            "• *ABC* 40 ANOS, SEPSE\n"
            "• *OBS*: INFORMAMOS QUE TEMOS 09 PACIENTES AGUARDANDO\n"
            "🟡 SALA AMARELA\n"
        )
        patients = parse_whatsapp_message(text)["rooms"]["red_room"]["patients"]
        self.assertEqual([p["sigla"] for p in patients], ["ABC"])

    def test_red_room_no_patients_warns(self) -> None:
        text = "🔴 SALA VERMELHA: (02/04)\n🟡 SALA AMARELA: (00/12)\n"
        parsed = parse_whatsapp_message(text)
        self.assertEqual(parsed["rooms"]["red_room"]["patients"], [])
        self.assertIn("Pacientes da Sala Vermelha não detalhados no texto.", parsed["warnings"])

    def test_red_room_strips_leito_label_to_find_sigla(self) -> None:
        text = (
            "🔴 *SALA VERMELHA:* (02/03)\n"
            "* *LEITO-01:* I.V.M.S., 44 ANOS, AA | IAM\n"
            "* *CORR-01:* N.S., 60 ANOS, AA | CRISE CONVULSIVA\n"
            "🟡 SALA AMARELA\n"
        )
        patients = parse_whatsapp_message(text)["rooms"]["red_room"]["patients"]
        self.assertEqual([p["sigla"] for p in patients], ["I.V.M.S", "N.S"])

    def test_red_room_obs_with_age_is_patient_not_note(self) -> None:
        # "OBS" é nota quando seguido de ":" sem idade, mas paciente real
        # quando vem com idade ("1. OBS, 96 anos, ...").
        text = "🔴 SALA VERMELHA: (01/04)\n1. OBS, 96 anos, SD: DRC\n🟡 SALA AMARELA\n"
        patients = parse_whatsapp_message(text)["rooms"]["red_room"]["patients"]
        self.assertEqual([p["sigla"] for p in patients], ["OBS"])
        self.assertEqual(patients[0]["age"], "96 anos")

    def test_red_room_vitals_line_does_not_cut_patient_list(self) -> None:
        # Linha de sinais vitais com "PA: 128" não deve encerrar o bloco.
        text = (
            "🔴 SALA VERMELHA: (02/02)\n"
            "1 - RSS, MASCULINO, 46 ANOS,\n"
            "SSVV: PA: 128 X 90MMHG; FC:95 BPM\n\n"
            "2- PBJ, MASCULINO, 91 ANOS,\n"
            "🟡 SALA AMARELA: (04/04)\n"
        )
        patients = parse_whatsapp_message(text)["rooms"]["red_room"]["patients"]
        self.assertEqual([p["sigla"] for p in patients], ["RSS", "PBJ"])

    def test_red_room_sigla_dash_status_without_age(self) -> None:
        # Formato "SIGLA - status" sem marcador nem idade (centro maria).
        text = (
            "🔴 SALA VERMELHA: (03/02)\n\n"
            "TBDESDS - EM OBSERVAÇÃO\n\n"
            "SSB - EM OBSERVAÇÃO\n\n"
            "ICS - AGUARDA CONDUTA\n\n"
            "OBS: 03 PACIENTES AGUARDANDO REGULAÇÃO\n\n"
            "🟡 SALA AMARELA: (12/12)\n"
        )
        patients = parse_whatsapp_message(text)["rooms"]["red_room"]["patients"]
        self.assertEqual([p["sigla"] for p in patients], ["TBDESDS", "SSB", "ICS"])
        self.assertIn("OBSERVAÇÃO", patients[0]["clinical_summary"])

    def test_specialists_dentist_and_pediatrician_from_atendimento(self) -> None:
        spec = parse_whatsapp_message(VALERIA_TEXT)["specialists"]
        self.assertTrue(spec["has_dentist"])
        self.assertTrue(spec["has_pediatrician"])
        self.assertTrue(spec["has_psychiatrist"])
        self.assertFalse(spec["has_orthopedist"])


if __name__ == "__main__":
    unittest.main()