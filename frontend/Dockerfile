# syntax=docker/dockerfile:1.6
#
# Frontend Next.js (App Router) — build em modo `standalone`.
# Produz uma imagem mínima rodando `node server.js` como usuário não-root.
#
# Stages:
#   deps    -> instala dependências via pnpm com cache do lockfile
#   builder -> compila a aplicação (next build)
#   runner  -> imagem final, apenas com runtime + standalone output
#
# Requisitos no projeto:
#   - frontend/next.config.js precisa ter `output: 'standalone'` (configurado).
#   - pnpm-lock.yaml deve existir (gere com `pnpm install --lockfile-only`).

ARG NODE_VERSION=20-alpine

# ---------- Stage 1: deps ----------
FROM node:${NODE_VERSION} AS deps
WORKDIR /app

RUN apk add --no-cache libc6-compat \
    && corepack enable \
    && corepack prepare pnpm@9.12.0 --activate

# Copia somente arquivos de manifest para maximizar cache
COPY package.json pnpm-lock.yaml* ./

# `--frozen-lockfile` exige lockfile presente. Fallback evita travar build inicial,
# mas em CI/Prod o lockfile DEVE estar commitado.
RUN pnpm install --frozen-lockfile || pnpm install

# ---------- Stage 2: builder ----------
FROM node:${NODE_VERSION} AS builder
WORKDIR /app

RUN corepack enable && corepack prepare pnpm@9.12.0 --activate

COPY --from=deps /app/node_modules ./node_modules
COPY . .

ENV NEXT_TELEMETRY_DISABLED=1 \
    NODE_ENV=production

RUN pnpm build

# ---------- Stage 3: runner ----------
FROM node:${NODE_VERSION} AS runner
WORKDIR /app

ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    PORT=3000 \
    HOSTNAME=0.0.0.0

RUN addgroup --system --gid 1001 nodejs \
    && adduser  --system --uid 1001 nextjs

# Standalone server + assets estáticos + public
COPY --from=builder --chown=nextjs:nodejs /app/.next/standalone ./
COPY --from=builder --chown=nextjs:nodejs /app/.next/static    ./.next/static
COPY --from=builder --chown=nextjs:nodejs /app/public          ./public

USER nextjs

EXPOSE 3000

CMD ["node", "server.js"]
