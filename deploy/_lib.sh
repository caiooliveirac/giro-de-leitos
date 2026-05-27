#!/usr/bin/env bash
# Helpers compartilhados entre scripts de deploy.
# `source` este arquivo, não rode direto.

set -euo pipefail

# Cores (desliga se stdout não for tty)
if [ -t 1 ]; then
    C_RESET='\033[0m'
    C_RED='\033[0;31m'
    C_GREEN='\033[0;32m'
    C_YELLOW='\033[0;33m'
    C_BLUE='\033[0;34m'
    C_BOLD='\033[1m'
else
    C_RESET=''; C_RED=''; C_GREEN=''; C_YELLOW=''; C_BLUE=''; C_BOLD=''
fi

# Paths padrão em prod (sobrescrever via env se necessário)
: "${GIRO_ROOT:=/opt/giro}"
: "${GIRO_REPO:=${GIRO_ROOT}/giro-de-leitos}"
: "${GIRO_LOGS:=${GIRO_ROOT}/logs}"
: "${GIRO_BACKUPS:=${GIRO_ROOT}/backups}"
: "${GIRO_ENV_FILE:=${GIRO_ROOT}/.deploy-env}"
: "${COMPOSE_FILE:=${GIRO_REPO}/docker-compose.prod.yml}"

# Container names (devem casar com docker-compose.prod.yml)
: "${API_CONTAINER:=giro-de-leitos-api}"
: "${FRONTEND_CONTAINER:=giro-de-leitos-frontend}"
: "${DB_CONTAINER:=giro-de-leitos-db}"

mkdir -p "${GIRO_LOGS}" "${GIRO_BACKUPS}"

# Timestamp e log file por execução
TS="$(date +%Y%m%dT%H%M%S)"
: "${LOG_FILE:=${GIRO_LOGS}/deploy-${TS}.log}"

log() {
    local level="$1"; shift
    local color=""
    case "$level" in
        INFO)  color="$C_BLUE";;
        OK)    color="$C_GREEN";;
        WARN)  color="$C_YELLOW";;
        ERR)   color="$C_RED";;
        *)     color="";;
    esac
    local line
    line="$(date '+%Y-%m-%d %H:%M:%S') [${level}] $*"
    printf "%b%s%b\n" "$color" "$line" "$C_RESET"
    printf "%s\n" "$line" >> "$LOG_FILE"
}

banner() {
    local msg="$*"
    printf "%b\n" "${C_BOLD}${C_BLUE}=== ${msg} ===${C_RESET}"
    printf "=== %s ===\n" "$msg" >> "$LOG_FILE"
}

die() {
    log ERR "$*"
    exit 1
}

# Carrega /opt/giro/.deploy-env se existir. Não falha se ausente — várias
# variáveis têm default no compose.
load_deploy_env() {
    if [ -f "$GIRO_ENV_FILE" ]; then
        # shellcheck disable=SC1090
        set -a; . "$GIRO_ENV_FILE"; set +a
        log INFO "carregado $GIRO_ENV_FILE"
    else
        log WARN "$GIRO_ENV_FILE não existe — usando defaults do compose"
    fi
}

# Retorna SHA atual rodando no container (label org.opencontainers.image.revision)
# ou a tag da imagem após `:`.
current_sha_of_container() {
    local container="$1"
    local image
    image="$(docker inspect --format='{{.Config.Image}}' "$container" 2>/dev/null || echo "")"
    if [ -z "$image" ]; then
        echo "unknown"
        return
    fi
    # giro-api:abc1234 -> abc1234
    echo "${image##*:}"
}

# Persiste pair (KEY=VALUE) em $GIRO_ENV_FILE. Cria arquivo se ausente.
persist_env_var() {
    local key="$1"
    local val="$2"
    touch "$GIRO_ENV_FILE"
    if grep -qE "^${key}=" "$GIRO_ENV_FILE" 2>/dev/null; then
        # macOS sed precisa de '', mas em prod é GNU sed
        sed -i.bak "s|^${key}=.*|${key}=${val}|" "$GIRO_ENV_FILE"
        rm -f "${GIRO_ENV_FILE}.bak"
    else
        printf "%s=%s\n" "$key" "$val" >> "$GIRO_ENV_FILE"
    fi
}

compose() {
    docker compose -f "$COMPOSE_FILE" "$@"
}
