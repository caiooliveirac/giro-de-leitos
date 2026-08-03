"""Autoria do giro e menção de quem posta.

As UPAs postam o próprio giro no WhatsApp, então quem posta É o contato da
unidade. Estes testes cobrem o caminho inteiro dessa informação: normalizar o
número que chega da ingestão, guardar o autor, montar a menção do alerta e da
cobrança — e, o mais importante, DEGRADAR sem erro enquanto a tabela de
contatos estiver vazia, que é como ela nasce em produção.
"""

from __future__ import annotations

import json
import os
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

os.environ.setdefault("JWT_SECRET", "test-secret-contacts")
os.environ.setdefault("CPF_ENCRYPTION_KEY", "OmaP3i0nC2P9MwJv5wDhlb0aBpfNn5Y73I9c8wL2cIc=")
os.environ.setdefault("CPF_HASH_PEPPER", "test-pepper")

import db  # noqa: E402
import main  # noqa: E402
from reports import (  # noqa: E402
    build_compliance_messages,
    build_whatsapp_stale_alert_text,
    collect_alert_mentions,
    compute_gap_ranking,
    unit_mentions,
)
from services import whatsapp_alerts  # noqa: E402
from units import UNIT_REGISTRY  # noqa: E402


NOW = datetime(2026, 8, 3, 18, 32, tzinfo=timezone.utc)  # 15:32 em America/Sao_Paulo
_LOCAL_TZ = timezone(timedelta(hours=-3))

BARRIS = "5571988887777"
BARRIS_2 = "5571977776666"
SAO_MARCOS = "5571966665555"


def _local(day: int, hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 8, day, hour, minute, tzinfo=_LOCAL_TZ)


def _offender(unit_code: str, unit_name: str, age_hours: float, shift: str = "diurno") -> dict:
    return {
        "unit_key": unit_code,
        "unit_code": unit_code,
        "unit_name": unit_name,
        "age_hours": age_hours,
        "shift": shift,
        "limit_hours": 6.0 if shift == "diurno" else 12.0,
    }


def _contact(phone: str, name: str | None = None, giro_count: int = 1) -> dict:
    return {"phone": phone, "name": name, "giro_count": giro_count, "last_seen_at": NOW}


CONTACTS = {
    "upa_barris": [_contact(BARRIS, "MARIA ENFERMEIRA", 40), _contact(BARRIS_2, None, 5)],
    "pa_sao_marcos": [_contact(SAO_MARCOS, "JOÃO TÉCNICO", 12)],
}


class PhoneNormalizationTests(unittest.TestCase):
    """O número tem que sair sempre na mesma grafia: é chave de contato."""

    def test_jid_variants_become_plain_digits(self) -> None:
        casos = {
            "5571988887777@s.whatsapp.net": BARRIS,
            "5571988887777:42@s.whatsapp.net": BARRIS,
            "5571988887777@lid": BARRIS,
            "+55 (71) 98888-7777": BARRIS,
            " 5571988887777 ": BARRIS,
            BARRIS: BARRIS,
        }
        for entrada, esperado in casos.items():
            with self.subTest(entrada=entrada):
                self.assertEqual(whatsapp_alerts.normalize_phone(entrada), esperado)

    def test_missing_or_implausible_values_become_empty(self) -> None:
        # "557181082189-1462561641" é o id do GRUPO de produção (22 dígitos):
        # mencionar isso não notificaria ninguém.
        for entrada in (None, "", "   ", "abc", "123", "@s.whatsapp.net", 0, "557181082189-1462561641"):
            with self.subTest(entrada=entrada):
                self.assertEqual(whatsapp_alerts.normalize_phone(entrada), "")

    def test_matches_the_adapter_rule_of_only_digits_before_the_at(self) -> None:
        """Mesma regra do deploy/giro-wa-adapter/adapter.mjs (normalizePhone)."""
        self.assertEqual(whatsapp_alerts.normalize_phone("5571988887777@g.us"), BARRIS)


