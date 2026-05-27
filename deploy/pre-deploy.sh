#!/usr/bin/env bash
# pre-deploy.sh
#
# Tarefas pré-deploy:
#   1) pg_dump do banco para /opt/giro/backups/db-<timestamp>.sql.gz
#   2) Mantém apenas os 7 backups mais recentes
#   3) Sanity-check de existência da migration aditiva
#
# NÃO aplica migration aqui. A migration `migrations/001_auth_and_beds.sql`
# é aditiva + idempotente e é aplicada no startup do parser-api (init_db()).
# Forçar aqui dobraria a chance de erro durante swap.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=_lib.sh
. "${SCRIPT_DIR}/_lib.sh"

banner "PRE-DEPLOY (backup + sanity)"

load_deploy_env

# 1) pg_dump --------------------------------------------------------------
BACKUP_FILE="${GIRO_BACKUPS}/db-${TS}.sql.gz"

if ! docker ps --format '{{.Names}}' | grep -q "^${DB_CONTAINER}$"; then
    die "container ${DB_CONTAINER} não está rodando — abortando (db indisponível p/ backup)"
fi

log INFO "executando pg_dump → ${BACKUP_FILE}"
# Lê POSTGRES_USER/POSTGRES_DB do próprio container (fonte de verdade)
PG_USER="$(docker inspect --format='{{range .Config.Env}}{{println .}}{{end}}' "$DB_CONTAINER" | awk -F= '/^POSTGRES_USER=/{print $2}')"
PG_DB="$(docker inspect --format='{{range .Config.Env}}{{println .}}{{end}}' "$DB_CONTAINER" | awk -F= '/^POSTGRES_DB=/{print $2}')"
: "${PG_USER:=giro}"
: "${PG_DB:=giro_de_leitos}"

if ! docker exec "$DB_CONTAINER" pg_dump -U "$PG_USER" -d "$PG_DB" | gzip > "$BACKUP_FILE"; then
    rm -f "$BACKUP_FILE"
    die "pg_dump falhou — abortando deploy"
fi

if [ ! -s "$BACKUP_FILE" ]; then
    rm -f "$BACKUP_FILE"
    die "backup gerado está vazio — abortando deploy"
fi

log OK "backup ok ($(du -h "$BACKUP_FILE" | cut -f1)) → ${BACKUP_FILE}"

# 2) Rotação: mantém últimos 7 -------------------------------------------
# `ls -1t` lista mais novo primeiro; tail +8 pega do 8º em diante.
mapfile -t old < <(ls -1t "${GIRO_BACKUPS}"/db-*.sql.gz 2>/dev/null | tail -n +8 || true)
for f in "${old[@]}"; do
    log INFO "removendo backup antigo: $f"
    rm -f -- "$f"
done

# 3) Sanity-check migration ----------------------------------------------
MIG="${GIRO_REPO}/migrations/001_auth_and_beds.sql"
if [ ! -r "$MIG" ]; then
    die "migration $MIG não existe ou não é legível"
fi
log OK "migration $MIG presente (será aplicada via init_db() no startup do parser-api)"

log OK "pre-deploy concluído"
