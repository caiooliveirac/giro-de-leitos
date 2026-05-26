'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import { AnimatePresence, LayoutGroup, motion } from 'framer-motion';
import { ArrowRightLeft, Eraser, HeartPulse, LogOut, Plus, Skull } from 'lucide-react';
import type { Bed } from '@/lib/api';
import { formatRelative } from '@/lib/time';
import { PinPad } from '@/components/auth/PinPad';

interface RedRoomBedProps {
  bed: Bed | null;
  bedNumber: number;
  onSave: (data: { patient_sigla: string; clinical_summary: string }) => void | Promise<void>;
  onDischarge: () => void | Promise<void>;
  onDeath: (pin: string) => void | Promise<void>;
  onTransfer: () => void | Promise<void>;
  onClear: () => void | Promise<void>;
}

const QUICK_CHIPS = [
  'Dor torácica',
  'IAM',
  'AVC',
  'Trauma',
  'Sepse',
  'Crise convulsiva',
];

export function RedRoomBed({
  bed,
  bedNumber,
  onSave,
  onDischarge,
  onDeath,
  onTransfer,
  onClear,
}: RedRoomBedProps) {
  const isOccupied = Boolean(bed && bed.patient_sigla);
  const [expanded, setExpanded] = useState(false);
  const [sigla, setSigla] = useState(bed?.patient_sigla ?? '');
  const [summary, setSummary] = useState(bed?.clinical_summary ?? '');
  const [pinOpen, setPinOpen] = useState(false);
  const [pinError, setPinError] = useState<string | null>(null);
  const longPressTimer = useRef<number | null>(null);
  const longPressFired = useRef(false);

  useEffect(() => {
    if (!expanded) {
      setSigla(bed?.patient_sigla ?? '');
      setSummary(bed?.clinical_summary ?? '');
      setPinOpen(false);
      setPinError(null);
    }
  }, [expanded, bed]);

  const toggle = useCallback(() => setExpanded((v) => !v), []);

  const collapse = useCallback(() => {
    setExpanded(false);
    setPinOpen(false);
  }, []);

  const handleAddChip = (chip: string) => {
    setSummary((cur) => {
      if (!cur) return chip;
      if (cur.toLowerCase().includes(chip.toLowerCase())) return cur;
      return `${cur} · ${chip}`;
    });
  };

  const handleSave = async () => {
    await onSave({
      patient_sigla: sigla.trim().toUpperCase(),
      clinical_summary: summary.trim(),
    });
    collapse();
  };

  const startLongPress = () => {
    longPressFired.current = false;
    longPressTimer.current = window.setTimeout(() => {
      longPressFired.current = true;
      setPinOpen(true);
    }, 500);
  };
  const cancelLongPress = () => {
    if (longPressTimer.current !== null) {
      window.clearTimeout(longPressTimer.current);
      longPressTimer.current = null;
    }
  };

  const handlePinSubmit = async (pin: string) => {
    setPinError(null);
    try {
      await onDeath(pin);
      collapse();
    } catch {
      setPinError('PIN incorreto');
    }
  };

  return (
    <LayoutGroup>
      <motion.div
        layout
        transition={{ type: 'spring', stiffness: 340, damping: 32 }}
        className={`rounded-card border p-5 shadow-card ${
          isOccupied
            ? 'border-border bg-card'
            : 'border-dashed border-border bg-card/40'
        }`}
      >
        <motion.button
          layout
          type="button"
          onClick={toggle}
          aria-expanded={expanded}
          aria-label={`Leito ${bedNumber} ${isOccupied ? 'ocupado' : 'vago'}`}
          whileTap={{ scale: 0.97 }}
          className="flex w-full items-center gap-4 text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-blue focus-visible:ring-offset-2 focus-visible:ring-offset-surface"
        >
          <div
            className={`flex h-12 w-12 shrink-0 items-center justify-center rounded-pill text-lg font-semibold tabular-nums ${
              isOccupied
                ? 'bg-accent-red/10 text-accent-red'
                : 'bg-surface text-text-tertiary'
            }`}
          >
            {bedNumber}
          </div>

          <div className="min-w-0 flex-1">
            {isOccupied ? (
              <>
                <div className="flex items-baseline gap-2">
                  <span className="text-[20px] font-semibold leading-tight text-text-primary">
                    {bed?.patient_sigla}
                  </span>
                  {bed?.occupied_since && (
                    <span className="text-xs tabular-nums text-text-tertiary">
                      {formatRelative(bed.occupied_since)}
                    </span>
                  )}
                </div>
                {bed?.clinical_summary && (
                  <p className="mt-0.5 truncate text-sm text-text-secondary">
                    {bed.clinical_summary}
                  </p>
                )}
              </>
            ) : (
              <>
                <p className="text-base font-medium text-text-primary">Leito {bedNumber}</p>
                <p className="mt-0.5 text-sm text-text-tertiary">Tocar pra internar</p>
              </>
            )}
          </div>

          {isOccupied ? (
            <HeartPulse size={18} className="text-accent-red" aria-hidden />
          ) : (
            <Plus size={18} className="text-text-tertiary" aria-hidden />
          )}
        </motion.button>

        <AnimatePresence initial={false}>
          {expanded && (
            <motion.div
              key="body"
              layout
              initial={{ opacity: 0, height: 0 }}
              animate={{ opacity: 1, height: 'auto' }}
              exit={{ opacity: 0, height: 0 }}
              transition={{ type: 'spring', stiffness: 280, damping: 32 }}
              className="overflow-hidden"
            >
              <div className="mt-4 space-y-3">
                <div>
                  <label className="text-xs font-medium uppercase tracking-wide text-text-tertiary">
                    Sigla
                  </label>
                  <input
                    type="text"
                    value={sigla}
                    onChange={(e) => setSigla(e.target.value)}
                    maxLength={6}
                    placeholder="Ex.: JCO"
                    aria-label="Sigla do paciente"
                    className="mt-1 w-full rounded-pill border border-border bg-surface px-4 py-2.5 text-base font-medium uppercase tracking-wide text-text-primary placeholder:text-text-tertiary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-blue focus-visible:ring-offset-2 focus-visible:ring-offset-card"
                  />
                </div>

                <div>
                  <label className="text-xs font-medium uppercase tracking-wide text-text-tertiary">
                    Resumo clínico
                  </label>
                  <textarea
                    value={summary}
                    onChange={(e) => setSummary(e.target.value)}
                    rows={2}
                    placeholder="Descreva queixa principal, hipóteses..."
                    aria-label="Resumo clínico"
                    className="mt-1 w-full resize-none rounded-2xl border border-border bg-surface px-4 py-2.5 text-sm text-text-primary placeholder:text-text-tertiary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-blue focus-visible:ring-offset-2 focus-visible:ring-offset-card"
                  />
                </div>

                <div className="flex flex-wrap gap-1.5">
                  {QUICK_CHIPS.map((chip) => (
                    <motion.button
                      key={chip}
                      type="button"
                      whileTap={{ scale: 0.94 }}
                      onClick={() => handleAddChip(chip)}
                      className="rounded-pill bg-surface px-3 py-1 text-xs font-medium text-text-secondary transition hover:text-text-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-blue focus-visible:ring-offset-2 focus-visible:ring-offset-card"
                    >
                      {chip}
                    </motion.button>
                  ))}
                </div>

                <div className="flex gap-2 pt-1">
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
                    onClick={collapse}
                    aria-label="Cancelar edição"
                    className="rounded-pill border border-border bg-surface px-4 py-2.5 text-sm font-medium text-text-secondary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-blue focus-visible:ring-offset-2 focus-visible:ring-offset-card"
                  >
                    Cancelar
                  </motion.button>
                </div>

                {isOccupied && (
                  <div className="grid grid-cols-2 gap-2 border-t border-border/60 pt-3">
                    <motion.button
                      type="button"
                      whileTap={{ scale: 0.96 }}
                      onClick={() => void onDischarge()}
                      className="flex items-center justify-center gap-1.5 rounded-pill bg-accent-green px-3 py-2.5 text-sm font-semibold text-white focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-blue focus-visible:ring-offset-2 focus-visible:ring-offset-card"
                    >
                      <LogOut size={15} /> Alta
                    </motion.button>

                    <motion.button
                      type="button"
                      whileTap={{ scale: 0.96 }}
                      onPointerDown={startLongPress}
                      onPointerUp={cancelLongPress}
                      onPointerLeave={cancelLongPress}
                      onPointerCancel={cancelLongPress}
                      onClick={(e) => {
                        if (!longPressFired.current) {
                          e.preventDefault();
                        }
                      }}
                      aria-label="Óbito — pressione e segure"
                      className="flex items-center justify-center gap-1.5 rounded-pill bg-accent-red px-3 py-2.5 text-sm font-semibold text-white focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-blue focus-visible:ring-offset-2 focus-visible:ring-offset-card"
                    >
                      <Skull size={15} /> Óbito
                    </motion.button>

                    <motion.button
                      type="button"
                      whileTap={{ scale: 0.96 }}
                      onClick={() => void onTransfer()}
                      className="flex items-center justify-center gap-1.5 rounded-pill bg-accent-amber px-3 py-2.5 text-sm font-semibold text-black focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-blue focus-visible:ring-offset-2 focus-visible:ring-offset-card"
                    >
                      <ArrowRightLeft size={15} /> Transferir
                    </motion.button>

                    <motion.button
                      type="button"
                      whileTap={{ scale: 0.96 }}
                      onClick={() => void onClear()}
                      className="flex items-center justify-center gap-1.5 rounded-pill border border-border bg-surface px-3 py-2.5 text-sm font-medium text-text-secondary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-blue focus-visible:ring-offset-2 focus-visible:ring-offset-card"
                    >
                      <Eraser size={15} /> Esvaziar
                    </motion.button>
                  </div>
                )}

                {pinOpen && (
                  <PinPad
                    length={4}
                    onSubmit={handlePinSubmit}
                    onCancel={() => setPinOpen(false)}
                    error={pinError}
                    title="Confirme o óbito"
                    description="Digite seu PIN de 4 dígitos"
                  />
                )}
              </div>
            </motion.div>
          )}
        </AnimatePresence>
      </motion.div>
    </LayoutGroup>
  );
}