class IngestAuthorshipTests(unittest.TestCase):
    def test_sender_is_normalized_into_the_event(self) -> None:
        event = main.build_dashboard_event(
            "GIRO UPA BARRIS 10:00",
            "whatsmeow",
            sender_phone="5571988887777:3@s.whatsapp.net",
            sender_name="  Maria Enfermeira  ",
        )

        self.assertEqual(event["data"]["sender_phone"], BARRIS)
        self.assertEqual(event["data"]["sender_name"], "Maria Enfermeira")

    def test_event_without_sender_carries_no_authorship_keys(self) -> None:
        """O adapter antigo (e a ingestão manual) continuam valendo."""
        event = main.build_dashboard_event("GIRO UPA BARRIS 10:00", "manual")

        self.assertNotIn("sender_phone", event["data"])
        self.assertNotIn("sender_name", event["data"])

    def test_garbage_sender_is_dropped_instead_of_stored(self) -> None:
        event = main.build_dashboard_event("GIRO UPA BARRIS 10:00", "whatsmeow", sender_phone="bot")

        self.assertNotIn("sender_phone", event["data"])

    def test_ingest_endpoint_accepts_and_forwards_the_sender(self) -> None:
        with mock.patch.object(main, "build_dashboard_event", wraps=main.build_dashboard_event) as build, \
                mock.patch.object(main, "is_database_configured", return_value=False):
            from fastapi.testclient import TestClient

            with TestClient(main.app) as client:
                response = client.post(
                    "/api/ingest/whatsapp-bridge",
                    json={
                        "text": "GIRO UPA BARRIS 10:00",
                        "source": "whatsmeow",
                        "sender_phone": "5571988887777@s.whatsapp.net",
                        "sender_name": "Maria",
                        "dry_run": True,
                    },
                )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(build.call_args.kwargs["sender_phone"], "5571988887777@s.whatsapp.net")
        self.assertEqual(build.call_args.kwargs["sender_name"], "Maria")
        self.assertEqual(response.json()["event"]["data"]["sender_phone"], BARRIS)


class ContactGroupingTests(unittest.TestCase):
    def _row(self, unit_code: str, phone: str, count: int, name: str | None = None) -> dict:
        return {
            "unit_code": unit_code,
            "sender_phone": phone,
            "sender_name": name,
            "giro_count": count,
            "last_seen_at": NOW,
        }

    def test_rows_are_grouped_by_unit_keeping_the_sql_order(self) -> None:
        grouped = db.group_unit_contacts(
            [
                self._row("upa_barris", BARRIS, 40, "MARIA"),
                self._row("upa_barris", BARRIS_2, 5),
                self._row("pa_sao_marcos", SAO_MARCOS, 12, "JOÃO"),
            ]
        )

        self.assertEqual([item["phone"] for item in grouped["upa_barris"]], [BARRIS, BARRIS_2])
        self.assertEqual(grouped["upa_barris"][0]["name"], "MARIA")
        self.assertEqual(grouped["pa_sao_marcos"][0]["giro_count"], 12)

    def test_limit_per_unit_is_respected(self) -> None:
        rows = [self._row("upa_barris", f"55719000000{i:02d}", 10 - i) for i in range(5)]

        grouped = db.group_unit_contacts(rows, limit_per_unit=2)

        self.assertEqual(len(grouped["upa_barris"]), 2)

    def test_rows_without_unit_or_phone_are_ignored(self) -> None:
        grouped = db.group_unit_contacts(
            [self._row("", BARRIS, 1), self._row("upa_barris", "", 1), {"unit_code": "upa_barris"}]
        )

        self.assertEqual(grouped, {})

    def test_no_database_means_no_contacts_not_an_error(self) -> None:
        with mock.patch.object(db, "DATABASE_URL", ""):
            self.assertEqual(db.get_unit_contacts(), {})


