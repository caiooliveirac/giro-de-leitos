'use client';

import { useCallback, useEffect, useState } from 'react';
import { motion, useAnimationControls } from 'framer-motion';
import { Delete } from 'lucide-react';

interface PinPadProps {
  length?: number;
  onSubmit: (pin: string) => void | Promise<void>;
  onCancel?: () => void;
  error?: string | null;
  title?: string;
  description?: string;
}

const KEYS: Array<{ label: string; value: string | 'del' | 'noop'; aria: string }> = [
  { label: '1', value: '1', aria: 'Dígito 1' },
  { label: '2', value: '2', aria: 'Dígito 2' },
  { label: '3', value: '3', aria: 'Dígito 3' },
  { label: '4', value: '4', aria: 'Dígito 4' },
  { label: '5', value: '5', aria: 'Dígito 5' },
  { label: '6', value: '6', aria: 'Dígito 6' },
  { label: '7', value: '7', aria: 'Dígito 7' },
  { label: '8', value: '8', aria: 'Dígito 8' },
  { label: '9', value: '9', aria: 'Dígito 9' },
  { label: '', value: 'noop', aria: '' },
  { label: '0', value: '0', aria: 'Dígito 0' },
  { label: '⌫', value: 'del', aria: 'Apagar' },
];

export function PinPad({
  length = 4,
  onSubmit,
  onCancel,
  error,
  title = 'Confirme com seu PIN',
  description,
}: PinPadProps) {
  const [pin, setPin] = useState('');
  const [internalError, setInternalError] = useState<string | null>(null);
  const controls = useAnimationControls();

  const message = error ?? internalError;

  const triggerShake = useCallback(async () => {
    await controls.start({
      x: [0, -10, 10, -8, 8, -4, 4, 0],
      transition: { duration: 0.45 },
    });
  }, [controls]);

  useEffect(() => {
    if (error) {
      setPin('');
      void triggerShake();
    }
  }, [error, triggerShake]);

  const handlePress = useCallback(
    async (value: string | 'del' | 'noop') => {
      if (value === 'noop') return;
      setInternalError(null);
      if (value === 'del') {
        setPin((p) => p.slice(0, -1));
        return;
      }
      const next = (pin + value).slice(0, length);
      setPin(next);
      if (next.length === length) {
        try {
          await onSubmit(next);
        } catch {
          setInternalError('PIN incorreto');
          setPin('');
          void triggerShake();
        }
      }
    },
    [pin, length, onSubmit, triggerShake],
  );

  return (
    <motion.div
      layout
      initial={{ opacity: 0, y: -8 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ type: 'spring', stiffness: 360, damping: 30 }}
      className="mt-3 rounded-card border border-border bg-card p-4"
      role="group"
      aria-label="Teclado de PIN"
    >
      <div className="text-center">
        <p className="text-sm font-medium text-text-primary">{title}</p>
        {description && (
          <p className="mt-1 text-xs text-text-secondary">{description}</p>
        )}
      </div>

      <motion.div
        animate={controls}
        className="mt-4 flex items-center justify-center gap-3"
      >
        {Array.from({ length }).map((_, i) => {
          const filled = i < pin.length;
          return (
            <motion.div
              key={i}
              layout
              className={`h-3.5 w-3.5 rounded-full border ${
                filled
                  ? 'border-accent-blue bg-accent-blue'
                  : 'border-border bg-transparent'
              }`}
              animate={{ scale: filled ? 1.15 : 1 }}
              transition={{ type: 'spring', stiffness: 500, damping: 24 }}
            />
          );
        })}
      </motion.div>

      {message && (
        <p className="mt-2 text-center text-xs font-medium text-accent-red" role="alert">
          {message}
        </p>
      )}

      <div className="mx-auto mt-5 grid max-w-[280px] grid-cols-3 gap-3">
        {KEYS.map((k, idx) => {
          if (k.value === 'noop') {
            return <div key={`empty-${idx}`} aria-hidden />;
          }
          return (
            <motion.div
              key={`${k.label}-${idx}`}
              role="button"
              tabIndex={0}
              aria-label={k.aria}
              whileTap={{ scale: 0.92 }}
              onClick={() => void handlePress(k.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter' || e.key === ' ') {
                  e.preventDefault();
                  void handlePress(k.value);
                }
              }}
              className="flex h-[64px] cursor-pointer select-none items-center justify-center rounded-pill bg-surface text-2xl font-semibold tabular-nums text-text-primary transition focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-blue focus-visible:ring-offset-2 focus-visible:ring-offset-card hover:bg-border/40"
            >
              {k.value === 'del' ? <Delete size={22} aria-hidden /> : k.label}
            </motion.div>
          );
        })}
      </div>

      {onCancel && (
        <div className="mt-4 flex justify-center">
          <button
            type="button"
            onClick={onCancel}
            className="text-sm text-text-secondary underline-offset-2 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-blue focus-visible:ring-offset-2 focus-visible:ring-offset-card"
          >
            Cancelar
          </button>
        </div>
      )}
    </motion.div>
  );
}
