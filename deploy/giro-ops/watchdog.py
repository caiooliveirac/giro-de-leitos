#!/usr/bin/env python3
"""Watchdog do giro de leitos: percebe que a ingestão parou, RELIGA e reconcilia.

Histórico: em 2026-08-22 (18h) e 2026-11-18 (~32h) o whatsmeow-gw foi recriado
sem ``--add-host host.docker.internal:host-gateway``. O gateway seguia
recebendo e guardando as mensagens, mas nenhum webhook saía. O watchdog
antigo avisava no Telegram a cada 3h e ninguém agia. Este age.

A cada execução (timer de 5 min):

1. **Configuração do gateway** — container rodando, ``host.docker.internal``
   resolvendo, redes ``bridge`` e ``escala_default`` conectadas. Desviou →
   recria pelo ``~/whatsmeow-gw/run.sh`` (a sessão vive no volume, não pede QR).
2. **Sessão do WhatsApp** — ``/app/status``. Deslogado não tem conserto
   automático (precisa de QR): alerta.
3. **Entrega** — o gateway tem mensagem do grupo há mais de ``LAG_MIN`` que
   não chegou ao adapter → reinicia o adapter; persistindo, recria o gateway.
4. **Silêncio total** — nenhum webhook (de chat nenhum) e nada novo no grupo
   há ``SILENCE_MIN`` (madrugada normal: até ~40 min) → ``/app/reconnect``;
   persistindo, recria o gateway e alerta.
5. Depois de qualquer conserto, dispara o reconciliador (``giro-reconcile``),
   que lança o que se perdeu com a hora certa e sem sobrepor giro mais novo.

Consertos são limitados (``HEAL_COOLDOWN_MIN``) para não entrar em laço;
alertas têm cooldown e há aviso de recuperação quando volta ao normal.
"""

from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import reconcile as R  # noqa: E402

GW = os.getenv("GW_CONTAINER", "whatsmeow-gw")
RUN_SH = os.getenv("GW_RUN_SH", f"{R.HOME}/whatsmeow-gw/run.sh")
STATE = os.getenv("WATCHDOG_STATE", f"{R.HOME}/giro-ops/.watchdog-state.json")
LAG_MIN = float(os.getenv("LAG_MIN", "10"))
SILENCE_MIN = float(os.getenv("SILENCE_MIN", "90"))
HEAL_COOLDOWN_MIN = float(os.getenv("HEAL_COOLDOWN_MIN", "20"))
ALERT_COOLDOWN_MIN = float(os.getenv("ALERT_COOLDOWN_MIN", "180"))
REQUIRED_NETWORKS = ("wa-net", "bridge", "escala_default")
# Ensaio: diagnostica e diz o que faria, sem consertar nem mandar mensagem.
DRY = os.getenv("WATCHDOG_DRY") == "1"
if DRY:
    R.telegram = lambda text: R.log(f"[dry] telegram: {text}")


def sh(*cmd: str, timeout: int = 60) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


