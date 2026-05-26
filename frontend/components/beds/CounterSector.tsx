'use client';

import { useEffect, useState } from 'react';
import { AnimatePresence, motion } from 'framer-motion';
import { AlertTriangle, Minus, Plus } from 'lucide-react';
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

type State = 'ok' | 'full' | 'over';

function deriveState(occ: number, cap: number): State {
  if (occ > cap) return 'over';
  if (occ === cap) return 'full';
  return 'ok';
}

function stateText(occ: number, cap: number) {
  const s = deriveState(occ, cap);
  if (s === 'over') return `super-lotado · ${occ - cap} a mais`;
  if (s === 'full') return 'lotado';
  const free = cap - occ;
  return `${free} vaga${free === 1 ? '' : 's'}`;
}

interface StepperProps {
  label: string;
  value: number;
  onDec: () => void;
  onInc: () => void;
  min?: number;
  max?: number;
}

function Stepper({ label, value, onDec, onInc, min = 0, max = 99 }: StepperProps) {
  return (
    <div className="rounded-[18px] border border-line bg-surface p-3.5">
      <p className="mb-2 text-[11px] font-semibold uppercase tracking-[0.12em] text-ink-3">
        {label}
      </p>
      <div className="flex items-center justify-between gap-2">
        <motion.button
          type="button"
          whileTap={{ scale: 0.9 }}
          onClick={onDec}
          disabled={value <= min}
          aria-label={`${label} menos`}
          className="flex h-11 w-11 items-center justify-center rounded-[14px] bg-surface-2 text-ink disabled:opacity-35"
        >
          <Minus size={20} />
        </motion.button>
        <span className="min-w-[40px] text-center text-[28px] font-semibold tabular-nums tracking-tight text-ink">
          {value}
        </span>
        <motion.button
          type="button"
          whileTap={{ scale: 0.9 }}
          onClick={onInc}
          disabled={value >= max}
          aria-label={`${label} mais`}
          className="flex h-11 w-11 items-center justify-center rounded-[14px] bg-surface-2 text-ink disabled:opacity-35"
        >
          <Plus size={20} />
        </motion.button>
      </div>
    </div>
  );
}

export function CounterSector({ sector, onSave }: CounterSectorProps) {
  const [expanded, setExpanded] = useState(false);
  const [occ, setOcc] = useState(sector.occupancy);
  const [cap, setCap] = useState(sector.capacity);

  useEffect(() => {
    if (!expanded) {
      setOcc(sector.occupancy);
      setCap(sector.capacity);
    }
  }, [expanded, sector.occupancy, sector.capacity]);

  const state = deriveState(sector.occupancy, sector.capacity);

  // border + num coloring matching design
  const shellBorder =
    state === 'over'
      ? 'border-critical bg-critical-soft'
      : state === 'full'
        ? 'border-[color-mix(in_oklch,var(--warning)_35%,var(--line))]'
        : 'border-line';

  const numColor =
    state === 'over'
      ? 'text-critical'
      : state === 'full'
        ? 'text-warning-ink'
        : 'text-success-ink';

  const stateColor =
    state === 'over'
      ? 'text-critical-ink'
      : state === 'full'
        ? 'text-warning-ink'
        : 'text-success-ink';

  const commit = async () => {
    await onSave({ occupancy: occ, capacity: cap });
    setExpanded(false);
  };

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
        onClick={() => setExpanded((v) => !v)}
        whileTap={{ scale: 0.97 }}
        aria-expanded={expanded}
        aria-label={`${sector.label}: ${sector.occupancy} de ${sector.capacity}`}
        className="flex w-full items-center gap-3.5 px-5 py-4 text-left focus-visible:outline-none"
      >
        <div className={`text-[34px] font-semibold leading-none tabular-nums tracking-tight ${numColor}`}>
          {sector.occupancy}
          <span className="mx-1 font-normal text-ink-3">/</span>
          <span className="font-medium text-ink-2">{sector.capacity}</span>
        </div>
        <div className="min-w-0 flex-1">
          <p className="truncate text-[15px] font-semibold text-ink">{sector.label}</p>
          <p className={`mt-0.5 inline-flex items-center gap-1 text-xs font-medium ${stateColor}`}>
            {state === 'over' && <AlertTriangle size={12} aria-hidden />}
            {stateText(sector.occupancy, sector.capacity)}
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
            <div className="border-t border-line px-4 pb-4 pt-3">
              <div className="grid grid-cols-2 gap-3">
                <Stepper
                  label="Ocupação"
                  value={occ}
                  onDec={() => setOcc((v) => Math.max(0, v - 1))}
                  onInc={() => setOcc((v) => v + 1)}
                  max={Math.max(99, cap + 5)}
                />
                <Stepper
                  label="Capacidade"
                  value={cap}
                  onDec={() => setCap((v) => Math.max(0, v - 1))}
                  onInc={() => setCap((v) => v + 1)}
                />
              </div>
              <div className="mt-3 flex gap-2">
                <motion.button
                  type="button"
                  whileTap={{ scale: 0.96 }}
                  onClick={commit}
                  className="flex-1 rounded-2xl bg-ink px-4 py-3 text-sm font-semibold text-[var(--bg)]"
                >
                  Salvar
                </motion.button>
                <motion.button
                  type="button"
                  whileTap={{ scale: 0.96 }}
                  onClick={() => setExpanded(false)}
                  className="rounded-2xl border border-line bg-surface px-4 py-3 text-sm font-medium text-ink-2"
                >
                  Cancelar
                </motion.button>
              </div>
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </motion.div>
  );
}
