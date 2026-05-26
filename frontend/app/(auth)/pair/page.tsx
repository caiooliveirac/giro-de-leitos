'use client';

import { useEffect, useRef, useState, type ClipboardEvent, type KeyboardEvent } from 'react';
import { motion } from 'framer-motion';
import { useRouter } from 'next/navigation';
import Link from 'next/link';
import { apiFetch, ApiError } from '@/lib/api';
import { getOrCreateDeviceFingerprint } from '@/lib/device';
import { useToast } from '@/lib/toast';
import { ToastViewport } from '@/components/shared/ToastViewport';

const LENGTH = 6;

export default function PairPage() {
  const router = useRouter();
  const toast = useToast();
  const [digits, setDigits] = useState<string[]>(() => Array(LENGTH).fill(''));
  const [error, setError] = useState<string | null>(null);
  const [shaking, setShaking] = useState(false);
  const [loading, setLoading] = useState(false);
  const refs = useRef<Array<HTMLInputElement | null>>([]);

  const code = digits.join('');
  const complete = code.length === LENGTH && digits.every((d) => d.length === 1);

  useEffect(() => {
    refs.current[0]?.focus();
  }, []);

  const focusAt = (i: number) => {
    const el = refs.current[Math.max(0, Math.min(LENGTH - 1, i))];
    el?.focus();
    el?.select();
  };

  const setAt = (i: number, value: string) => {
    setDigits((d) => {
      const next = d.slice();
      next[i] = value;
      return next;
    });
  };

  const handleChange = (i: number, raw: string) => {
    if (error) setError(null);
    const digit = raw.replace(/\D/g, '').slice(-1);
    if (!digit) {
      setAt(i, '');
      return;
    }
    setAt(i, digit);
    if (i < LENGTH - 1) focusAt(i + 1);
  };

  const handleKeyDown = (i: number, e: KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'Backspace') {
      if (digits[i]) {
        setAt(i, '');
        return;
      }
      e.preventDefault();
      if (i > 0) {
        setAt(i - 1, '');
        focusAt(i - 1);
      }
    } else if (e.key === 'ArrowLeft' && i > 0) {
      e.preventDefault();
      focusAt(i - 1);
    } else if (e.key === 'ArrowRight' && i < LENGTH - 1) {
      e.preventDefault();
      focusAt(i + 1);
    } else if (e.key === 'Enter' && complete) {
      void submit(code);
    }
  };

  const handlePaste = (e: ClipboardEvent<HTMLInputElement>) => {
    const text = e.clipboardData.getData('text').replace(/\D/g, '').slice(0, LENGTH);
    if (!text) return;
    e.preventDefault();
    const next = Array(LENGTH).fill('') as string[];
    for (let i = 0; i < text.length; i++) next[i] = text[i]!;
    setDigits(next);
    focusAt(Math.min(text.length, LENGTH - 1));
  };

  const triggerShake = () => {
    setShaking(true);
    setTimeout(() => setShaking(false), 380);
  };

  const submit = async (value: string) => {
    if (loading || value.length !== LENGTH) return;
    setLoading(true);
    setError(null);
    try {
      const fingerprint = getOrCreateDeviceFingerprint();
      await apiFetch('/api/auth/device/pair', {
        method: 'POST',
        body: JSON.stringify({
          pairing_code: value,
          device_fingerprint: fingerprint,
          label:
            typeof navigator !== 'undefined' ? navigator.userAgent.slice(0, 120) : 'tablet',
        }),
      });
      toast.success('Tablet pareado');
      router.push('/shift');
    } catch (err) {
      const msg = err instanceof ApiError ? err.message : 'Código inválido';
      setError(msg);
      triggerShake();
      setDigits(Array(LENGTH).fill(''));
      setTimeout(() => focusAt(0), 60);
    } finally {
      setLoading(false);
    }
  };

  return (
    <main className="mx-auto flex min-h-dvh w-full max-w-[520px] flex-col px-5 pb-10 pt-12">
      <motion.div
        initial={{ opacity: 0, y: 12 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ type: 'spring', stiffness: 320, damping: 30 }}
      >
        <p className="text-[11px] font-semibold uppercase tracking-[0.14em] text-ink-3">
          Primeiro uso
        </p>
        <h1 className="mt-1 text-[28px] font-semibold leading-[1.15] tracking-tight text-ink">
          Identificar este<br />aparelho
        </h1>
        <p className="mt-2 text-[15px] leading-relaxed text-ink-2">
          Peça o código de 6 dígitos pro seu coordenador. O código expira em{' '}
          <strong className="text-ink">10 minutos</strong>.
        </p>
        <p className="mt-1 text-[13px] text-ink-3">
          Esse aparelho fica vinculado à unidade dele por 30 dias.
        </p>
      </motion.div>

      <motion.div
        className="code-dots"
        animate={shaking ? { x: [0, -10, 10, -8, 8, -4, 4, 0] } : { x: 0 }}
        transition={{ duration: 0.38 }}
      >
        {digits.map((d, i) => (
          <input
            key={i}
            ref={(el) => {
              refs.current[i] = el;
            }}
            className="code-cell"
            data-on={d.length > 0}
            data-err={error != null && d.length > 0}
            value={d}
            onChange={(e) => handleChange(i, e.target.value)}
            onKeyDown={(e) => handleKeyDown(i, e)}
            onPaste={handlePaste}
            onFocus={(e) => e.currentTarget.select()}
            inputMode="numeric"
            pattern="[0-9]*"
            autoComplete={i === 0 ? 'one-time-code' : 'off'}
            maxLength={1}
            aria-label={`Dígito ${i + 1}`}
            disabled={loading}
          />
        ))}
      </motion.div>

      {error && <p className="attempts" role="alert">{error}</p>}

      <button
        type="button"
        className="cta mt-6"
        disabled={!complete || loading}
        onClick={() => void submit(code)}
      >
        {loading ? 'Pareando…' : 'Parear aparelho'}
      </button>

      <div className="mt-6 text-center">
        <Link href="/" className="back-btn">
          Voltar
        </Link>
      </div>
      <ToastViewport />
    </main>
  );
}
