'use client';

import { useEffect, useRef, useState, type ClipboardEvent, type KeyboardEvent } from 'react';
import { motion } from 'framer-motion';
import { useRouter } from 'next/navigation';
import Link from 'next/link';
import {
  apiFetch,
  ApiError,
  type DeviceSelfPairResponse,
} from '@/lib/api';
import { getOrCreateDeviceFingerprint } from '@/lib/device';
import { useToast } from '@/lib/toast';
import { ToastViewport } from '@/components/shared/ToastViewport';
import { formatCpf, digitsOnly, validateCpf } from '@/lib/cpf';

const LENGTH = 6;
const PIN_LENGTH = 4;

type Mode = 'code' | 'self';

export default function PairPage() {
  const [mode, setMode] = useState<Mode>('code');

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
      </motion.div>

      <div
        role="tablist"
        aria-label="Modo de pareamento"
        className="mt-6 grid grid-cols-2 gap-1 rounded-pill border border-border bg-surface p-1"
      >
        <button
          type="button"
          role="tab"
          aria-selected={mode === 'code'}
          onClick={() => setMode('code')}
          className={`rounded-pill px-3 py-2 text-xs font-semibold transition ${
            mode === 'code'
              ? 'bg-card text-text-primary shadow-sm'
              : 'text-text-secondary hover:text-text-primary'
          }`}
        >
          Tenho código de 6 dígitos
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={mode === 'self'}
          onClick={() => setMode('self')}
          className={`rounded-pill px-3 py-2 text-xs font-semibold transition ${
            mode === 'self'
              ? 'bg-card text-text-primary shadow-sm'
              : 'text-text-secondary hover:text-text-primary'
          }`}
        >
          Já tenho cadastro nesta UPA
        </button>
      </div>

      {mode === 'code' ? <CodeMode /> : <SelfPairMode />}

      <p className="mt-5 text-center text-[12px] text-ink-3">
        {mode === 'code'
          ? 'Já tem cadastro? Use a aba ao lado pra parear com seu CPF/senha/PIN.'
          : 'Precisa do código? Peça pra um coordenador.'}
      </p>

      <div className="mt-6 text-center">
        <Link href="/" className="back-btn">
          Voltar
        </Link>
      </div>
      <ToastViewport />
    </main>
  );
}

// ─── Mode A: 6-digit code from coordinator ────────────────────────────────
function CodeMode() {
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
    <>
      <p className="mt-4 text-[15px] leading-relaxed text-ink-2">
        Peça o código de 6 dígitos pro seu coordenador. O código expira em{' '}
        <strong className="text-ink">10 minutos</strong>.
      </p>
      <p className="mt-1 text-[13px] text-ink-3">
        Esse aparelho fica vinculado à unidade dele por 30 dias.
      </p>

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
    </>
  );
}

