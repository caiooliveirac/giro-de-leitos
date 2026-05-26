'use client';

import { useState } from 'react';
import { AnimatePresence, motion } from 'framer-motion';
import { FlaskConical } from 'lucide-react';
import type { LucideIcon } from 'lucide-react';
import type { ExamStatus } from '@/lib/api';

interface ExamCardProps {
  sectorKey: string;
  label: string;
  status: ExamStatus;
  unavailable_reason?: string | null;
  icon?: LucideIcon;
  onChange: (next: { status: ExamStatus; unavailable_reason: string | null }) => void | Promise<void>;
}

// Razões fiéis ao design/src/data.jsx REASON_OPTIONS.
const REASON_OPTIONS = [
  'sem técnico',
  'equipamento em manutenção',
  'sem insumo',
  'aguardando contraste',
];

export function ExamCard({
  label,
  status,
  unavailable_reason,
  icon: Icon = FlaskConical,
  onChange,
}: ExamCardProps) {
  const [expanded, setExpanded] = useState(false);
  const isWorking = status === 'working';

  const flipToggle = (e: React.MouseEvent) => {
    e.stopPropagation();
    if (isWorking) {
      void onChange({ status: 'unavailable', unavailable_reason: unavailable_reason ?? null });
    } else {
      void onChange({ status: 'working', unavailable_reason: null });
    }
  };

  const pickReason = (reason: string) => {
    const next = unavailable_reason === reason ? null : reason;
    void onChange({ status: 'unavailable', unavailable_reason: next });
  };

  const shellBorder = isWorking
    ? 'border-line'
    : 'border-[color-mix(in_oklch,var(--critical)_35%,var(--line))]';

  return (
    <motion.div
      layout
      transition={{ type: 'spring', stiffness: 340, damping: 32 }}
      className={`overflow-hidden rounded-card border bg-surface-elev shadow-card ${shellBorder} ${
        expanded ? 'shadow-pop' : ''
      }`}
    >
      <motion.button
        layout
        type="button"
        whileTap={{ scale: 0.97 }}
        onClick={() => setExpanded((v) => !v)}
        aria-expanded={expanded}
        aria-label={`${label}: ${isWorking ? 'funcionando' : 'indisponível'}`}
        className="flex w-full items-center gap-3.5 px-5 py-4 text-left focus-visible:outline-none"
      >
        <div
          className={`flex h-[42px] w-[42px] shrink-0 items-center justify-center rounded-[14px] ${
            isWorking
              ? 'bg-success-soft text-success-ink'
              : 'bg-critical-soft text-critical-ink'
          }`}
        >
          <Icon size={20} />
        </div>
        <div className="min-w-0 flex-1">
          <p className="truncate text-[16px] font-semibold text-ink">{label}</p>
          <p
            className={`mt-0.5 truncate text-[13px] ${
              isWorking ? 'text-success-ink' : 'text-critical-ink'
            }`}
          >
            {isWorking
              ? 'Funcionando'
              : unavailable_reason
                ? `Indisponível · ${unavailable_reason}`
                : 'Indisponível'}
          </p>
        </div>

        {/* toggle switch fiel ao design */}
        <button
          type="button"
          role="switch"
          aria-checked={isWorking}
          aria-label={`Alternar ${label}`}
          onClick={flipToggle}
          className={`relative h-8 w-14 shrink-0 rounded-full border transition-colors ${
            isWorking
              ? 'border-success bg-success'
              : 'border-line bg-surface-2'
          }`}
        >
          <span
            aria-hidden
            className="absolute left-[3px] top-[3px] h-6 w-6 rounded-full bg-white shadow-[0_1px_3px_rgba(0,0,0,0.18)] transition-transform"
            style={{
              transform: isWorking ? 'translateX(24px)' : 'translateX(0)',
              transitionTimingFunction: 'cubic-bezier(0.34, 1.2, 0.4, 1)',
              transitionDuration: '220ms',
            }}
          />
        </button>
      </motion.button>

      <AnimatePresence initial={false}>
        {expanded && (
          <motion.div
            layout
            initial={{ opacity: 0, height: 0 }}
            animate={{ opacity: 1, height: 'auto' }}
            exit={{ opacity: 0, height: 0 }}
            transition={{ type: 'spring', stiffness: 280, damping: 32 }}
            className="overflow-hidden"
          >
            <div className="px-4 pb-4 pt-1">
              <p className="mb-2 text-[11px] font-semibold uppercase tracking-[0.12em] text-ink-3">
                {!isWorking ? 'Motivo (opcional)' : 'Disponível — sem ação necessária'}
              </p>
              {!isWorking && (
                <div className="flex flex-wrap gap-1.5">
                  {REASON_OPTIONS.map((r) => {
                    const on = unavailable_reason === r;
                    return (
                      <motion.button
                        key={r}
                        type="button"
                        whileTap={{ scale: 0.94 }}
                        onClick={() => pickReason(r)}
                        className={`min-h-[36px] rounded-full border px-3 py-2 text-[13px] font-medium transition-colors ${
                          on
                            ? 'border-[color-mix(in_oklch,var(--critical)_40%,var(--line))] bg-critical-soft text-critical-ink'
                            : 'border-line bg-surface text-ink-2'
                        }`}
                      >
                        {r}
                      </motion.button>
                    );
                  })}
                </div>
              )}
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </motion.div>
  );
}
