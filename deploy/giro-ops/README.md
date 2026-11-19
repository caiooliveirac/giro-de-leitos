# giro-ops — a ingestão do giro não pode parar calada

Roda **no host** (systemd), fora do compose. Fonte de verdade aqui; a cópia em
uso fica em `~/giro-ops/` (e o `run.sh` em `~/whatsmeow-gw/run.sh`).

```
WhatsApp ─▶ whatsmeow-gw ──webhook──▶ giro-wa-adapter :3081 ──▶ API /api/ingest/whatsapp-bridge
               │ guarda o histórico                                 ▲
               └──── GET /chat/<jid>/messages ◀── reconcile.py ─────┘ (backfill)
                                   watchdog.py: detecta, religa, dispara o reconcile
```

## Por quê

Em 2026-08-22 (18h) e 2026-11-18 (~32h) o `whatsmeow-gw` foi recriado à mão
sem `--add-host host.docker.internal:host-gateway`. O gateway seguia recebendo
e guardando as mensagens, mas nenhum webhook saía. O watchdog antigo só
avisava no Telegram, e os giros do período nunca foram lançados.

## As quatro garantias

| | Quem | Como |
|---|---|---|
| Perceber | `watchdog.py` (5 min) | config do container (add-host, redes, DNS), sessão (`/app/status`), mensagem no gateway que não virou `[hook]` no adapter, silêncio total > 90 min |
| Religar | `watchdog.py` | reinicia o adapter → recria o gateway por `whatsmeow-gw-run.sh` → `/app/reconnect`. Cooldown de 20 min por ação; falhou → alerta "precisa de gente". Deslogado (QR) só alerta |
| Não perder | `reconcile.py` (10 min + logo após conserto) | compara as mensagens do grupo guardadas no gateway (72h) com o banco (`wa_message_id` ou texto) e reenvia as que faltam, em ordem, com a hora real de envio |
| Não sobrepor | API (`save_event`) | `wa_message_id` único (reenvio não duplica); mensagem enviada antes da que está no painel vai só para o histórico (`status: stale`); `backfill=true` não dispara alerta no grupo |

O reconciliador ignora o que o **próprio número pareado** posta no grupo
(`is_from_me`): são os alertas automáticos "🔄 Giro de leitos — atualização
pendente", não giro de unidade.

## Operar

```bash
python3 -I ~/giro-ops/reconcile.py --dry-run            # o que falta, sem enviar
python3 -I ~/giro-ops/reconcile.py --window-hours 168   # buraco maior que 72h
WATCHDOG_DRY=1 python3 -I ~/giro-ops/watchdog.py        # diagnóstico sem agir
journalctl -u giro-watchdog -u giro-reconcile --since today
```

Estado do watchdog (cooldowns, alertas): `~/giro-ops/.watchdog-state.json`.

## Instalar / atualizar

```bash
mkdir -p ~/giro-ops
cp deploy/giro-ops/{watchdog.py,reconcile.py} ~/giro-ops/
cp deploy/giro-ops/whatsmeow-gw-run.sh ~/whatsmeow-gw/run.sh
sudo cp deploy/giro-ops/systemd/* /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now giro-watchdog.timer giro-reconcile.timer
```

**Nunca recriar o `whatsmeow-gw` com `docker run` na mão** — sempre
`bash ~/whatsmeow-gw/run.sh`. (Se esquecer, o watchdog recria em ≤5 min.)
