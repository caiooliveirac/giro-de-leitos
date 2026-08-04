"""Aviso de restrição de UPA no grupo do WhatsApp.

O grupo é o mesmo onde as UPAs postam o giro: se o aviso mentir ou repetir, o
grupo é silenciado e o canal deixa de existir. Os testes daqui cobrem, nesta
ordem, as três formas de mentir:

1. anunciar liberação porque a API do `tabela` caiu (o pior — não há como
   desfazer no grupo);
2. re-anunciar como nova uma restrição que já estava valendo (restart/deploy);
3. repetir o lembrete de 4h dentro da mesma janela.

Nenhum teste pode encostar na rede.
"""

from __future__ import annotations

import os
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

# `main`/`reports` fazem fail-fast em auth/crypto no import (mesma receita dos
# outros testes do pacote).
os.environ.setdefault("JWT_SECRET", "test-secret-restrictions")
os.environ.setdefault("CPF_ENCRYPTION_KEY", "OmaP3i0nC2P9MwJv5wDhlb0aBpfNn5Y73I9c8wL2cIc=")
os.environ.setdefault("CPF_HASH_PEPPER", "test-pepper")

from services import upa_restrictions_wa as wa  # noqa: E402
from services import whatsapp_alerts  # noqa: E402


_LOCAL_TZ = timezone(timedelta(hours=-3))  # America/Sao_Paulo não tem verão
NOW = datetime(2026, 8, 4, 18, 32, tzinfo=timezone.utc)  # 15:32 local


def _local(day: int, hour: int, minute: int = 0) -> datetime:
    """Instante escrito em hora LOCAL — é assim que as janelas são definidas."""
    return datetime(2026, 8, day, hour, minute, tzinfo=_LOCAL_TZ)


def _api_row(identifier: str, name: str, until: datetime, label: str = "hoje 19:00") -> dict:
    """Uma linha como o `tabela` devolve: camelCase, ISO + rótulo pronto."""
    return {
        "id": identifier,
        "unitKey": f"upa_{name.lower().replace(' ', '_')}",
        "unitName": name,
        "until": until.isoformat(),
        "untilLabel": label,
        "autor": "Coordenação",
    }


def _snapshot_row(identifier: str, name: str, until: datetime, label: str = "hoje 19:00") -> dict:
    """A mesma linha já normalizada, como fica guardada no snapshot."""
    return {
        "id": identifier,
        "unit_key": f"upa_{name.lower().replace(' ', '_')}",
        "unit_name": name,
        "until": until.isoformat(),
        "until_label": label,
        "autor": "Coordenação",
    }


class _Recorder:
    """Substitui o envio: guarda o texto em vez de falar com o gateway."""

    def __init__(self, *, accept: bool = True) -> None:
        self.accept = accept
        self.messages: list[str] = []

    def __call__(self, message: str) -> bool:
        if not message.strip():
            return False
        self.messages.append(message)
        return self.accept


class _FakeStore:
    """Banco de mentira: snapshot + janelas reservadas."""

    def __init__(self, snapshot: dict | None = None, notices: set[str] | None = None) -> None:
        self.snapshot = snapshot
        self.notices = notices or set()
        self.writes = 0

    def read(self) -> dict | None:
        return self.snapshot

    def write(self, payload: dict) -> None:
        self.snapshot = payload
        self.writes += 1

    def claim(self, key: str) -> bool:
        if key in self.notices:
            return False
        self.notices.add(key)
        return True

    def release(self, key: str) -> None:
        self.notices.discard(key)


def _cycle(store: _FakeStore, fetch, send, now=NOW, channel_on=True):
    return wa.run_restriction_cycle(
        fetch=fetch,
        read_snapshot=store.read,
        write_snapshot=store.write,
        claim_notice=store.claim,
        release_notice=store.release,
        send=send,
        channel_on=lambda: channel_on,
        now=now,
    )


# ---------------------------------------------------------------------------
# 1. Leitura do contrato público
# ---------------------------------------------------------------------------