def load_state() -> dict:
    try:
        with open(STATE, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def save_state(state: dict) -> None:
    os.makedirs(os.path.dirname(STATE), exist_ok=True)
    tmp = STATE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(state, fh)
    os.replace(tmp, STATE)


def gw_get(path: str) -> dict:
    auth = R.read_env(R.GW_ENV, "APP_BASIC_AUTH")
    headers = {"Authorization": "Basic " + base64.b64encode(auth.encode()).decode()} if auth else {}
    with urllib.request.urlopen(urllib.request.Request(R.GW_URL + path, headers=headers), timeout=20) as r:
        return json.load(r)


def config_problems() -> list[str]:
    insp = sh("docker", "inspect", GW)
    if insp.returncode != 0:
        return ["container whatsmeow-gw não existe"]
    info = json.loads(insp.stdout)[0]
    problems = []
    if not info["State"].get("Running"):
        problems.append("whatsmeow-gw parado")
        return problems
    extra = info["HostConfig"].get("ExtraHosts") or []
    if not any(h.startswith("host.docker.internal:") for h in extra):
        problems.append("whatsmeow-gw sem --add-host host.docker.internal")
    nets = set((info["NetworkSettings"].get("Networks") or {}).keys())
    missing = [n for n in REQUIRED_NETWORKS if n not in nets]
    if missing:
        problems.append("whatsmeow-gw fora das redes " + ", ".join(missing))
    dns = sh("docker", "exec", GW, "getent", "hosts", "host.docker.internal", timeout=20)
    if dns.returncode != 0 or not dns.stdout.strip():
        problems.append("whatsmeow-gw não resolve host.docker.internal (webhooks mortos)")
    return problems


def last_hook_at() -> datetime | None:
    out = sh("journalctl", "-u", "giro-wa-adapter", "--since", "-24h", "-o", "short-unix", "--no-pager").stdout
    last = None
    for line in out.splitlines():
        if "[hook]" in line:
            last = line.split(" ", 1)[0]
    return datetime.fromtimestamp(float(last), timezone.utc) if last else None


def last_group_message_at(jid: str) -> datetime | None:
    q = urllib.parse.urlencode({"limit": 20})
    data = gw_get(f"/chat/{urllib.parse.quote(jid)}/messages?{q}")
    stamps = [
        R.parse_ts(m["timestamp"])
        for m in ((data.get("results") or {}).get("data") or [])
        if m.get("timestamp") and not m.get("is_from_me")
    ]
    return max(stamps) if stamps else None


def alert(state: dict, key: str, text: str) -> None:
    now = time.time()
    sent = state.setdefault("alerts", {})
    if now - sent.get(key, 0) < ALERT_COOLDOWN_MIN * 60:
        return
    R.telegram(text)
    sent[key] = now
    R.log(f"alerta enviado: {text}")


def can_heal(state: dict, what: str) -> bool:
    last = state.get("heals", {}).get(what, 0)
    return time.time() - last >= HEAL_COOLDOWN_MIN * 60


def mark_heal(state: dict, what: str) -> None:
    state.setdefault("heals", {})[what] = time.time()
    state["healed_pending_reconcile"] = True


def recreate_gateway(state: dict, reason: str) -> bool:
    if not can_heal(state, "gateway"):
        R.log(f"gateway precisa de conserto ({reason}), mas já recriei há menos de {HEAL_COOLDOWN_MIN:g} min")
        return False
    R.log(f"recriando whatsmeow-gw via run.sh: {reason}")
    if DRY:
        R.log("[dry] run.sh não executado")
        return False
    mark_heal(state, "gateway")
    res = sh("bash", RUN_SH, timeout=180)
    ok = res.returncode == 0 and not config_problems()
    R.log(("run.sh OK" if ok else f"run.sh FALHOU rc={res.returncode}: {res.stderr[-400:]}"))
    if ok:
        R.telegram(f"🔧 Giro de leitos: gateway WhatsApp RELIGADO automaticamente ({reason}). Reconciliando mensagens perdidas.")
    else:
        alert(state, "heal-failed", f"🚨 Giro de leitos: tentei religar o gateway ({reason}) e FALHOU. Precisa de gente: bash ~/whatsmeow-gw/run.sh · docker logs --tail 30 whatsmeow-gw")
    return ok


def main() -> int:
    state = load_state()
    jid = R.read_env(R.ADAPTER_ENV, "GIRO_GROUP_JID")
    now = datetime.now(timezone.utc)
    problems: list[str] = []

    # 1. Configuração do container.
    cfg = config_problems()
    if cfg:
        problems += cfg
        recreate_gateway(state, "; ".join(cfg))

    # 2. Sessão do WhatsApp.
    try:
        st = (gw_get("/app/status").get("results") or {})
        if not st.get("is_logged_in"):
            problems.append("WhatsApp DESLOGADO no gateway")
            alert(state, "logout", "🚨 Giro de leitos: o número do gateway foi DESLOGADO do WhatsApp. Precisa parear de novo (QR) — nada chega até lá.")
        elif not st.get("is_connected") and can_heal(state, "reconnect"):
            problems.append("gateway desconectado do WhatsApp")
            mark_heal(state, "reconnect")
            if not DRY:
                gw_get("/app/reconnect")
            R.log("gateway desconectado: /app/reconnect enviado")
    except Exception as exc:
        problems.append(f"API do gateway não responde ({exc})")
        if not cfg:
            recreate_gateway(state, "API do gateway não responde")

    # 3/4. Entrega e silêncio.
    try:
        gw_last = last_group_message_at(jid) if jid else None
    except Exception:
        gw_last = None
    hook_last = last_hook_at()
    # Mensagem que um conserto anterior já tratou não conta de novo (a
    # reconciliação lança ela); só mensagem NOVA presa escala o conserto.
    acked = R.parse_ts(state["lag_ack"]) if state.get("lag_ack") else None
    lagging = (
        gw_last is not None
        and (acked is None or gw_last > acked)
        and (hook_last is None or gw_last - hook_last > timedelta(minutes=LAG_MIN))
        and now - gw_last > timedelta(minutes=2)
    )
    silent = (
        (hook_last is None or now - hook_last > timedelta(minutes=SILENCE_MIN))
        and (gw_last is None or now - gw_last > timedelta(minutes=SILENCE_MIN))
    )
    if lagging:
        lag = "nunca" if hook_last is None else f"{(gw_last - hook_last).total_seconds() / 60:.0f} min"
        problems.append(f"gateway recebeu mensagem que não chegou ao adapter (atraso {lag})")
        state["lag_ack"] = gw_last.isoformat()
        if can_heal(state, "adapter"):
            mark_heal(state, "adapter")
            R.log("reiniciando giro-wa-adapter")
            if not DRY:
                sh("sudo", "-n", "systemctl", "restart", "giro-wa-adapter")
        else:
            recreate_gateway(state, "mensagens não chegam ao adapter mesmo depois de reiniciá-lo")
    elif silent:
        mins = (now - (hook_last or now - timedelta(days=1))).total_seconds() / 60
        if can_heal(state, "reconnect"):
            mark_heal(state, "reconnect")
            state["silence_strikes"] = state.get("silence_strikes", 0) + 1
            try:
                if not DRY:
                    gw_get("/app/reconnect")
                R.log(f"silêncio de {mins:.0f} min: /app/reconnect enviado")
            except Exception as exc:
                R.log(f"/app/reconnect falhou: {exc}")
        if state.get("silence_strikes", 0) >= 2:
            problems.append(f"nenhuma mensagem do WhatsApp há {mins:.0f} min, mesmo depois de reconectar")
            recreate_gateway(state, "silêncio longo mesmo depois de reconectar")
    else:
        state.pop("silence_strikes", None)

    # 5. Reconciliar depois de conserto (e o timer próprio cobre o resto).
    if state.pop("healed_pending_reconcile", False) and not DRY:
        sh("sudo", "-n", "systemctl", "start", "--no-block", "giro-reconcile.service")
        R.log("reconciliação disparada")

    if problems:
        R.log("PROBLEMAS: " + " | ".join(problems))
        if not state.get("down_since"):
            state["down_since"] = time.time()
        alert(state, "down", "⚠️ Giro de leitos com problema: " + "; ".join(problems) + ". Tentando corrigir sozinho; a reconciliação lança o que faltar.")
    elif state.get("down_since"):
        mins = (time.time() - state["down_since"]) / 60
        R.telegram(f"✅ Giro de leitos normalizado (problema durou ~{mins:.0f} min). Mensagens do período são reconciliadas automaticamente.")
        state.pop("down_since", None)
        state.pop("alerts", None)
    if not DRY:
        save_state(state)
    else:
        R.log(f"[dry] estado final: {json.dumps(state)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
