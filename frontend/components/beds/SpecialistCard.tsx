'use client';

import { useCallback, useRef, useState } from 'react';
import { AnimatePresence, motion } from 'framer-motion';
import type { LucideIcon } from 'lucide-react';
import type { SpecialistStatus } from '@/lib/api';

interface SpecialistCardProps {
  sectorKey: string;
  label: string;
  status: SpecialistStatus;
  icon?: LucideIcon;
  onChange: (next: SpecialistStatus) => void | Promise<void>;
}

const STATUS_META: Record<
  SpecialistStatus,
  { label: string; pill: string; dot: string }
> = {
  available: {
    label: 'Disponível',
    pill: 'bg-accent-green/15 text-accent-green',
    dot: 'bg-accent-green',
  },
  on_call: {
    label: 'Sobreaviso',
    pill: 'bg-accent-amber/15 text-accent-amber',
    dot: 'bg-accent-amber',
  },
  unavailable: {
    label: 'Indisponível',
    pill: 'bg-text-tertiary/15 text-text-secondary',
    dot: 'bg-text-tertiary',
  },
};

const CYCLE: SpecialistStatus[] = ['available', 'on_call', 'unavailable'];

export function SpecialistCard({
  label,
  status,
  icon: Icon,
  onChange,
}: SpecialistCardProps) {
  const [picker, setPicker] = useState(false);
  const longPressTimer = useRef<number | null>(null);
  const longPressFired = useRef(false);

  const meta = STATUS_META[status];

  const handleTap = useCallback(() => {
    if (longPressFired.current) {
      longPressFired.current = false;
      return;
    }
    const next = CYCLE[(CYCLE.indexOf(status) + 1) % CYCLE.length];
    void onChange(next);
  }, [status, onChange]);

  const startLongPress = () => {
    longPressFired.current = false;
    longPressTimer.current = window.setTimeout(() => {
      longPressFired.current = true;
      setPicker(true);
    }, 450);
  };
  const cancelLongPress = () => {
    if (longPressTimer.current !== null) {
      window.clearTimeout(longPressTimer.current);
      longPressTimer.current = null;
    }
  };

  return (
    <motion.div
      layout
      transition={{ type: 'spring', stiffness: 340, damping: 32 }}
      className="rounded-card border border-border bg-card p-4 shadow-card"
    >
      <motion.button
        type="button"
        whileTap={{ scale: 0.96 }}
        onClick={handleTap}
        onPointerDown={startLongPress}
        onPointerUp={cancelLongPress}
        onPointerLeave={cancelLongPress}
        onPointerCancel={cancelLongPress}
        aria-label={`${label}: ${meta.label}`}
        className="flex w-full items-center gap-3 text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-blue focus-visible:ring-offset-2 focus-visible:ring-offset-surface"
      >
        {Icon && (
          <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-pill bg-surface text-text-secondary">
            <Icon size={16} />
          </div>
        )}
        <div className="min-w-0 flex-1">
          <p className="truncate text-sm font-medium text-text-primary">{label}</p>
        </div>
        <span
          className={`inline-flex items-center gap-1.5 rounded-pill px-2.5 py-1 text-[11px] font-semibold ${meta.pill}`}
        >
          <span className={`h-1.5 w-1.5 rounded-full ${meta.dot}`} />
          {meta.label}
        </span>
      </motion.button>

      <AnimatePresence initial={false}>
        {picker && (
          <motion.div
            layout
            initial={{ opacity: 0, height: 0 }}
            animate={{ opacity: 1, height: 'auto' }}
            exit={{ opacity: 0, height: 0 }}
            className="overflow-hidden"
          >
            <div className="mt-3 flex flex-wrap gap-1.5">
              {CYCLE.map((s) => {
                const m = STATUS_META[s];
                const active = s === status;
                return (
                  <motion.button
                    key={s}
                    type="button"
                    whileTap={{ scale: 0.94 }}
                    onClick={() => {
                      void onChange(s);
                      setPicker(false);
                    }}
                    className={`rounded-pill px-3 py-1.5 text-xs font-medium ${
                      active ? m.pill : 'bg-surface text-text-secondary'
                    } focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-blue focus-visible:ring-offset-2 focus-visible:ring-offset-card`}
                  >
                    {m.label}
                  </motion.button>
                );
              })}
              <button
                type="button"
                onClick={() => setPicker(false)}
                className="ml-auto text-xs text-text-tertiary underline-offset-2 hover:underline"
              >
                Fechar
              </button>
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </motion.div>
  );
}