class FetchTests(unittest.TestCase):
    def test_network_failure_is_unknown_not_empty(self) -> None:
        """Timeout NÃO pode virar `[]` — isso anunciaria liberação geral."""
        with mock.patch.object(wa.request, "urlopen", side_effect=OSError("timeout")):
            self.assertIsNone(wa.fetch_restrictions("http://exemplo/restrictions"))

    def test_malformed_payload_is_unknown(self) -> None:
        for payload in (b"nao sou json", b'{"ok": false}', b'{"ok": true}', b'[]'):
            with self.subTest(payload=payload):
                with mock.patch.object(wa.request, "urlopen", _fake_urlopen(payload)):
                    self.assertIsNone(wa.fetch_restrictions("http://exemplo/restrictions"))

    def test_empty_list_is_a_valid_answer(self) -> None:
        """`[]` legítimo significa "nada restrito" e precisa passar."""
        with mock.patch.object(wa.request, "urlopen", _fake_urlopen(b'{"ok":true,"restrictions":[]}')):
            self.assertEqual(wa.fetch_restrictions("http://exemplo/restrictions"), [])

    def test_camel_case_contract_is_read(self) -> None:
        """A API do `tabela` fala camelCase — ler snake_case foi o bug histórico."""
        body = (
            b'{"ok":true,"restrictions":[{"id":7,"unitKey":"upa_barris",'
            b'"unitName":"UPA Barris","until":"2026-08-04T22:00:00.000Z",'
            b'"untilLabel":"hoje 19:00","autor":"Ana"}]}'
        )
        with mock.patch.object(wa.request, "urlopen", _fake_urlopen(body)):
            rows = wa.fetch_restrictions("http://exemplo/restrictions")

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["id"], "7")  # id numérico vira string de chave
        self.assertEqual(rows[0]["unit_name"], "UPA Barris")
        self.assertEqual(rows[0]["until_label"], "hoje 19:00")

    def test_row_without_name_is_dropped(self) -> None:
        body = b'{"ok":true,"restrictions":[{"id":1},{"id":2,"unitName":"UPA San Martin"}]}'
        with mock.patch.object(wa.request, "urlopen", _fake_urlopen(body)):
            rows = wa.fetch_restrictions("http://exemplo/restrictions")

        self.assertEqual([row["unit_name"] for row in rows], ["UPA San Martin"])


def _fake_urlopen(body: bytes):
    class _Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return body

    def _open(_req, timeout=None):
        return _Response()

    return _open


# ---------------------------------------------------------------------------
# 2. O que mudou
# ---------------------------------------------------------------------------


class DiffTests(unittest.TestCase):
    def test_new_unit_is_created(self) -> None:
        current = [_snapshot_row("1", "UPA Barris", NOW + timedelta(hours=4))]
        events = wa.diff_restrictions({}, current, NOW)
        self.assertEqual([(e["kind"], e["row"]["unit_name"]) for e in events], [("created", "UPA Barris")])

    def test_same_deadline_is_not_an_event(self) -> None:
        row = _snapshot_row("1", "UPA Barris", NOW + timedelta(hours=4))
        self.assertEqual(wa.diff_restrictions({"1": row}, [row], NOW), [])

    def test_changed_deadline_is_renewed(self) -> None:
        antes = _snapshot_row("1", "UPA Barris", NOW + timedelta(hours=2))
        depois = _snapshot_row("1", "UPA Barris", NOW + timedelta(hours=8))
        events = wa.diff_restrictions({"1": antes}, [depois], NOW)
        self.assertEqual([e["kind"] for e in events], ["renewed"])

    def test_gone_before_the_deadline_is_a_decision(self) -> None:
        """Sumiu com prazo no futuro = a coordenação liberou antes da hora."""
        antes = _snapshot_row("1", "UPA Barris", NOW + timedelta(hours=4))
        events = wa.diff_restrictions({"1": antes}, [], NOW)
        self.assertEqual([e["kind"] for e in events], ["liberated"])

    def test_gone_after_the_deadline_merely_expired(self) -> None:
        """Sumiu com prazo vencido = venceu sozinha, ninguém decidiu nada."""
        antes = _snapshot_row("1", "UPA Barris", NOW - timedelta(minutes=5))
        events = wa.diff_restrictions({"1": antes}, [], NOW)
        self.assertEqual([e["kind"] for e in events], ["expired"])


# ---------------------------------------------------------------------------
# 3. A varredura
# ---------------------------------------------------------------------------


