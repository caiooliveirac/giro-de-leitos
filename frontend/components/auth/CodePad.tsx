'use client';

import { useCallback, useEffect, useState } from 'react';
import { motion, useAnimationControls } from 'framer-motion';
import { Delete } from 'lucide-react';

interface CodePadProps {
  length?: number;
  onChange?: (code: string) => void;
  onSubmit: (code: string) => void | Promise<void>;
  error?: string | null;
  title?: string;
  description?: string;
  autoSubmit?: boolean;
  loading?: boolean;
  submitLabel?: string;
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

export function CodePad({
  length = 6,
  onChange,
  onSubmit,
  error,
  title = 'Digite o código',
  description,
  autoSubmit = false,
  loading = false,
  submitLabel = 'Confirmar',
}: CodePadProps) {
  const [code, setCode] = useState('');
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
      setCode('');
      void triggerShake();
    }
  }, [error, triggerShake]);

  useEffect(() => {
    onChange?.(code);
  }, [code, onChange]);

  const submit = useCallback(
    async (value: string) => {
      try {
        await onSubmit(value);
      } catch {
        setInternalError('Código inválido');
        setCode('');
        void triggerShake();
      }
    },
    [onSubmit, triggerShake],
  );

  const handlePress = useCallback(
    async (value: string | 'del' | 'noop') => {
      if (value === 'noop' || loading) return;
      setInternalError(null);
      if (value === 'del') {
        setCode((c) => c.slice(0, -1));
        return;
      }
      setCode((prev) => {
        const next = (prev + value).slice(0, length);
        if (autoSubmit && next.length === length) {
          void submit(next);
        }
        return next;
      });
    },
    [length, loading, autoSubmit, submit],
  );

  return (
    <motion.div
      layout
      initial={{ opacity: 0, y: -8 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ type: 'spring', stiffness: 360, damping: 30 }}
      className="rounded-card border border-border bg-card p-4"
      role="group"
      aria-label="Teclado de código"
    >
      <div className="text-center">
        <p className="text-sm font-medium text-text-primary">{title}</p>
        {description && <p className="mt-1 text-xs text-text-secondary">{description}</p>}
      </div>

      <motion.div animate={controls} className="mt-4 flex items-center justify-center gap-2">
        {Array.from({ length }).map((_, i) => {
          const filled = i < code.length;
          return (
            <motion.div
              key={i}
              layout
              className={`flex h-12 w-9 items-center justify-center rounded-xl border text-xl font-semibold tabular-nums ${
                filled
                  ? 'border-accent-blue bg-accent-blue/10 text-text-primary'
                  : 'border-border bg-surface text-text-tertiary'
              }`}
            >
              {filled ? code[i] : ''}
            </motion.div>
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
          if (k.value === 'noop') return <div key={`empty-${idx}`} aria-hidden />;
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
              className="flex h-[60px] cursor-pointer select-none items-center justify-center rounded-pill bg-surface text-2xl font-semibold tabular-nums text-text-primary transition focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-blue focus-visible:ring-offset-2 focus-visible:ring-offset-card hover:bg-border/40"
            >
              {k.value === 'del' ? <Delete size={22} aria-hidden /> : k.label}
            </motion.div>
          );
        })}
      </div>

      {!autoSubmit && (
        <div className="mt-4 flex justify-center">
          <button
            type="button"
            disabled={code.length !== length || loading}
            onClick={() => void submit(code)}
            className="rounded-pill bg-accent-blue px-6 py-2.5 text-sm font-semibold text-white disabled:opacity-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-blue focus-visible:ring-offset-2 focus-visible:ring-offset-card"
          >
            {loading ? 'Validando…' : submitLabel}
          </button>
        </div>
      )}
    </motion.div>
  );
}
