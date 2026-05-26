# syntax=docker/dockerfile:1.6

# ---------- Stage 1: builder ----------
FROM node:20-alpine AS builder
WORKDIR /app

RUN apk add --no-cache libc6-compat \
    && corepack enable \
    && corepack prepare pnpm@9.12.0 --activate

COPY package.json ./
COPY pnpm-lock.yaml* ./

RUN pnpm install --frozen-lockfile || pnpm install

COPY . .

ENV NEXT_TELEMETRY_DISABLED=1
RUN pnpm build

# ---------- Stage 2: runner ----------
FROM node:20-alpine AS runner
WORKDIR /app

ENV NODE_ENV=production
ENV NEXT_TELEMETRY_DISABLED=1
ENV PORT=3000

RUN corepack enable \
    && corepack prepare pnpm@9.12.0 --activate \
    && addgroup --system --gid 1001 nodejs \
    && adduser --system --uid 1001 nextjs

COPY --from=builder /app/package.json ./package.json
COPY --from=builder /app/pnpm-lock.yaml* ./
COPY --from=builder /app/next.config.js ./next.config.js
COPY --from=builder /app/public ./public
COPY --from=builder /app/.next ./.next
COPY --from=builder /app/node_modules ./node_modules

USER nextjs

EXPOSE 3000

CMD ["pnpm", "start"]