class MentionTests(unittest.TestCase):
    def test_mentions_are_the_numbers_of_who_posts_the_unit(self) -> None:
        self.assertEqual(unit_mentions("upa_barris", CONTACTS), [BARRIS, BARRIS_2])

    def test_alert_mentions_are_unique_and_follow_the_offenders(self) -> None:
        offenders = [_offender("upa_barris", "UPA BARRIS", 9.0), _offender("pa_sao_marcos", "PA SÃO MARCOS", 8.0)]

        self.assertEqual(
            collect_alert_mentions(offenders, CONTACTS), [BARRIS, BARRIS_2, SAO_MARCOS]
        )

    def test_the_same_person_posting_for_two_units_is_mentioned_once(self) -> None:
        contacts = {"upa_barris": [_contact(BARRIS)], "pa_sao_marcos": [_contact(BARRIS)]}
        offenders = [_offender("upa_barris", "UPA BARRIS", 9.0), _offender("pa_sao_marcos", "PA SÃO MARCOS", 8.0)]

        self.assertEqual(collect_alert_mentions(offenders, contacts), [BARRIS])

    def test_unknown_unit_contributes_no_mention(self) -> None:
        offenders = [_offender("upa_valeria", "UPA VALÉRIA", 9.0)]

        self.assertEqual(collect_alert_mentions(offenders, CONTACTS), [])

    def test_empty_contacts_table_yields_no_mentions(self) -> None:
        offenders = [_offender("upa_barris", "UPA BARRIS", 9.0)]

        for contacts in ({}, None):
            with self.subTest(contacts=contacts):
                self.assertEqual(collect_alert_mentions(offenders, contacts), [])


class AlertTextWithMentionsTests(unittest.TestCase):
    def test_unit_line_mentions_who_posts(self) -> None:
        text = build_whatsapp_stale_alert_text(
            [_offender("upa_barris", "UPA BARRIS", 9.0)], {}, NOW, None, CONTACTS
        )

        self.assertIn(f"• UPA BARRIS — 9h00 (limite diurno 6h) · 👤 @{BARRIS} · @{BARRIS_2}", text)

    def test_contact_wins_over_the_registered_coordinator(self) -> None:
        """Nomear o coordenador não resolvia: quem posta é quem responde."""
        text = build_whatsapp_stale_alert_text(
            [_offender("upa_barris", "UPA BARRIS", 9.0)],
            {"upa_barris": ["JOILSON SANTOS"]},
            NOW,
            None,
            CONTACTS,
        )

        self.assertIn(f"@{BARRIS}", text)
        self.assertNotIn("JOILSON SANTOS", text)

    def test_unit_without_contact_falls_back_to_the_coordinator(self) -> None:
        text = build_whatsapp_stale_alert_text(
            [_offender("upa_valeria", "UPA VALÉRIA", 9.0)],
            {"upa_valeria": ["JOILSON SANTOS"]},
            NOW,
            None,
            CONTACTS,
        )

        self.assertIn("👤 JOILSON SANTOS", text)

    def test_no_contact_and_no_coordinator_says_so(self) -> None:
        text = build_whatsapp_stale_alert_text([_offender("upa_valeria", "UPA VALÉRIA", 9.0)], {}, NOW, None, {})

        self.assertIn("👤 sem coordenador", text)

    def test_more_than_two_contacts_are_trimmed_with_a_counter(self) -> None:
        contacts = {"upa_barris": [_contact(BARRIS), _contact(BARRIS_2), _contact(SAO_MARCOS)]}

        text = build_whatsapp_stale_alert_text([_offender("upa_barris", "UPA BARRIS", 9.0)], {}, NOW, None, contacts)

        self.assertIn(f"@{BARRIS} · @{BARRIS_2} +1", text)

    def test_empty_contacts_produce_the_same_text_as_before(self) -> None:
        offenders = [_offender("upa_barris", "UPA BARRIS", 9.0)]
        responsibles = {"upa_barris": ["JOILSON SANTOS"]}

        self.assertEqual(
            build_whatsapp_stale_alert_text(offenders, responsibles, NOW, None, {}),
            build_whatsapp_stale_alert_text(offenders, responsibles, NOW),
        )


