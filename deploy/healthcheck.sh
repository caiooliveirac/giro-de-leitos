#!/usr/bin/env bash
# healthcheck.sh
#
# Verifica saúde pós-deploy. Exit 0 = OK, ≥1 = falha (motivo no stderr).
#
# Flags:
#   --api-url URL          URL base do parser-api (default: http://localhost:8000
#                          via `docker exec` no container)
#   --include-frontend     Também testa https://giro.mnrs.com.br/
#   --frontend-url URL     Sobrescreve URL do frontend
#   --max-retries N        (default 10) tentativas com backoff 1s
#   --stale-hours N        (default 6) janela aceita p/ updated_at recente
#
# Retornos:
#   0 = OK
#   1 = api /health falhou
#   2 = api /api/summary falhou
#   3 = frontend falhou
#   (warn → exit 0) updated_at vazio é apenas aviso (bridge pode estar
#   dormente fora de horário de plantão)

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=_lib.sh
. "${SCRIPT_DIR}/_lib.sh"

API_URL=""
FRONTEND_URL="https://giro.mnrs.com.br/"
INCLUDE_FRONTEND=0
MAX_RETRIES=10
STALE_HOURS=6

while [ $# -gt 0 ]; do
    case "$1" in
        --api-url) API_URL="$2"; shift 2;;
        --include-frontend) INCLUDE_FRONTEND=1; shift;;
        --frontend-url) FRONTEND_URL="$2"; shift 2;;
        --max-retries) MAX_RETRIES="$2"; shift 2;;
        --stale-hours) STALE_HOURS="$2"; shift 2;;
        *) die "flag desconhecida: $1";;
    esac
done

banner "HEALTHCHECK"

# --- 1) GET /api/health -------------------------------------------------
log INFO "verificando /api/health (até ${MAX_RETRIES}x)"
ok=0
for i in $(seq 1 "$MAX_RETRIES"); do
    if [ -n "$API_URL" ]; then
        body="$(curl -fsS "${API_URL%/}/api/health" 2>/dev/null || true)"
    else
        # Via docker exec — independente de nginx/DNS
        body="$(docker exec "$API_CONTAINER" sh -c 'wget -qO- http://127.0.0.1:8000/api/health 2>/dev/null || curl -fsS http://127.0.0.1:8000/api/health 2>/dev/null' || true)"
    fi
    if printf '%s' "$body" | grep -q '"status".*"ok"'; then
        ok=1
        log OK "api /health respondendo (tentativa $i)"
        break
    fi
    sleep 1
done
[ "$ok" -eq 1 ] || { log ERR "/api/health não respondeu OK em ${MAX_RETRIES}s"; exit 1; }

# --- 2) GET /api/summary ------------------------------------------------
log INFO "verificando /api/summary"
if [ -n "$API_URL" ]; then
    summary="$(curl -fsS "${API_URL%/}/api/summary" 2>/dev/null || true)"
else
    summary="$(docker exec "$API_CONTAINER" sh -c 'wget -qO- http://127.0.0.1:8000/api/summary 2>/dev/null || curl -fsS http://127.0.0.1:8000/api/summary 2>/dev/null' || true)"
fi
if ! printf '%s' "$summary" | grep -q '"units"'; then
    log ERR "/api/summary não retornou shape esperado (campo 'units' ausente)"
    log ERR "resposta: $(printf '%s' "$summary" | head -c 200)"
    exit 2
fi
log OK "/api/summary OK"

# --- 3) Sinal-de-vida do parser via current_unit_status ----------------
log INFO "verificando atividade recente em current_unit_status (janela ${STALE_HOURS}h)"
PG_USER="$(docker inspect --format='{{range .Config.Env}}{{println .}}{{end}}' "$DB_CONTAINER" 2>/dev/null | awk -F= '/^POSTGRES_USER=/{print $2}')"
PG_DB="$(docker inspect --format='{{range .Config.Env}}{{println .}}{{end}}' "$DB_CONTAINER" 2>/dev/null | awk -F= '/^POSTGRES_DB=/{print $2}')"
: "${PG_USER:=giro}"
: "${PG_DB:=giro_de_leitos}"

set +e
recent="$(docker exec "$DB_CONTAINER" psql -U "$PG_USER" -d "$PG_DB" -tAc \
    "SELECT COUNT(*) FROM current_unit_status WHERE updated_at > NOW() - INTERVAL '${STALE_HOURS} hours';" 2>/dev/null)"
rc=$?
set -e
if [ $rc -ne 0 ]; then
    log WARN "não foi possível consultar current_unit_status (psql rc=$rc) — seguindo"
elif [ "${recent:-0}" = "0" ]; then
    log WARN "nenhuma unidade com updated_at nas últimas ${STALE_HOURS}h"
    log WARN "  → pode ser normal fora de horário de plantão"
    log WARN "  → ou indicar que a whatsapp-bridge não está postando"
else
    log OK "${recent} unidade(s) com updated_at na janela"
fi

# --- 4) Frontend (opcional) --------------------------------------------
if [ "$INCLUDE_FRONTEND" -eq 1 ]; then
    log INFO "verificando frontend em $FRONTEND_URL (até ${MAX_RETRIES}x)"
    ok=0
    for i in $(seq 1 "$MAX_RETRIES"); do
        body="$(curl -fsSL "$FRONTEND_URL" 2>/dev/null | head -c 200 || true)"
        if printf '%s' "$body" | grep -qi '<!doctype html'; then
            ok=1
            log OK "frontend respondendo HTML (tentativa $i)"
            break
        fi
        sleep 1
    done
    [ "$ok" -eq 1 ] || { log ERR "frontend não respondeu HTML em ${MAX_RETRIES}s"; exit 3; }
fi

log OK "healthcheck passou"
exit 0