class CycleTests(unittest.TestCase):
    def test_source_down_says_nothing_and_keeps_the_snapshot(self) -> None:
        antes = {"1": _snapshot_row("1", "UPA Barris", NOW + timedelta(hours=4))}
        store = _FakeStore(snapshot=dict(antes))
        send = _Recorder()

        resultado = _cycle(store, lambda: None, send)

        self.assertEqual(send.messages, [])
        self.assertEqual(store.snapshot, antes)
        self.assertEqual(store.writes, 0)
        self.assertEqual(resultado["skipped"], "fonte indisponível")

    def test_first_run_seeds_without_announcing(self) -> None:
        """Deploy novo não pode anunciar como nova cada restrição já vigente."""
        current = [_snapshot_row("1", "UPA Barris", NOW + timedelta(hours=4))]
        store = _FakeStore(snapshot=None)
        send = _Recorder()

        resultado = _cycle(store, lambda: current, send)

        self.assertEqual(send.messages, [])
        self.assertEqual(resultado["skipped"], "snapshot semeado")
        self.assertIn("1", store.snapshot)

    def test_second_run_after_seeding_stays_quiet(self) -> None:
        current = [_snapshot_row("1", "UPA Barris", NOW + timedelta(hours=4))]
        store = _FakeStore(snapshot=None)
        send = _Recorder()

        _cycle(store, lambda: current, send)
        _cycle(store, lambda: current, send)

        self.assertEqual(send.messages, [])

    def test_new_restriction_is_announced_once(self) -> None:
        current = [_snapshot_row("1", "UPA Barris", NOW + timedelta(hours=4))]
        store = _FakeStore(snapshot={})
        send = _Recorder()

        _cycle(store, lambda: current, send)
        _cycle(store, lambda: current, send)  # nada mudou desde o aviso

        self.assertEqual(len(send.messages), 1)
        self.assertIn("UPA Barris", send.messages[0])

    def test_refused_send_is_retried_and_not_recorded(self) -> None:
        """Gateway fora do ar não pode transformar o aviso em silêncio."""
        current = [_snapshot_row("1", "UPA Barris", NOW + timedelta(hours=4))]
        store = _FakeStore(snapshot={})
        recusa = _Recorder(accept=False)

        _cycle(store, lambda: current, recusa)
        self.assertEqual(store.snapshot, {})
        self.assertEqual(store.writes, 0)

        aceite = _Recorder()
        _cycle(store, lambda: current, aceite)
        self.assertEqual(len(aceite.messages), 1)
        self.assertIn("1", store.snapshot)

    def test_snapshot_advances_per_item(self) -> None:
        """Um aviso que falhou não pode segurar o que já foi entregue."""
        current = [
            _snapshot_row("1", "UPA Barris", NOW + timedelta(hours=4)),
            _snapshot_row("2", "UPA San Martin", NOW + timedelta(hours=4)),
        ]
        store = _FakeStore(snapshot={})

        class _SoADoPrimeiro(_Recorder):
            def __call__(self, message: str) -> bool:
                if "San Martin" in message:
                    return False
                return super().__call__(message)

        send = _SoADoPrimeiro()
        _cycle(store, lambda: current, send)

        self.assertIn("1", store.snapshot)
        self.assertNotIn("2", store.snapshot)


class DryRunCycleTests(unittest.TestCase):
    """Canal desligado é SIMULAÇÃO, não fila de espera."""

    def test_dry_run_advances_the_snapshot(self) -> None:
        """Senão o mesmo 'enviaria' seria logado a cada 2min, para sempre."""
        current = [_snapshot_row("1", "UPA Barris", NOW + timedelta(hours=4))]
        store = _FakeStore(snapshot={})
        recusa = _Recorder(accept=False)  # dry-run devolve False

        resultado = _cycle(store, lambda: current, recusa, channel_on=False)

        self.assertIn("1", store.snapshot)
        self.assertEqual(resultado["changes_sent"], 0)  # nada foi entregue de fato

    def test_dry_run_does_not_pile_up_a_backlog(self) -> None:
        """Ligar o canal depois não pode despejar meses de mudança no grupo."""
        current = [_snapshot_row("1", "UPA Barris", NOW + timedelta(hours=4))]
        store = _FakeStore(snapshot={})

        _cycle(store, lambda: current, _Recorder(accept=False), channel_on=False)
        depois = _Recorder()
        _cycle(store, lambda: current, depois, channel_on=True)

        self.assertEqual(depois.messages, [])


