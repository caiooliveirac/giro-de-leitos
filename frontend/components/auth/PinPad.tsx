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
  compact?: boolean;
}

// Disposição idêntica ao design/src/pin.jsx:
// 1 2 3
// 4 5 6
// 7 8 9
// ·  0  ⌫
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
  compact = false,
}: PinPadProps) {
  const [pin, setPin] = useState('');
  const [internalError, setInternalError] = useState<string | null>(null);
  const [shaking, setShaking] = useState(false);
  const controls = useAnimationControls();

  const message = error ?? internalError;

  const triggerShake = useCallback(async () => {
    setShaking(true);
    await controls.start({
      x: [0, -10, 10, -8, 8, -4, 4, 0],
      transition: { duration: 0.45 },
    });
    setShaking(false);
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
      if (pin.length >= length) return;
      const next = (pin + value).slice(0, length);
      setPin(next);
      if (next.length === length) {
        // 120ms tick pro último ponto preencher visualmente
        setTimeout(() => {
          void (async () => {
            try {
              await onSubmit(next);
            } catch {
              setInternalError('PIN incorreto · tente de novo');
              setPin('');
              void triggerShake();
            }
          })();
        }, 120);
      }
    },
    [pin, length, onSubmit, triggerShake],
  );

  const dotSize = compact ? 'h-2.5 w-2.5' : 'h-3.5 w-3.5';
  const keyHeight = compact ? 'h-12' : 'h-16';
  const keyRadius = compact ? 'rounded-2xl' : 'rounded-[22px]';
  const keyFont = compact ? 'text-xl' : 'text-[26px]';

  return (
    <motion.div
      layout
      initial={{ opacity: 0, y: -8 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ type: 'spring', stiffness: 360, damping: 30 }}
      className={
        compact
          ? 'mt-3 flex flex-col items-center'
          : 'mt-3 rounded-card border border-line bg-surface-elev p-4'
      }
      role="group"
      aria-label="Teclado de PIN"
    >
      {!compact && (
        <div className="text-center">
          <p className="text-sm font-semibold text-ink">{title}</p>
          {description && <p className="mt-1 text-xs text-ink-2">{description}</p>}
        </div>
      )}

      <motion.div
        animate={controls}
        className={`flex items-center justify-center ${compact ? 'mb-3 mt-4 gap-2.5' : 'mb-5 mt-6 gap-3.5'}`}
      >
        {Array.from({ length }).map((_, i) => {
          const filled = i < pin.length;
          return (
            <motion.div
              key={i}
              layout
              className={`${dotSize} rounded-full border-[1.5px] ${
                shaking
                  ? 'border-critical bg-critical'
                  : filled
                    ? 'border-ink bg-ink'
                    : 'border-line-strong bg-transparent'
              }`}
              animate={{ scale: filled ? 1.08 : 1 }}
              transition={{ type: 'spring', stiffness: 500, damping: 24 }}
            />
          );
        })}
      </motion.div>

      <div
        className={`mx-auto grid w-full max-w-[300px] grid-cols-3 ${compact ? 'gap-2' : 'gap-2.5 px-2'}`}
      >
        {KEYS.map((k, idx) => {
          if (k.value === 'noop') {
            return <div key={`empty-${idx}`} aria-hidden />;
          }
          return (
            <motion.button
              key={`${k.label}-${idx}`}
              type="button"
              aria-label={k.aria}
              whileTap={{ scale: 0.93 }}
              onClick={() => void handlePress(k.value)}
              className={`flex ${keyHeight} ${keyRadius} ${keyFont} items-center justify-center border border-line bg-surface-elev font-medium tabular-nums text-ink transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)] focus-visible:ring-offset-2 focus-visible:ring-offset-[var(--surface)] active:bg-surface-2`}
            >
              {k.value === 'del' ? <Delete size={compact ? 18 : 22} aria-hidden /> : k.label}
            </motion.button>
          );
        })}
      </div>

      {message && (
        <p
          className="mt-3.5 text-center text-xs font-medium text-critical-ink"
          role="alert"
        >
          {message}
        </p>
      )}

      {onCancel && (
        <div className="mt-4 flex justify-center">
          <button
            type="button"
            onClick={onCancel}
            className="text-sm text-ink-2 underline-offset-2 hover:underline focus-visible:outline-none"
          >
            cancelar
          </button>
        </div>
      )}
    </motion.div>
  );
}
