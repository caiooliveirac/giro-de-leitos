#!/usr/bin/env bash
# deploy.sh — entrypoint de deploy de produção
#
# Uso:
#   ./deploy.sh [--component api|frontend|both] [--skip-backup] [--skip-healthcheck]
#
# Fluxo:
#   1) Carrega /opt/giro/.deploy-env
#   2) git fetch && git pull origin main
#   3) Captura SHA atual rodando em cada container → PREVIOUS_*
#   4) Calcula novo SHA (git rev-parse --short HEAD)
#   5) Build local (giro-api:$SHA, giro-frontend:$SHA)
#   6) pre-deploy.sh (backup db + sanity)
#   7) Persiste API_SHA/FRONTEND_SHA novos em $GIRO_ENV_FILE
#   8) Swap atômico: compose up -d --no-deps <svc>
#   9) healthcheck.sh — se falha → rollback automático + exit 1
#  10) Resumo
#
# REGRA: NÃO usa --remove-orphans, NÃO toca whatsapp-bridge, NÃO toca postgres.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=_lib.sh
. "${SCRIPT_DIR}/_lib.sh"

COMPONENT="both"
SKIP_BACKUP=0
SKIP_HEALTHCHECK=0

while [ $# -gt 0 ]; do
    case "$1" in
        --component) COMPONENT="$2"; shift 2;;
        --skip-backup) SKIP_BACKUP=1; shift;;
        --skip-healthcheck) SKIP_HEALTHCHECK=1; shift;;
        -h|--help)
            sed -n '1,30p' "$0"; exit 0;;
        *) die "flag desconhecida: $1";;
    esac
done
case "$COMPONENT" in
    api|frontend|both) ;;
    *) die "--component deve ser api|frontend|both";;
esac

banner "DEPLOY giro-de-leitos ($COMPONENT)"
log INFO "log: $LOG_FILE"

# 1) env --------------------------------------------------------------
load_deploy_env

# 2) git pull --------------------------------------------------------
cd "$GIRO_REPO"
log INFO "git fetch && pull origin main"
git fetch origin
git pull --ff-only origin main || die "git pull não conseguiu fast-forward — resolva manualmente"

NEW_SHA="$(git rev-parse --short HEAD)"
log INFO "HEAD agora: $NEW_SHA ($(git log -1 --pretty=%s))"

# 3) PREVIOUS = SHA rodando agora ------------------------------------
PREV_API_RUNNING="$(current_sha_of_container "$API_CONTAINER" 2>/dev/null || echo unknown)"
PREV_FRONTEND_RUNNING="$(current_sha_of_container "$FRONTEND_CONTAINER" 2>/dev/null || echo unknown)"
log INFO "rodando agora: api=$PREV_API_RUNNING frontend=$PREV_FRONTEND_RUNNING"

# 4/5) Build ----------------------------------------------------------
if [ "$COMPONENT" = "api" ] || [ "$COMPONENT" = "both" ]; then
    banner "BUILD giro-api:$NEW_SHA"
    docker build -t "giro-api:$NEW_SHA" -t "giro-api:latest" "$GIRO_REPO" \
        || die "build da api falhou"
fi
if [ "$COMPONENT" = "frontend" ] || [ "$COMPONENT" = "both" ]; then
    banner "BUILD giro-frontend:$NEW_SHA"
    docker build -t "giro-frontend:$NEW_SHA" -t "giro-frontend:latest" \
        -f "$GIRO_REPO/frontend/Dockerfile" "$GIRO_REPO/frontend" \
        || die "build do frontend falhou"
fi

# 6) pre-deploy -------------------------------------------------------
if [ "$SKIP_BACKUP" -eq 1 ]; then
    log WARN "--skip-backup ativo — PERIGOSO"
else
    "${SCRIPT_DIR}/pre-deploy.sh" || die "pre-deploy falhou"
fi

# 7) Persiste env -----------------------------------------------------
if [ "$COMPONENT" = "api" ] || [ "$COMPONENT" = "both" ]; then
    persist_env_var PREVIOUS_API_SHA "$PREV_API_RUNNING"
    persist_env_var API_SHA "$NEW_SHA"
fi
if [ "$COMPONENT" = "frontend" ] || [ "$COMPONENT" = "both" ]; then
    persist_env_var PREVIOUS_FRONTEND_SHA "$PREV_FRONTEND_RUNNING"
    persist_env_var FRONTEND_SHA "$NEW_SHA"
fi

# 8) Swap atômico -----------------------------------------------------
banner "SWAP"
load_deploy_env  # recarrega para o compose ver API_SHA / FRONTEND_SHA novos
if [ "$COMPONENT" = "api" ] || [ "$COMPONENT" = "both" ]; then
    log INFO "compose up -d --no-deps parser-api  (API_SHA=$NEW_SHA)"
    API_SHA="$NEW_SHA" FRONTEND_SHA="${FRONTEND_SHA:-latest}" \
        compose up -d --no-deps parser-api \
        || die "swap da api falhou"
fi
if [ "$COMPONENT" = "frontend" ] || [ "$COMPONENT" = "both" ]; then
    log INFO "compose up -d --no-deps frontend  (FRONTEND_SHA=$NEW_SHA)"
    FRONTEND_SHA="$NEW_SHA" API_SHA="${API_SHA:-latest}" \
        compose up -d --no-deps frontend \
        || die "swap do frontend falhou"
fi

# 9) Healthcheck ------------------------------------------------------
if [ "$SKIP_HEALTHCHECK" -eq 1 ]; then
    log WARN "--skip-healthcheck ativo — PERIGOSO"
else
    HC_FLAGS=()
    if [ "$COMPONENT" = "frontend" ] || [ "$COMPONENT" = "both" ]; then
        HC_FLAGS+=(--include-frontend)
    fi
    if ! "${SCRIPT_DIR}/healthcheck.sh" "${HC_FLAGS[@]}"; then
        log ERR "healthcheck falhou — iniciando rollback automático"
        "${SCRIPT_DIR}/rollback.sh" "$COMPONENT" || log ERR "rollback automático também falhou — INTERVIR MANUALMENTE"
        die "deploy abortado por falha de healthcheck"
    fi
fi

# 10) Resumo ----------------------------------------------------------
banner "DEPLOY CONCLUÍDO"
log OK "componente: $COMPONENT"
log OK "novo SHA: $NEW_SHA"
log OK "  API_SHA=$(grep '^API_SHA=' "$GIRO_ENV_FILE" 2>/dev/null | cut -d= -f2-)"
log OK "  FRONTEND_SHA=$(grep '^FRONTEND_SHA=' "$GIRO_ENV_FILE" 2>/dev/null | cut -d= -f2-)"
log OK "  PREVIOUS_API_SHA=$(grep '^PREVIOUS_API_SHA=' "$GIRO_ENV_FILE" 2>/dev/null | cut -d= -f2-)"
log OK "  PREVIOUS_FRONTEND_SHA=$(grep '^PREVIOUS_FRONTEND_SHA=' "$GIRO_ENV_FILE" 2>/dev/null | cut -d= -f2-)"
log OK ""
log OK "Para reverter:"
log OK "  ${SCRIPT_DIR}/rollback.sh $COMPONENT"
