#!/usr/bin/env bash
# status.sh — mostra estado atual do deploy

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=_lib.sh
. "${SCRIPT_DIR}/_lib.sh"

banner "STATUS giro-de-leitos"

load_deploy_env

printf "%bSHAs persistidos em %s%b\n" "$C_BOLD" "$GIRO_ENV_FILE" "$C_RESET"
for k in API_SHA PREVIOUS_API_SHA FRONTEND_SHA PREVIOUS_FRONTEND_SHA; do
    v="$(grep "^${k}=" "$GIRO_ENV_FILE" 2>/dev/null | cut -d= -f2-)"
    printf "  %-26s %s\n" "$k" "${v:-<vazio>}"
done

echo
printf "%bSHAs atualmente em execução (label da imagem)%b\n" "$C_BOLD" "$C_RESET"
printf "  %-26s %s\n" "parser-api"  "$(current_sha_of_container "$API_CONTAINER")"
printf "  %-26s %s\n" "frontend"    "$(current_sha_of_container "$FRONTEND_CONTAINER")"
printf "  %-26s %s\n" "postgres"    "$(current_sha_of_container "$DB_CONTAINER")"

echo
printf "%bContainers%b\n" "$C_BOLD" "$C_RESET"
docker ps --filter "name=giro-de-leitos" --format 'table {{.Names}}\t{{.Status}}\t{{.Image}}'

echo
printf "%bÚltimos 3 deploys (logs)%b\n" "$C_BOLD" "$C_RESET"
ls -1t "$GIRO_LOGS"/deploy-*.log 2>/dev/null | head -3 | while read -r f; do
    last_line="$(grep -E '\[(OK|ERR)\]' "$f" | tail -1 || true)"
    printf "  %s\n    %s\n" "$(basename "$f")" "${last_line:-<sem linha de status>}"
done

echo
printf "%bBackups recentes%b\n" "$C_BOLD" "$C_RESET"
ls -lht "$GIRO_BACKUPS"/db-*.sql.gz 2>/dev/null | head -5 | awk '{print "  "$0}'