# ---------------------------------------------------------------------------
# 4. A janela de 4h
# ---------------------------------------------------------------------------


class DigestSlotTests(unittest.TestCase):
    def test_windows_are_anchored_on_the_shift_change(self) -> None:
        self.assertEqual(wa.digest_hours(4), [3, 7, 11, 15, 19, 23])

    def test_two_hour_interval_matches_the_regulator_group(self) -> None:
        """O mesmo cálculo com 2h reproduz a lista do grupo dos reguladores."""
        self.assertEqual(wa.digest_hours(2), [1, 3, 5, 7, 9, 11, 13, 15, 17, 19, 21, 23])

    def test_hour_outside_the_window_has_no_slot(self) -> None:
        self.assertIsNone(wa.resolve_digest_slot(_local(4, 16, 0), 4))

    def test_late_boot_within_grace_still_fires(self) -> None:
        self.assertIsNotNone(wa.resolve_digest_slot(_local(4, 19, 7), 4))

    def test_too_late_misses_the_window(self) -> None:
        self.assertIsNone(wa.resolve_digest_slot(_local(4, 19, 40), 4))

    def test_slot_key_is_deterministic(self) -> None:
        primeiro = wa.resolve_digest_slot(_local(4, 19, 1), 4)
        segundo = wa.resolve_digest_slot(_local(4, 19, 9), 4)
        self.assertEqual(primeiro, segundo)
        self.assertNotEqual(primeiro, wa.resolve_digest_slot(_local(4, 23, 1), 4))


class DigestTests(unittest.TestCase):
    def setUp(self) -> None:
        patch = mock.patch.object(wa, "WHATSAPP_RESTRICTION_DIGEST_HOURS", 4.0)
        patch.start()
        self.addCleanup(patch.stop)

    def test_digest_goes_out_on_the_window(self) -> None:
        current = [_snapshot_row("1", "UPA Barris", _local(4, 23))]
        store = _FakeStore(snapshot=dict({"1": current[0]}))
        send = _Recorder()

        resultado = _cycle(store, lambda: current, send, now=_local(4, 19, 2))

        self.assertTrue(resultado["digest_sent"])
        self.assertEqual(len(send.messages), 1)
        self.assertIn("UPA Barris", send.messages[0])

    def test_digest_is_not_repeated_inside_the_same_window(self) -> None:
        """Dois ticks (ou dois restarts) na mesma janela = um lembrete só."""
        current = [_snapshot_row("1", "UPA Barris", _local(4, 23))]
        store = _FakeStore(snapshot=dict({"1": current[0]}))
        send = _Recorder()

        _cycle(store, lambda: current, send, now=_local(4, 19, 2))
        _cycle(store, lambda: current, send, now=_local(4, 19, 6))

        self.assertEqual(len(send.messages), 1)

    def test_failed_digest_releases_the_window_for_a_retry(self) -> None:
        current = [_snapshot_row("1", "UPA Barris", _local(4, 23))]
        store = _FakeStore(snapshot=dict({"1": current[0]}))

        _cycle(store, lambda: current, _Recorder(accept=False), now=_local(4, 19, 2))
        aceite = _Recorder()
        _cycle(store, lambda: current, aceite, now=_local(4, 19, 8))

        self.assertEqual(len(aceite.messages), 1)

    def test_nothing_restricted_means_group_silence(self) -> None:
        store = _FakeStore(snapshot={})
        send = _Recorder()

        resultado = _cycle(store, lambda: [], send, now=_local(4, 19, 2))

        self.assertEqual(send.messages, [])
        self.assertFalse(resultado["digest_sent"])

    def test_digest_text_is_empty_without_rows(self) -> None:
        self.assertEqual(wa.build_restrictions_digest([], NOW), "")

    def test_digest_announces_units_beyond_the_cap(self) -> None:
        rows = [
            _snapshot_row(str(i), f"UPA {i}", NOW + timedelta(hours=4))
            for i in range(wa.MAX_DIGEST_UNITS + 3)
        ]
        texto = wa.build_restrictions_digest(rows, NOW)
        self.assertIn("+3 unidade(s)", texto)


