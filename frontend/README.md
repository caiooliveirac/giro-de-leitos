# Giro de Leitos — Frontend

Aplicação Next.js 14 (App Router) que consome a API FastAPI do backend.

## Stack

- Next.js 14 + React 18 + TypeScript 5
- Tailwind CSS 3 com tokens semânticos (CSS vars) e dark mode (`class`)
- TanStack Query, Zustand, React Hook Form + Zod
- Radix UI (Toast, Toggle, Tabs), Framer Motion, Lucide Icons
- PWA via `next-pwa`

## Instalação

```bash
cd frontend
pnpm install
```

## Desenvolvimento

```bash
pnpm dev
```

A app sobe em `http://localhost:3000`. Em desenvolvimento, requisições para `/api/*` e `/ws/*` são proxyadas para o backend em `http://localhost:8000` (configurado em `next.config.js`). Garanta que o backend FastAPI esteja rodando antes de testar fluxos autenticados.

## Scripts

| Script | Descrição |
|--------|-----------|
| `pnpm dev` | Servidor de desenvolvimento (porta 3000) |
| `pnpm build` | Build de produção |
| `pnpm start` | Servidor de produção |
| `pnpm lint` | ESLint (config Next.js) |
| `pnpm typecheck` | `tsc --noEmit` |

## Estrutura

```
app/                 Rotas (App Router) com grupos: (public), (auth), (app)
components/          UI reutilizável (beds/, auth/, shared/)
hooks/               Hooks customizados
lib/                 api, ws, theme, device, utilitários
styles/              tokens.css (CSS vars light/dark)
public/              manifest, ícones, assets estáticos
```

## Tema

Tokens em `styles/tokens.css` inspirados no Apple Health (light/dark). Toggle via `useTheme()` em `lib/theme.ts`, persistido em `localStorage` (`gl_theme`). O `app/layout.tsx` injeta script inline pré-hidratação para evitar FOUC.