class GatewayMentionPayloadTests(unittest.TestCase):
    """Nenhum destes testes pode encostar na rede."""

    def _capture(self, **kwargs) -> dict:
        captured: dict = {}

        class _Response:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self):
                return b'{"code":"SUCCESS"}'

        def _fake_urlopen(req, timeout=None):
            captured["body"] = json.loads(req.data.decode("utf-8"))
            return _Response()

        with mock.patch.object(whatsapp_alerts.request, "urlopen", _fake_urlopen):
            whatsapp_alerts.send_gateway_message(
                "5571999999999", "aviso", url="http://127.0.0.1:3080", auth="admin:segredo", **kwargs
            )
        return captured["body"]

    def test_mentions_go_as_an_array_of_digits(self) -> None:
        body = self._capture(mentions=[f"{BARRIS}@s.whatsapp.net", SAO_MARCOS])

        self.assertEqual(body["mentions"], [BARRIS, SAO_MARCOS])
        self.assertEqual(body["phone"], "5571999999999")

    def test_duplicate_and_invalid_mentions_are_dropped(self) -> None:
        body = self._capture(mentions=[BARRIS, BARRIS, "", None, "abc"])

        self.assertEqual(body["mentions"], [BARRIS])

    def test_without_mentions_the_field_is_absent(self) -> None:
        """Gateway antigo não pode receber um campo vazio e devolver 400."""
        for mentions in ([], None, ["abc"]):
            with self.subTest(mentions=mentions):
                self.assertNotIn("mentions", self._capture(mentions=mentions))

    def test_dispatch_forwards_the_mentions_without_touching_the_network(self) -> None:
        with mock.patch.object(whatsapp_alerts, "WHATSAPP_ALERT_ENABLED", True), \
                mock.patch.object(whatsapp_alerts, "WHATSAPP_ALERT_TO", "5571999999999"), \
                mock.patch.object(whatsapp_alerts, "send_gateway_message") as send:
            sent = whatsapp_alerts.dispatch_stale_alert("aviso", [f"{BARRIS}@s.whatsapp.net"])

        self.assertTrue(sent)
        send.assert_called_once_with("5571999999999", "aviso", mentions=[BARRIS])

    def test_dry_run_with_mentions_still_sends_nothing(self) -> None:
        with mock.patch.object(whatsapp_alerts, "WHATSAPP_ALERT_ENABLED", False), \
                mock.patch.object(whatsapp_alerts, "WHATSAPP_ALERT_TO", "5571999999999"), \
                mock.patch.object(whatsapp_alerts, "send_gateway_message") as send:
            sent = whatsapp_alerts.dispatch_stale_alert("aviso", [BARRIS])

        self.assertFalse(sent)
        send.assert_not_called()


class WatcherWiringTests(unittest.TestCase):
    def _rows(self) -> list[dict]:
        return [
            {
                "unit_key": "upa_barris",
                "unit_code": "upa_barris",
                "displayed_name": "UPA BARRIS",
                "canonical_name": "UPA BARRIS",
                "updated_at": NOW - timedelta(hours=20),
            }
        ]

    def test_alert_mentions_who_posts_the_silent_unit(self) -> None:
        with mock.patch.object(main, "is_database_configured", return_value=True), \
                mock.patch.object(main, "get_whatsapp_alert_state", return_value={}), \
                mock.patch.object(main, "get_unit_responsibles", return_value={}), \
                mock.patch.object(main, "get_unit_contacts", return_value=CONTACTS), \
                mock.patch.object(main, "dispatch_stale_alert", return_value=False) as dispatch:
            main._notify_stale_whatsapp(self._rows(), NOW)

        message, mentions = dispatch.call_args.args
        self.assertIn(f"@{BARRIS}", message)
        self.assertEqual(mentions, [BARRIS, BARRIS_2])

    def test_empty_contacts_table_does_not_break_the_watcher(self) -> None:
        with mock.patch.object(main, "is_database_configured", return_value=True), \
                mock.patch.object(main, "get_whatsapp_alert_state", return_value={}), \
                mock.patch.object(main, "get_unit_responsibles", return_value={"upa_barris": ["JOILSON"]}), \
                mock.patch.object(main, "get_unit_contacts", return_value={}), \
                mock.patch.object(main, "dispatch_stale_alert", return_value=False) as dispatch:
            main._notify_stale_whatsapp(self._rows(), NOW)

        message, mentions = dispatch.call_args.args
        self.assertIn("👤 JOILSON", message)
        self.assertEqual(mentions, [])

    def test_contacts_lookup_failure_never_reaches_the_watcher(self) -> None:
        with mock.patch.object(main, "is_database_configured", return_value=True), \
                mock.patch.object(main, "get_whatsapp_alert_state", return_value={}), \
                mock.patch.object(main, "get_unit_responsibles", return_value={}), \
                mock.patch.object(main, "get_unit_contacts", side_effect=RuntimeError("coluna não existe")), \
                mock.patch.object(main, "dispatch_stale_alert") as dispatch:
            main._notify_stale_whatsapp(self._rows(), NOW)  # não levanta

        dispatch.assert_not_called()


