'use client';

import { useEffect, useState } from 'react';
import { AnimatePresence, motion } from 'framer-motion';
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

export function ExamCard({
  label,
  status,
  unavailable_reason,
  icon: Icon,
  onChange,
}: ExamCardProps) {
  const [editing, setEditing] = useState(false);
  const [reason, setReason] = useState(unavailable_reason ?? '');

  useEffect(() => {
    setReason(unavailable_reason ?? '');
  }, [unavailable_reason]);

  const isWorking = status === 'working';

  const handleTap = () => {
    if (isWorking) {
      // becoming unavailable — open reason editor
      setReason('');
      setEditing(true);
    } else {
      // back to working — clear reason
      void onChange({ status: 'working', unavailable_reason: null });
      setEditing(false);
    }
  };

  const handleSaveReason = () => {
    void onChange({ status: 'unavailable', unavailable_reason: reason.trim() || null });
    setEditing(false);
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
        aria-label={`${label}: ${isWorking ? 'funcionando' : 'indisponível'}`}
        className="flex w-full items-center gap-3 text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-blue focus-visible:ring-offset-2 focus-visible:ring-offset-surface"
      >
        {Icon && (
          <div
            className={`flex h-9 w-9 shrink-0 items-center justify-center rounded-pill ${
              isWorking ? 'bg-accent-green/15 text-accent-green' : 'bg-accent-red/10 text-accent-red'
            }`}
          >
            <Icon size={16} />
          </div>
        )}
        <div className="min-w-0 flex-1">
          <p className="truncate text-sm font-medium text-text-primary">{label}</p>
          {!isWorking && unavailable_reason && (
            <p className="truncate text-xs text-text-secondary">{unavailable_reason}</p>
          )}
        </div>
        <span
          className={`inline-flex items-center gap-1.5 rounded-pill px-2.5 py-1 text-[11px] font-semibold ${
            isWorking
              ? 'bg-accent-green/15 text-accent-green'
              : 'bg-accent-red/15 text-accent-red'
          }`}
        >
          <span
            className={`h-1.5 w-1.5 rounded-full ${
              isWorking ? 'bg-accent-green' : 'bg-accent-red'
            }`}
          />
          {isWorking ? 'OK' : 'Indisponível'}
        </span>
      </motion.button>

      <AnimatePresence initial={false}>
        {editing && (
          <motion.div
            layout
            initial={{ opacity: 0, height: 0 }}
            animate={{ opacity: 1, height: 'auto' }}
            exit={{ opacity: 0, height: 0 }}
            transition={{ type: 'spring', stiffness: 280, damping: 32 }}
            className="overflow-hidden"
          >
            <div className="mt-3 space-y-2">
              <textarea
                value={reason}
                onChange={(e) => setReason(e.target.value)}
                rows={2}
                placeholder="Quebrado, em manutenção, sem reagente…"
                aria-label="Motivo de indisponibilidade"
                className="w-full resize-none rounded-2xl border border-border bg-surface px-3 py-2 text-sm text-text-primary placeholder:text-text-tertiary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-blue focus-visible:ring-offset-2 focus-visible:ring-offset-card"
              />
              <div className="flex gap-2">
                <motion.button
                  type="button"
                  whileTap={{ scale: 0.96 }}
                  onClick={handleSaveReason}
                  className="flex-1 rounded-pill bg-accent-red px-4 py-2 text-sm font-semibold text-white focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-blue focus-visible:ring-offset-2 focus-visible:ring-offset-card"
                >
                  Marcar indisponível
                </motion.button>
                <motion.button
                  type="button"
                  whileTap={{ scale: 0.96 }}
                  onClick={() => setEditing(false)}
                  className="rounded-pill border border-border bg-surface px-4 py-2 text-sm font-medium text-text-secondary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-blue focus-visible:ring-offset-2 focus-visible:ring-offset-card"
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
