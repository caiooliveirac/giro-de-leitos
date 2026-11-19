#!/usr/bin/env python3
"""Reconciliador do giro de leitos: nenhuma mensagem do grupo fica de fora.

Por que existe: em 18-19/11/2026 o gateway WhatsApp (whatsmeow-gw) ficou ~32h
recebendo os giros mas sem conseguir entregar o webhook. As mensagens ficaram
guardadas no histórico do próprio gateway; ninguém as reprocessou.

O que faz, a cada execução (timer de 10 min + logo depois de o watchdog
religar o gateway):

1. lista as mensagens do grupo do giro guardadas no gateway nas últimas
   ``WINDOW_HOURS`` horas (``GET /chat/<jid>/messages``);
2. descarta as que o banco já tem — pelo ``wa_message_id`` ou, para o histórico
   anterior ao id, pelo texto normalizado;
3. reenvia as que faltam ao ingest, em ordem cronológica, com a hora REAL de
   envio (``received_at``), ``wa_message_id`` e ``backfill=true``.

A API garante o resto (ver save_event): mesma mensagem não duplica, giro
enviado antes do que está no painel vai só para o histórico (nunca sobrepõe um
mais recente) e backfill não dispara alerta no grupo. Recuperou algo → um
resumo no Telegram de admin.

Só stdlib. Uso: reconcile.py [--dry-run] [--window-hours N]
"""

from __future__ import annotations

import argparse
import base64
import collections
import fcntl
import json
import os
import re
import subprocess
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

HOME = os.path.expanduser("~ubuntu")
GW_URL = os.getenv("GW_URL", "http://127.0.0.1:3080")
GW_ENV = os.getenv("GW_ENV", f"{HOME}/whatsmeow-gw/env.list")
ADAPTER_ENV = os.getenv("ADAPTER_ENV", f"{HOME}/giro-wa-adapter/.env")
DEPLOY_ENV = os.getenv("DEPLOY_ENV", f"{HOME}/giro-de-leitos/.deploy-env")
INGEST_URL = os.getenv("GIRO_INGEST_URL", "http://127.0.0.1:8000/api/ingest/whatsapp-bridge")
DB_CONTAINER = os.getenv("GIRO_DB_CONTAINER", "giro-de-leitos-db-local")
WINDOW_HOURS = float(os.getenv("WINDOW_HOURS", "72"))
# Mensagem mais nova que isso ainda pode estar a caminho pelo webhook ao vivo.
SETTLE_MINUTES = float(os.getenv("SETTLE_MINUTES", "3"))
MAX_PER_RUN = int(os.getenv("MAX_PER_RUN", "500"))
LOCK = os.getenv("LOCK_FILE", "/tmp/giro-reconcile.lock")


def log(msg: str) -> None:
    print(msg, flush=True)


def read_env(path: str, key: str) -> str:
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                if line.startswith(f"{key}="):
                    return line.split("=", 1)[1].strip()
    except OSError:
        pass
    return ""


def norm_text(text: str | None) -> str:
    return re.sub(r"\s+", " ", (text or "")).strip().casefold()


def parse_ts(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def gw_messages(jid: str, since: datetime) -> list[dict]:
    auth = read_env(GW_ENV, "APP_BASIC_AUTH")
    headers = {"Authorization": "Basic " + base64.b64encode(auth.encode()).decode()} if auth else {}
    out: list[dict] = []
    offset = 0
    start = since.strftime("%Y-%m-%dT%H:%M:%SZ")
    while True:
        q = urllib.parse.urlencode({"limit": 100, "offset": offset, "start_time": start})
        url = f"{GW_URL}/chat/{urllib.parse.quote(jid)}/messages?{q}"
        with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=30) as r:
            body = json.load(r)
        if body.get("code") != "SUCCESS":
            raise RuntimeError(f"gateway respondeu {body.get('code')}: {body.get('message')}")
        page = (body.get("results") or {}).get("data") or []
        out.extend(page)
        if len(page) < 100:
            return out
        offset += 100
        if offset > 20000:
            raise RuntimeError("paginação do gateway não terminou")


def known_in_db(hours: float) -> tuple[set[str], set[str]]:
    # Margem larga no created_at: o lote reentregue em 18/11 foi gravado horas
    # depois de enviado, e o reprocessamento pode demorar o mesmo tanto.
    sql = (
        # to_jsonb(p)->>'wa_message_id': funciona também antes da migração 012
        # (coluna ausente vira NULL em vez de erro).
        "SELECT coalesce(json_agg(json_build_object('id', to_jsonb(p)->>'wa_message_id', 't', p.raw_text)), '[]') "
        "FROM parsed_events p WHERE p.created_at > now() - interval '%d hours'" % int(hours + 48)
    )
    res = subprocess.run(
        ["docker", "exec", DB_CONTAINER, "psql", "-U", "giro", "-d", "giro_de_leitos", "-At", "-c", sql],
        capture_output=True, text=True, timeout=60, check=True,
    )
    rows = json.loads(res.stdout.strip() or "[]")
    ids = {r["id"] for r in rows if r.get("id")}
    texts = {norm_text(r.get("t")) for r in rows if r.get("t")}
    return ids, texts


