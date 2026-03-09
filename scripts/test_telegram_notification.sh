#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────
# Testa a notificação Telegram do giro SEM afetar o site/dashboard.
# Usa dry_run=true na API para parsear sem salvar ou publicar.
#
# Uso:
#   ./scripts/test_telegram_notification.sh                  # texto de exemplo
#   ./scripts/test_telegram_notification.sh "texto do giro"  # texto customizado
#   UPA_HINT="UPA BROTAS" ./scripts/test_telegram_notification.sh
# ─────────────────────────────────────────────────────────────────────
set -euo pipefail

API_URL="${API_URL:-http://localhost:8000}"
ENDPOINT="/api/ingest/whatsapp-bridge"

# Pega tokens do .env ou do docker-compose
if [[ -z "${TELEGRAM_BOT_TOKEN:-}" || -z "${TELEGRAM_ADMIN_CHAT_ID:-}" ]]; then
    COMPOSE_FILE="$(dirname "$0")/../docker-compose.yml"
    if [[ -f "$COMPOSE_FILE" ]]; then
        TELEGRAM_BOT_TOKEN="${TELEGRAM_BOT_TOKEN:-$(grep -oP 'TELEGRAM_BOT_TOKEN=\K.*' "$COMPOSE_FILE" 2>/dev/null | head -1 || true)}"
        TELEGRAM_ADMIN_CHAT_ID="${TELEGRAM_ADMIN_CHAT_ID:-$(grep -oP 'TELEGRAM_ADMIN_CHAT_ID=\K.*' "$COMPOSE_FILE" 2>/dev/null | head -1 || true)}"
    fi
fi

# Texto de exemplo (ou argumento)
SAMPLE_TEXT="${1:-🏥 UPA BROTAS

⏰ Horário: 14:30

🔴 SALA VERMELHA 03/04
🟡 SALA AMARELA 06/08
🟣 ISOLAMENTO MASC 01/02
🟣 ISOLAMENTO FEM 02/03
🟣 ISOLAMENTO PED 01/01

🚶 CORREDOR:
1. MARIA - DOR TORÁCICA
2. JOÃO - AVC

🦴 ORTOPEDISTA: SIM
🔪 CIRURGIÃO: NÃO
🧠 PSIQUIATRIA: NÃO}"

UPA_HINT="${UPA_HINT:-}"

echo "═══════════════════════════════════════════════════"
echo "  🧪 Teste de notificação Telegram (dry_run)"
echo "═══════════════════════════════════════════════════"
echo ""
echo "📡 API: ${API_URL}${ENDPOINT}"
echo ""

# Montar payload JSON
PAYLOAD=$(jq -n \
    --arg text "$SAMPLE_TEXT" \
    --arg hint "$UPA_HINT" \
    '{text: $text, source: "test-telegram", dry_run: true} + (if $hint != "" then {unit_hint: $hint} else {} end)')

echo "📤 Enviando para API (dry_run=true)..."
RESPONSE=$(curl -s -w "\n%{http_code}" -X POST \
    "${API_URL}${ENDPOINT}" \
    -H "Content-Type: application/json" \
    -d "$PAYLOAD")

HTTP_CODE=$(echo "$RESPONSE" | tail -1)
BODY=$(echo "$RESPONSE" | sed '$d')

if [[ "$HTTP_CODE" != "200" ]]; then
    echo "❌ API retornou HTTP $HTTP_CODE"
    echo "$BODY" | jq . 2>/dev/null || echo "$BODY"
    exit 1
fi

echo "✅ API respondeu HTTP 200"
echo ""

# Extrair dados para montar a notificação
STATUS=$(echo "$BODY" | jq -r '.status // "unknown"')
DRY_RUN_FLAG=$(echo "$BODY" | jq -r '.dry_run // false')
UPA_NAME=$(echo "$BODY" | jq -r '.event.data.upa_name // "UPA não identificada"')
IS_ACCEPTED=$(echo "$BODY" | jq -r 'if .status == "accepted" then "true" else "false" end')
IS_PENDING=$(echo "$BODY" | jq -r 'if .status == "pending" then "true" else "false" end')