# ---------------------------------------------------------------------------
# 5. Tom da mensagem
# ---------------------------------------------------------------------------


class MessageToneTests(unittest.TestCase):
    def test_new_restriction_credits_the_coordination(self) -> None:
        row = _snapshot_row("1", "UPA Barris", NOW + timedelta(hours=4))
        texto = wa.build_restriction_change_text("created", row)

        self.assertIn("Coordenação de Unidades Fixas", texto)
        self.assertIn("redirecionadas", texto)
        self.assertIn("hoje 19:00", texto)  # o rótulo vem pronto da API

    def test_text_uses_whatsapp_bold_not_html(self) -> None:
        row = _snapshot_row("1", "UPA Barris", NOW + timedelta(hours=4))
        for kind in ("created", "renewed", "liberated", "expired"):
            with self.subTest(kind=kind):
                texto = wa.build_restriction_change_text(kind, row)
                self.assertNotIn("<b>", texto)
                self.assertIn("*", texto)

    def test_liberation_and_expiry_read_differently(self) -> None:
        row = _snapshot_row("1", "UPA Barris", NOW + timedelta(hours=4))
        liberada = wa.build_restriction_change_text("liberated", row)
        vencida = wa.build_restriction_change_text("expired", row)

        self.assertIn("antes do prazo", liberada)
        self.assertIn("venceu", vencida)
        self.assertNotEqual(liberada, vencida)

    def test_unknown_kind_produces_nothing(self) -> None:
        row = _snapshot_row("1", "UPA Barris", NOW + timedelta(hours=4))
        self.assertEqual(wa.build_restriction_change_text("sei_la", row), "")

    def test_deadline_falls_back_to_local_formatting(self) -> None:
        """Sem `untilLabel` o prazo ainda sai legível, no fuso de exibição."""
        row = _snapshot_row("1", "UPA Barris", _local(4, 19), label="")
        self.assertIn("04/08 19:00", wa.build_restriction_change_text("created", row))


# ---------------------------------------------------------------------------
# 6. Travas de envio
# ---------------------------------------------------------------------------


class DispatchTests(unittest.TestCase):
    """Nenhum destes testes pode encostar na rede."""

    def test_disabled_is_dry_run(self) -> None:
        with mock.patch.object(wa, "WHATSAPP_RESTRICTION_ENABLED", False), \
                mock.patch.object(wa, "WHATSAPP_GROUP_TO", "120363@g.us"), \
                mock.patch.object(wa, "send_gateway_message") as send:
            self.assertFalse(wa.dispatch_group_message("🚫 teste"))
        send.assert_not_called()

    def test_missing_group_is_dry_run_even_when_enabled(self) -> None:
        with mock.patch.object(wa, "WHATSAPP_RESTRICTION_ENABLED", True), \
                mock.patch.object(wa, "WHATSAPP_GROUP_TO", ""), \
                mock.patch.object(wa, "send_gateway_message") as send:
            self.assertFalse(wa.dispatch_group_message("🚫 teste"))
        send.assert_not_called()

    def test_empty_message_is_never_sent(self) -> None:
        with mock.patch.object(wa, "WHATSAPP_RESTRICTION_ENABLED", True), \
                mock.patch.object(wa, "WHATSAPP_GROUP_TO", "120363@g.us"), \
                mock.patch.object(wa, "send_gateway_message") as send:
            self.assertFalse(wa.dispatch_group_message("   "))
        send.assert_not_called()

    def test_enabled_sends_to_the_group_jid(self) -> None:
        with mock.patch.object(wa, "WHATSAPP_RESTRICTION_ENABLED", True), \
                mock.patch.object(wa, "WHATSAPP_GROUP_TO", "120363@g.us"), \
                mock.patch.object(wa, "send_gateway_message") as send:
            self.assertTrue(wa.dispatch_group_message("🚫 teste"))
        send.assert_called_once_with("120363@g.us", "🚫 teste")

    def test_gateway_failure_never_raises(self) -> None:
        with mock.patch.object(wa, "WHATSAPP_RESTRICTION_ENABLED", True), \
                mock.patch.object(wa, "WHATSAPP_GROUP_TO", "120363@g.us"), \
                mock.patch.object(wa, "send_gateway_message", side_effect=OSError("recusado")):
            self.assertFalse(wa.dispatch_group_message("🚫 teste"))