class ComplianceWithContactsTests(unittest.TestCase):
    """/cobranca também passa a falar com quem posta."""

    def _ranking(self) -> list[dict]:
        baseline = {unit["code"]: NOW - timedelta(hours=1) for unit in UNIT_REGISTRY}
        baseline["upa_barris"] = _local(3, 2, 0)  # 13h30 de silêncio, turno noturno
        events = [
            {"unit_code": "upa_barris", "created_at": _local(1, 8)},
            {"unit_code": "upa_barris", "created_at": _local(1, 22)},
            {"unit_code": "upa_barris", "created_at": _local(3, 2)},
        ]
        return compute_gap_ranking(events, baseline, NOW, 7)

    def test_ranking_points_to_who_posts_instead_of_the_coordinator(self) -> None:
        messages = build_compliance_messages(
            self._ranking(), {"upa_barris": ["JOILSON SANTOS"]}, NOW, 7, 0, CONTACTS
        )

        self.assertIn(f"👤 posta o giro: @{BARRIS} · @{BARRIS_2} (Maria)", messages[0])
        self.assertNotIn("JOILSON SANTOS", messages[0])

    def test_snippet_greets_whoever_posts_the_giro(self) -> None:
        messages = build_compliance_messages(
            self._ranking(), {"upa_barris": ["JOILSON SANTOS"]}, NOW, 7, 0, CONTACTS
        )

        self.assertIn("Olá, Maria", messages[1])

    def test_copiable_snippet_never_carries_a_phone_number(self) -> None:
        """O balão copiável vai para a unidade; o número fica no lado do gestor."""
        messages = build_compliance_messages(self._ranking(), {}, NOW, 7, 0, CONTACTS)

        for phone in (BARRIS, BARRIS_2, SAO_MARCOS):
            self.assertNotIn(phone, messages[1])

    def test_consolidated_names_who_posts(self) -> None:
        messages = build_compliance_messages(self._ranking(), {}, NOW, 7, 0, CONTACTS)

        self.assertIn(f"posta: @{BARRIS}", messages[-1])

    def test_without_contacts_the_cobranca_is_exactly_the_one_from_before(self) -> None:
        responsibles = {"upa_barris": ["JOILSON SANTOS"]}
        ranking = self._ranking()

        self.assertEqual(
            build_compliance_messages(ranking, responsibles, NOW, 7, 0, {}),
            build_compliance_messages(ranking, responsibles, NOW, 7, 0),
        )
        self.assertIn("👤 JOILSON SANTOS", build_compliance_messages(ranking, responsibles, NOW, 7, 0)[0])

    def test_unit_without_contact_still_falls_back_to_the_coordinator(self) -> None:
        messages = build_compliance_messages(
            self._ranking(), {"upa_barris": ["JOILSON SANTOS"]}, NOW, 7, 0, {"upa_valeria": [_contact(SAO_MARCOS)]}
        )

        self.assertIn("👤 JOILSON SANTOS", messages[0])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
