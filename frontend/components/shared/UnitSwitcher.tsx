'use client';

import { Building2 } from 'lucide-react';
import type { MyUnit } from '@/hooks/useMyUnits';

interface UnitSwitcherProps {
  units: MyUnit[];
  value: string | null;
  onChange: (id: string) => void;
}

/**
 * Seletor leve de UPA para coordenador multi-UPA. Renderiza nada quando há
 * uma única UPA (ou nenhuma) — só aparece pra quem coordena mais de uma.
 */
export function UnitSwitcher({ units, value, onChange }: UnitSwitcherProps) {
  if (units.length <= 1) return null;

  return (
    <section className="mt-2" aria-label="UPA em exibição">
      <div className="mb-1.5 flex items-center gap-1.5 px-1 text-ink-3">
        <Building2 size={12} aria-hidden />
        <h2 className="text-[11px] font-semibold uppercase tracking-[0.14em]">UPA</h2>
      </div>
      <div className="flex flex-wrap gap-1.5">
        {units.map((u) => {
          const on = u.id === value;
          return (
            <button
              key={u.id}
              type="button"
              onClick={() => onChange(u.id)}
              aria-pressed={on}
              className={`rounded-pill border px-3 py-1.5 text-xs font-medium transition focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-blue/30 ${
                on
                  ? 'border-accent-blue bg-accent-blue/10 text-accent-blue'
                  : 'border-border bg-surface text-text-secondary hover:bg-border/30'
              }`}
            >
              {u.name}
              {u.is_primary && (
                <span className="ml-1 text-[10px] uppercase tracking-wide text-text-tertiary">
                  · principal
                </span>
              )}
            </button>
          );
        })}
      </div>
    </section>
  );
}