// ─── Mode B: self-pair with existing credentials ──────────────────────────
function SelfPairMode() {
  const router = useRouter();
  const toast = useToast();
  const [cpf, setCpf] = useState('');
  const [password, setPassword] = useState('');
  const [pin, setPin] = useState<string[]>(() => Array(PIN_LENGTH).fill(''));
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [shaking, setShaking] = useState(false);
  const pinRefs = useRef<Array<HTMLInputElement | null>>([]);

  const cpfDigits = digitsOnly(cpf);
  const cpfOk = validateCpf(cpf);
  const pinStr = pin.join('');
  const pinOk = pinStr.length === PIN_LENGTH && pin.every((d) => /\d/.test(d));
  const passwordOk = password.length >= 8;
  const canSubmit = cpfOk && pinOk && passwordOk && !loading;

  const setPinAt = (i: number, value: string) => {
    setPin((d) => {
      const next = d.slice();
      next[i] = value;
      return next;
    });
  };

  const handlePinChange = (i: number, raw: string) => {
    if (error) setError(null);
    const digit = raw.replace(/\D/g, '').slice(-1);
    if (!digit) {
      setPinAt(i, '');
      return;
    }
    setPinAt(i, digit);
    if (i < PIN_LENGTH - 1) pinRefs.current[i + 1]?.focus();
  };

  const handlePinKeyDown = (i: number, e: KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'Backspace') {
      if (pin[i]) {
        setPinAt(i, '');
        return;
      }
      e.preventDefault();
      if (i > 0) {
        setPinAt(i - 1, '');
        pinRefs.current[i - 1]?.focus();
      }
    } else if (e.key === 'ArrowLeft' && i > 0) {
      e.preventDefault();
      pinRefs.current[i - 1]?.focus();
    } else if (e.key === 'ArrowRight' && i < PIN_LENGTH - 1) {
      e.preventDefault();
      pinRefs.current[i + 1]?.focus();
    }
  };

  const triggerShake = () => {
    setShaking(true);
    setTimeout(() => setShaking(false), 380);
  };

  const submit = async () => {
    if (!canSubmit) return;
    setLoading(true);
    setError(null);
    try {
      const fingerprint = getOrCreateDeviceFingerprint();
      const label =
        typeof navigator !== 'undefined' ? navigator.userAgent.slice(0, 60) : 'tablet';
      const result = await apiFetch<DeviceSelfPairResponse>(
        '/api/auth/device/self-pair',
        {
          method: 'POST',
          body: JSON.stringify({
            cpf: cpfDigits,
            password,
            pin: pinStr,
            device_fingerprint: fingerprint,
            label,
          }),
        },
      );
      try {
        window.localStorage.setItem(
          'gl_shift_user',
          JSON.stringify({
            id: result.user.id,
            name: result.user.name,
            role: result.user.role,
            unit_id: result.user.unit_id,
          }),
        );
      } catch {
        // ignore localStorage failures
      }
      toast.success('Aparelho pareado · plantão iniciado');
      router.push('/');
    } catch (err) {
      let msg = 'Não foi possível parear.';
      if (err instanceof ApiError) {
        if (err.status === 401) msg = 'Credenciais inválidas.';
        else if (err.status === 429)
          msg = 'Muitas tentativas. Tente novamente em alguns minutos.';
        else if (err.status === 403) msg = err.message;
        else if (err.status === 422) msg = 'Preencha todos os campos corretamente.';
      }
      setError(msg);
      triggerShake();
      setPin(Array(PIN_LENGTH).fill(''));
      setTimeout(() => pinRefs.current[0]?.focus(), 60);
    } finally {
      setLoading(false);
    }
  };

  return (
    <motion.div
      className="mt-5 space-y-4"
      animate={shaking ? { x: [0, -10, 10, -8, 8, -4, 4, 0] } : { x: 0 }}
      transition={{ duration: 0.38 }}
    >
      <p className="text-[14px] leading-relaxed text-ink-2">
        Use seu CPF, senha e PIN. O aparelho fica vinculado à sua UPA por 30
        dias e seu plantão já começa.
      </p>

      <div className="field">
        <label className="field-label" htmlFor="sp-cpf">
          CPF
        </label>
        <input
          id="sp-cpf"
          type="text"
          inputMode="numeric"
          autoComplete="username"
          className="input-shell tnum"
          value={formatCpf(cpf)}
          placeholder="000.000.000-00"
          data-err={cpf.length >= 11 && !cpfOk}
          onChange={(e) => setCpf(e.target.value)}
          disabled={loading}
        />
      </div>

      <div className="field">
        <label className="field-label" htmlFor="sp-pass">
          Senha
        </label>
        <input
          id="sp-pass"
          type="password"
          autoComplete="current-password"
          className="input-shell"
          value={password}
          placeholder="sua senha"
          onChange={(e) => setPassword(e.target.value)}
          disabled={loading}
        />
      </div>

      <div className="field">
        <label className="field-label" htmlFor="sp-pin-0">
          PIN de plantão · 4 dígitos
        </label>
        <div className="code-dots" style={{ justifyContent: 'flex-start', gap: 10 }}>
          {pin.map((d, i) => (
            <input
              key={i}
              id={i === 0 ? 'sp-pin-0' : undefined}
              ref={(el) => {
                pinRefs.current[i] = el;
              }}
              className="code-cell"
              data-on={d.length > 0}
              data-err={error != null && d.length > 0}
              value={d ? '•' : ''}
              onChange={(e) => handlePinChange(i, e.target.value)}
              onKeyDown={(e) => handlePinKeyDown(i, e)}
              onFocus={(e) => e.currentTarget.select()}
              inputMode="numeric"
              pattern="[0-9]*"
              autoComplete="off"
              maxLength={1}
              aria-label={`PIN dígito ${i + 1}`}
              disabled={loading}
            />
          ))}
        </div>
      </div>

      {error && (
        <p className="attempts" role="alert">
          {error}
        </p>
      )}

      <button
        type="button"
        className="cta mt-2"
        disabled={!canSubmit}
        onClick={() => void submit()}
      >
        {loading ? 'Pareando…' : 'Parear e iniciar plantão'}
      </button>
    </motion.div>
  );
}
