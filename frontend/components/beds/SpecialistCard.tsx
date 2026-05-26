'use client';

import { useState } from 'react';
import { AnimatePresence, motion } from 'framer-motion';
import { Stethoscope } from 'lucide-react';
import type { LucideIcon } from 'lucide-react';
import type { SpecialistStatus } from '@/lib/api';

interface SpecialistCardProps {
  sectorKey: string;
  label: string;
  status: SpecialistStatus;
  icon?: LucideIcon;
  onChange: (next: SpecialistStatus) => void | Promise<void>;
}

const STATES: Array<{ id: SpecialistStatus; label: string }> = [
  { id: 'available', label: 'Disponível' },
  { id: 'on_call', label: 'Sob aviso' },
  { id: 'unavailable', label: 'Indisponível' },
];

function statusMeta(status: SpecialistStatus) {
  switch (status) {
    case 'available':
      return { dot: 'bg-success', text: 'text-success-ink' };
    case 'on_call':
      return { dot: 'bg-warning', text: 'text-warning-ink' };
    case 'unavailable':
      return { dot: 'bg-neutral', text: 'text-ink-3' };
  }
}

export function SpecialistCard({
  label,
  status,
  icon: Icon = Stethoscope,
  onChange,
}: SpecialistCardProps) {
  const [expanded, setExpanded] = useState(false);
  const meta = statusMeta(status);
  const current = STATES.find((s) => s.id === status);

  return (
    <motion.div
      layout
      transition={{ type: 'spring', stiffness: 340, damping: 32 }}
      className={`overflow-hidden rounded-card border border-line bg-surface-elev shadow-card ${
        expanded ? 'shadow-pop' : ''
      }`}
    >
      <motion.button
        layout
        type="button"
        whileTap={{ scale: 0.97 }}
        onClick={() => setExpanded((v) => !v)}
        aria-expanded={expanded}
        aria-label={`${label}: ${current?.label ?? status}`}
        className="flex w-full items-center gap-3.5 px-5 py-4 text-left focus-visible:outline-none"
      >
        <div className="flex h-[42px] w-[42px] shrink-0 items-center justify-center rounded-full bg-surface-2 text-ink-2">
          <Icon size={20} />
        </div>
        <div className="min-w-0 flex-1">
          <p className="truncate text-[16px] font-semibold text-ink">{label}</p>
          <p className={`mt-0.5 inline-flex items-center gap-1.5 text-[13px] font-medium ${meta.text}`}>
            <span className={`inline-block h-2.5 w-2.5 rounded-full ${meta.dot}`} />
            {current?.label}
          </p>
        </div>
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
            <div className="px-4 pb-4">
              <div className="grid grid-cols-3 gap-1.5 rounded-[14px] border border-line bg-surface p-1">
                {STATES.map((s) => {
                  const on = s.id === status;
                  return (
                    <motion.button
                      key={s.id}
                      type="button"
                      whileTap={{ scale: 0.96 }}
                      onClick={() => {
                        void onChange(s.id);
                        setExpanded(false);
                      }}
                      className={`min-h-[44px] rounded-[11px] px-2 py-3 text-sm font-medium transition-all ${
                        on
                          ? 'bg-surface-elev text-ink shadow-[0_1px_3px_rgba(0,0,0,0.08),0_0_0_1px_var(--line-strong)]'
                          : 'text-ink-2'
                      }`}
                    >
                      {s.label}
                    </motion.button>
                  );
                })}
              </div>
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </motion.div>
  );
}