def ingest(msg: dict) -> dict:
    body = {
        "text": msg["content"],
        "source": "whatsmeow",
        "received_at": msg["timestamp"],
        "wa_message_id": msg["id"],
        "backfill": True,
    }
    phone = re.sub(r"\D", "", str(msg.get("sender_jid") or "").split("@")[0].split(":")[0])
    if 8 <= len(phone) <= 15:
        body["sender_phone"] = phone
    if msg.get("sender_display_name"):
        body["sender_name"] = msg["sender_display_name"]
    req = urllib.request.Request(
        INGEST_URL, json.dumps(body).encode(), {"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def telegram(text: str) -> None:
    token = read_env(DEPLOY_ENV, "TELEGRAM_ALERT_BOT_TOKEN") or read_env(DEPLOY_ENV, "TELEGRAM_BOT_TOKEN")
    chat = read_env(DEPLOY_ENV, "TELEGRAM_ADMIN_CHAT_ID")
    if not token or not chat:
        log("sem token/chat do Telegram: resumo não enviado")
        return
    data = urllib.parse.urlencode({"chat_id": chat, "text": text}).encode()
    try:
        urllib.request.urlopen(f"https://api.telegram.org/bot{token}/sendMessage", data, timeout=15).read()
    except Exception as exc:  # o resumo é acessório; o backfill já foi feito
        log(f"falha ao avisar no Telegram: {exc}")


def unit_of(resp: dict) -> str:
    data = (resp.get("event") or {}).get("data") or {}
    return data.get("upa_name") or resp.get("unit_code") or "?"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="só lista o que falta, não envia nada")
    ap.add_argument("--window-hours", type=float, default=WINDOW_HOURS)
    args = ap.parse_args()

    lock = open(LOCK, "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        log("outra execução em andamento — saindo")
        return 0

    jid = read_env(ADAPTER_ENV, "GIRO_GROUP_JID")
    if not jid:
        log("GIRO_GROUP_JID ausente no .env do adapter")
        return 2

    now = datetime.now(timezone.utc)
    since = now - timedelta(hours=args.window_hours)
    settled = now - timedelta(minutes=SETTLE_MINUTES)

    msgs = gw_messages(jid, since)
    # Mesmo filtro do adapter: só mensagem de grupo com texto. E nunca o que o
    # PRÓPRIO número pareado postou: são os alertas automáticos do sistema
    # ("🔄 Giro de leitos — atualização pendente", services/whatsapp_alerts.py),
    # que o webhook não entrega e que não são giro de unidade nenhuma.
    candidates = [
        m for m in msgs
        if (m.get("content") or "").strip() and m.get("id") and m.get("timestamp")
        and not m.get("is_from_me")
        and since <= parse_ts(m["timestamp"]) <= settled
    ]
    ids, texts = known_in_db(args.window_hours)
    missing = [m for m in candidates if m["id"] not in ids and norm_text(m["content"]) not in texts]
    missing.sort(key=lambda m: parse_ts(m["timestamp"]))
    log(f"gateway: {len(candidates)} mensagens com texto em {args.window_hours:g}h; faltando no banco: {len(missing)}")

    if not missing:
        return 0
    if len(missing) > MAX_PER_RUN:
        log(f"{len(missing)} faltando > MAX_PER_RUN={MAX_PER_RUN}; processando as {MAX_PER_RUN} mais antigas")
        missing = missing[:MAX_PER_RUN]

    if args.dry_run:
        for m in missing:
            log(f"  faltando {m['timestamp']} id={m['id']} len={len(m['content'])}")
        return 0

    statuses: collections.Counter[str] = collections.Counter()
    per_unit: collections.Counter[str] = collections.Counter()
    failures = 0
    for m in missing:
        try:
            resp = ingest(m)
        except Exception as exc:
            failures += 1
            log(f"  ERRO id={m['id']} {m['timestamp']}: {exc}")
            continue
        st = resp.get("status", "?")
        statuses[st] += 1
        if st in ("accepted", "stale"):
            per_unit[unit_of(resp)] += 1
        log(f"  {st:9} {m['timestamp']} id={m['id']} unidade={unit_of(resp) if st in ('accepted', 'stale') else '-'}")

    recovered = statuses["accepted"] + statuses["stale"]
    first, last = missing[0]["timestamp"], missing[-1]["timestamp"]
    log(f"resumo: {dict(statuses)} falhas={failures}")
    if recovered or failures:
        lines = [
            "♻️ Giro de leitos — reconciliação recuperou mensagens que não tinham chegado",
            f"Período: {first[:16].replace('T', ' ')} → {last[:16].replace('T', ' ')} UTC",
            f"Giros lançados: {recovered} (no painel: {statuses['accepted']}; só histórico, já havia giro mais novo: {statuses['stale']})",
            f"Outras mensagens do grupo (sem unidade/conversa): {statuses['pending']}",
        ]
        if per_unit:
            lines.append("Por unidade: " + ", ".join(f"{u} {n}" for u, n in per_unit.most_common()))
        if failures:
            lines.append(f"⚠️ {failures} falharam e serão tentadas de novo na próxima rodada")
        telegram("\n".join(lines))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