# Dados digeridos
RED=$(echo "$BODY" | jq -r '.event.data.rooms.red_room.ratio // "—"')
YELLOW=$(echo "$BODY" | jq -r '.event.data.rooms.yellow_room.ratio // "—"')
YELLOW_M=$(echo "$BODY" | jq -r '.event.data.rooms.yellow_male.ratio // empty' 2>/dev/null || true)
YELLOW_F=$(echo "$BODY" | jq -r '.event.data.rooms.yellow_female.ratio // empty' 2>/dev/null || true)
ISO_MODE=$(echo "$BODY" | jq -r '.event.data.rooms.isolation_mode // "none"')
ISO_TOTAL=$(echo "$BODY" | jq -r '.event.data.rooms.isolation_total.ratio // "—"')
ISO_M=$(echo "$BODY" | jq -r '.event.data.rooms.isolation_male.ratio // empty' 2>/dev/null || true)
ISO_F=$(echo "$BODY" | jq -r '.event.data.rooms.isolation_female.ratio // empty' 2>/dev/null || true)
ISO_P=$(echo "$BODY" | jq -r '.event.data.rooms.isolation_pediatric.ratio // empty' 2>/dev/null || true)
ORTHO=$(echo "$BODY" | jq -r 'if .event.data.specialists.has_orthopedist then "✅" else "❌" end')
SURG=$(echo "$BODY" | jq -r 'if .event.data.specialists.has_surgeon then "✅" else "❌" end')
PSIQ=$(echo "$BODY" | jq -r 'if .event.data.specialists.has_psychiatrist then "✅" else "❌" end')
CORRIDOR=$(echo "$BODY" | jq -r '.event.data.corridor_patients | length')
WARNINGS=$(echo "$BODY" | jq -r '.event.data.warnings | join(" | ") // empty' 2>/dev/null || true)

# Emoji de status
if [[ "$IS_ACCEPTED" == "true" ]]; then
    EMOJI="✅"; STATUS_TEXT="processado"
elif [[ "$IS_PENDING" == "true" ]]; then
    EMOJI="⚠️"; STATUS_TEXT="pendente (dados faltando)"
else
    EMOJI="❌"; STATUS_TEXT="erro: $STATUS"
fi

# Montar mensagem Telegram
MSG="${EMOJI} <b>🧪 TESTE — Giro — ${UPA_NAME}</b>
Status: ${STATUS_TEXT} (dry_run=${DRY_RUN_FLAG})

<b>📊 Dados digeridos:</b>
🔴 Vermelha: ${RED}"

if [[ -n "$YELLOW_M" && "$YELLOW_M" != "null" ]]; then
    MSG+="
🟡 Amarela Masc: ${YELLOW_M}
🟡 Amarela Fem: ${YELLOW_F:-—}"
fi
if [[ "$YELLOW" != "—" ]]; then
    MSG+="
🟡 Amarela: ${YELLOW}"
fi

if [[ "$ISO_MODE" == "split" ]]; then
    [[ -n "$ISO_M" && "$ISO_M" != "null" ]] && MSG+="
🟣 Iso Masc: ${ISO_M}"
    [[ -n "$ISO_F" && "$ISO_F" != "null" ]] && MSG+="
🟣 Iso Fem: ${ISO_F}"
    [[ -n "$ISO_P" && "$ISO_P" != "null" ]] && MSG+="
🟣 Iso Ped: ${ISO_P}"
elif [[ "$ISO_TOTAL" != "—" ]]; then
    MSG+="
🟣 Isolamento: ${ISO_TOTAL}"
fi

MSG+="
🦴 Ortop: ${ORTHO}  🔪 Cirurg: ${SURG}  🧠 Psiq: ${PSIQ}"

if [[ "$CORRIDOR" -gt 0 ]]; then
    MSG+="
🚶 Corredor: ${CORRIDOR} paciente(s)"
fi

if [[ -n "$WARNINGS" ]]; then
    MSG+="

⚠️ ${WARNINGS}"
fi

# Texto bruto truncado
RAW_TEXT=$(echo "$SAMPLE_TEXT" | head -c 2000)
MSG+="

<b>📝 Texto bruto:</b>
<blockquote>${RAW_TEXT}</blockquote>"

echo "═══════════════════════════════════════════════════"
echo "  📨 Mensagem que será enviada ao Telegram:"
echo "═══════════════════════════════════════════════════"
echo ""
echo "$MSG"
echo ""

# Enviar para Telegram
if [[ -z "${TELEGRAM_BOT_TOKEN:-}" || -z "${TELEGRAM_ADMIN_CHAT_ID:-}" ]]; then
    echo "⚠️  TELEGRAM_BOT_TOKEN ou TELEGRAM_ADMIN_CHAT_ID não configurados."
    echo "   Defina-os como variáveis de ambiente para enviar a notificação."
    echo ""
    echo "   Dados parseados (JSON):"
    echo "$BODY" | jq '.event.data | {upa_name, rooms, specialists, corridor_patients, warnings}'
    exit 0
fi

echo "📨 Enviando para Telegram..."
TG_RESPONSE=$(curl -s -X POST \
    "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage" \
    -H "Content-Type: application/json" \
    -d "$(jq -n --arg chat "$TELEGRAM_ADMIN_CHAT_ID" --arg text "$MSG" \
        '{chat_id: $chat, text: $text, parse_mode: "HTML"}')")

TG_OK=$(echo "$TG_RESPONSE" | jq -r '.ok')
if [[ "$TG_OK" == "true" ]]; then
    echo "✅ Notificação enviada com sucesso!"
else
    echo "❌ Falha ao enviar:"
    echo "$TG_RESPONSE" | jq .
fi