# ---------------------------------------------------------------------------
# 7. A cobrança de silêncio no grupo (opt-in)
# ---------------------------------------------------------------------------


class StaleAlertGroupTests(unittest.TestCase):
    """A cobrança de giro parado só chega ao grupo quando o dono liga."""

    def test_group_is_not_a_destination_by_default(self) -> None:
        with mock.patch.object(whatsapp_alerts, "WHATSAPP_ALERT_ENABLED", True), \
                mock.patch.object(whatsapp_alerts, "WHATSAPP_ALERT_TO", "5571999999999"), \
                mock.patch.object(whatsapp_alerts, "WHATSAPP_GROUP_TO", "120363@g.us"), \
                mock.patch.object(whatsapp_alerts, "WHATSAPP_ALERT_TO_GROUP", False):
            self.assertEqual(whatsapp_alerts.alert_destinations(), ["5571999999999"])

    def test_opt_in_adds_the_group(self) -> None:
        with mock.patch.object(whatsapp_alerts, "WHATSAPP_ALERT_ENABLED", True), \
                mock.patch.object(whatsapp_alerts, "WHATSAPP_ALERT_TO", "5571999999999"), \
                mock.patch.object(whatsapp_alerts, "WHATSAPP_GROUP_TO", "120363@g.us"), \
                mock.patch.object(whatsapp_alerts, "WHATSAPP_ALERT_TO_GROUP", True), \
                mock.patch.object(whatsapp_alerts, "send_gateway_message") as send:
            enviado = whatsapp_alerts.dispatch_stale_alert("🔴 cobrança")

        self.assertTrue(enviado)
        self.assertEqual(
            [call.args[0] for call in send.call_args_list], ["5571999999999", "120363@g.us"]
        )

    def test_group_only_when_the_manager_number_is_absent(self) -> None:
        with mock.patch.object(whatsapp_alerts, "WHATSAPP_ALERT_ENABLED", True), \
                mock.patch.object(whatsapp_alerts, "WHATSAPP_ALERT_TO", ""), \
                mock.patch.object(whatsapp_alerts, "WHATSAPP_GROUP_TO", "120363@g.us"), \
                mock.patch.object(whatsapp_alerts, "WHATSAPP_ALERT_TO_GROUP", True):
            self.assertEqual(whatsapp_alerts.alert_destinations(), ["120363@g.us"])

    def test_failure_on_one_destination_does_not_lose_the_other(self) -> None:
        def _falha_no_grupo(destino, _message, mentions=None):
            if destino.endswith("@g.us"):
                raise OSError("recusado")

        with mock.patch.object(whatsapp_alerts, "WHATSAPP_ALERT_ENABLED", True), \
                mock.patch.object(whatsapp_alerts, "WHATSAPP_ALERT_TO", "5571999999999"), \
                mock.patch.object(whatsapp_alerts, "WHATSAPP_GROUP_TO", "120363@g.us"), \
                mock.patch.object(whatsapp_alerts, "WHATSAPP_ALERT_TO_GROUP", True), \
                mock.patch.object(whatsapp_alerts, "send_gateway_message", _falha_no_grupo):
            self.assertTrue(whatsapp_alerts.dispatch_stale_alert("🔴 cobrança"))

    def test_same_jid_twice_is_sent_once(self) -> None:
        with mock.patch.object(whatsapp_alerts, "WHATSAPP_ALERT_ENABLED", True), \
                mock.patch.object(whatsapp_alerts, "WHATSAPP_ALERT_TO", "120363@g.us"), \
                mock.patch.object(whatsapp_alerts, "WHATSAPP_GROUP_TO", "120363@g.us"), \
                mock.patch.object(whatsapp_alerts, "WHATSAPP_ALERT_TO_GROUP", True):
            self.assertEqual(whatsapp_alerts.alert_destinations(), ["120363@g.us"])


if __name__ == "__main__":
    unittest.main()
