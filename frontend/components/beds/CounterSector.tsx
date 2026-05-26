'use client';

import { useEffect, useState } from 'react';
import { AnimatePresence, motion } from 'framer-motion';
import { Minus, Plus } from 'lucide-react';
import type { LucideIcon } from 'lucide-react';

interface CounterSectorProps {
  sector: {
    key: string;
    label: string;
    occupancy: number;
    capacity: number;
    version: number;
    icon?: LucideIcon;
  };
  onSave: (next: { occupancy: number; capacity: number }) => void | Promise<void>;
}

function colorClass(occ: number, cap: number): string {
  if (occ > cap) return 'text-accent-red';
  if (occ === cap) return 'text-accent-amber';
  return 'text-accent-green';
}

interface StepperProps {
  label: string;
  value: number;
  onChange: (next: number) => void;
  min?: number;
  max?: number;
  ariaLabel: string;
}

function Stepper({ label, value, onChange, min = 0, max = 99, ariaLabel }: StepperProps) {
  return (
    <div className="rounded-2xl border border-border bg-surface p-3">
      <p className="text-xs font-medium uppercase tracking-wide text-text-tertiary">{label}</p>
      <div className="mt-2 flex items-center justify-between gap-2">
        <motion.button
          type="button"
          whileTap={{ scale: 0.92 }}
          onClick={() => onChange(Math.max(min, value - 1))}
          aria-label={`Diminuir ${ariaLabel}`}
          className="flex h-11 w-11 items-center justify-center rounded-pill bg-card text-text-primary shadow-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-blue focus-visible:ring-offset-2 focus-visible:ring-offset-surface"
        >
          <Minus size={16} />
        </motion.button>
        <span className="text-2xl font-semibold tabular-nums text-text-primary">{value}</span>
        <motion.button
          type="button"
          whileTap={{ scale: 0.92 }}
          onClick={() => onChange(Math.min(max, value + 1))}
          aria-label={`Aumentar ${ariaLabel}`}
          className="flex h-11 w-11 items-center justify-center rounded-pill bg-card text-text-primary shadow-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-blue focus-visible:ring-offset-2 focus-visible:ring-offset-surface"
        >
          <Plus size={16} />
        </motion.button>
      </div>
    </div>
  );
}

export function CounterSector({ sector, onSave }: CounterSectorProps) {
  const Icon = sector.icon;
  const [expanded, setExpanded] = useState(false);
  const [occ, setOcc] = useState(sector.occupancy);
  const [cap, setCap] = useState(sector.capacity);

  useEffect(() => {
    if (!expanded) {
      setOcc(sector.occupancy);
      setCap(sector.capacity);
    }
  }, [expanded, sector.occupancy, sector.capacity]);

  const handleSave = async () => {
    await onSave({ occupancy: occ, capacity: cap });
    setExpanded(false);
  };

  return (
    <motion.div
      layout
      transition={{ type: 'spring', stiffness: 340, damping: 32 }}
      className="rounded-card border border-border bg-card p-5 shadow-card"
    >
      <motion.button
        layout
        type="button"
        onClick={() => setExpanded((v) => !v)}
        whileTap={{ scale: 0.97 }}
        aria-expanded={expanded}
        aria-label={`${sector.label}: ${sector.occupancy} de ${sector.capacity}`}
        className="flex w-full items-center gap-3 text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-blue focus-visible:ring-offset-2 focus-visible:ring-offset-surface"
      >
        {Icon && (
          <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-pill bg-surface text-text-secondary">
            <Icon size={16} />
          </div>
        )}
        <div className="min-w-0 flex-1">
          <p className="truncate text-sm font-medium text-text-primary">{sector.label}</p>
          <p className="text-xs text-text-tertiary">ocupação / capacidade</p>
        </div>
        <div
          className={`text-[32px] font-semibold leading-none tabular-nums ${colorClass(
            sector.occupancy,
            sector.capacity,
          )}`}
        >
          {sector.occupancy}
          <span className="text-lg text-text-tertiary">/{sector.capacity}</span>
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
            <div className="mt-4 grid grid-cols-2 gap-2">
              <Stepper
                label="Ocupação"
                value={occ}
                onChange={setOcc}
                ariaLabel="ocupação"
                max={Math.max(99, cap + 5)}
              />
              <Stepper
                label="Capacidade"
                value={cap}
                onChange={setCap}
                min={0}
                ariaLabel="capacidade"
              />
            </div>
            <div className="mt-3 flex gap-2">
              <motion.button
                type="button"
                whileTap={{ scale: 0.96 }}
                onClick={handleSave}
                className="flex-1 rounded-pill bg-accent-blue px-4 py-2.5 text-sm font-semibold text-white focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-blue focus-visible:ring-offset-2 focus-visible:ring-offset-card"
              >
                Salvar
              </motion.button>
              <motion.button
                type="button"
                whileTap={{ scale: 0.96 }}
                onClick={() => setExpanded(false)}
                className="rounded-pill border border-border bg-surface px-4 py-2.5 text-sm font-medium text-text-secondary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-blue focus-visible:ring-offset-2 focus-visible:ring-offset-card"
              >
                Cancelar
              </motion.button>
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </motion.div>
  );
}
