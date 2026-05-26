'use client';

import { Moon, Sun, Activity } from 'lucide-react';
import { useTheme } from '@/lib/theme';

export default function HomePage() {
  const { theme, toggle, mounted } = useTheme();

  return (
    <main className="min-h-screen px-6 py-10 sm:px-10">
      <header className="mx-auto flex max-w-3xl items-center justify-between">
        <div>
          <p className="text-xs uppercase tracking-widest text-text-tertiary">Fase 4</p>
          <h1 className="text-3xl font-semibold tracking-tight">Giro de Leitos</h1>
        </div>
        <button
          type="button"
          onClick={toggle}
          aria-label="Alternar tema"
          className="inline-flex h-11 w-11 items-center justify-center rounded-pill border border-border bg-card text-text-secondary transition hover:text-text-primary"
        >
          {mounted && theme === 'dark' ? <Sun size={18} /> : <Moon size={18} />}
        </button>
      </header>

      <section className="mx-auto mt-10 max-w-3xl">
        <div className="card-ios">
          <div className="flex items-start gap-4">
            <div className="flex h-12 w-12 items-center justify-center rounded-pill bg-accent-green/15 text-accent-green">
              <Activity size={22} />
            </div>
            <div className="flex-1">
              <h2 className="text-lg font-semibold">Frontend inicializado</h2>
              <p className="mt-1 text-sm text-text-secondary">
                Next.js 14 (App Router), Tailwind 3 com tokens semanticos e dark mode persistente.
                As Fases 5 e 6 vao trazer os componentes de leitos, autenticacao e telas reais.
              </p>
              <div className="mt-4 flex flex-wrap gap-2 text-xs">
                <span className="rounded-pill bg-accent-blue/10 px-3 py-1 text-accent-blue">App Router</span>
                <span className="rounded-pill bg-accent-amber/10 px-3 py-1 text-accent-amber">Tailwind tokens</span>
                <span className="rounded-pill bg-accent-green/10 px-3 py-1 text-accent-green">PWA ready</span>
              </div>
            </div>
          </div>
        </div>
      </section>
    </main>
  );
}
