#!/usr/bin/env bash
# rollback.sh — volta para o SHA PREVIOUS registrado em $GIRO_ENV_FILE
#
# Uso: rollback.sh api | frontend | both
#
# Não builda nada. Apenas faz `compose up -d --no-deps` apontando para a tag
# anterior. Tempo esperado: <5s. NÃO toca whatsapp-bridge nem postgres.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=_lib.sh
. "${SCRIPT_DIR}/_lib.sh"

COMPONENT="${1:-both}"
case "$COMPONENT" in
    api|frontend|both) ;;
    *) die "uso: $0 api|frontend|both";;
esac

banner "ROLLBACK ($COMPONENT)"

load_deploy_env

PREV_API="${PREVIOUS_API_SHA:-}"
PREV_FRONTEND="${PREVIOUS_FRONTEND_SHA:-}"
CUR_API="${API_SHA:-}"
CUR_FRONTEND="${FRONTEND_SHA:-}"

did_anything=0

rollback_one() {
    local name="$1"   # parser-api | frontend
    local prev="$2"
    local cur="$3"
    local var_sha="$4"  # API_SHA | FRONTEND_SHA
    local var_prev="$5" # PREVIOUS_API_SHA | PREVIOUS_FRONTEND_SHA

    if [ -z "$prev" ]; then
        log WARN "$name: PREVIOUS SHA não registrado — nada a rolar"
        return
    fi
    if [ "$prev" = "$cur" ]; then
        log WARN "$name: PREVIOUS == CURRENT ($prev) — nada a fazer"
        return
    fi

    log INFO "$name: $cur → $prev"
    # Verifica que a imagem ainda existe localmente
    local image
    if [ "$name" = "parser-api" ]; then image="giro-api:$prev"; else image="giro-frontend:$prev"; fi
    if ! docker image inspect "$image" >/dev/null 2>&1; then
        die "$name: imagem $image não existe localmente — rollback impossível. Rebuild a partir do commit $prev."
    fi

    # Troca CURRENT/PREVIOUS no env file
    persist_env_var "$var_prev" "$cur"
    persist_env_var "$var_sha"  "$prev"

    # shellcheck disable=SC2086
    if [ "$name" = "parser-api" ]; then
        API_SHA="$prev" compose up -d --no-deps parser-api
    else
        FRONTEND_SHA="$prev" compose up -d --no-deps frontend
    fi

    did_anything=1
}

if [ "$COMPONENT" = "api" ] || [ "$COMPONENT" = "both" ]; then
    rollback_one parser-api "$PREV_API" "$CUR_API" API_SHA PREVIOUS_API_SHA
fi
if [ "$COMPONENT" = "frontend" ] || [ "$COMPONENT" = "both" ]; then
    rollback_one frontend "$PREV_FRONTEND" "$CUR_FRONTEND" FRONTEND_SHA PREVIOUS_FRONTEND_SHA
fi

if [ "$did_anything" -eq 0 ]; then
    log WARN "nenhum rollback executado"
    exit 0
fi

# Healthcheck pós-rollback (sem incluir frontend público se for só api)
HC_FLAGS=()
if [ "$COMPONENT" = "frontend" ] || [ "$COMPONENT" = "both" ]; then
    HC_FLAGS+=(--include-frontend)
fi
"${SCRIPT_DIR}/healthcheck.sh" "${HC_FLAGS[@]}" || die "healthcheck pós-rollback falhou — verifique manualmente"

log OK "rollback concluído"
log OK "  API_SHA agora: $(grep '^API_SHA=' "$GIRO_ENV_FILE" 2>/dev/null | cut -d= -f2-)"
log OK "  FRONTEND_SHA agora: $(grep '^FRONTEND_SHA=' "$GIRO_ENV_FILE" 2>/dev/null | cut -d= -f2-)"
