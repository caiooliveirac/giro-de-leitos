'use client';

import { useState } from 'react';
import { useRouter } from 'next/navigation';
import { motion } from 'framer-motion';
import { Eye, EyeOff, ShieldCheck } from 'lucide-react';
import { apiFetch, ApiError } from '@/lib/api';
import { useToast } from '@/lib/toast';
import { ToastViewport } from '@/components/shared/ToastViewport';

const MIN_LEN = 6;

export default function TrocarSenhaPage() {
  const router = useRouter();
  const toast = useToast();
  const [pw, setPw] = useState('');
  const [confirm, setConfirm] = useState('');
  const [show, setShow] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const lenOk = pw.length >= MIN_LEN;
  const matchOk = confirm.length > 0 && pw === confirm;
  const canSubmit = lenOk && matchOk && !loading;

  const submit = async () => {
    if (!canSubmit) return;
    setLoading(true);
    setError(null);
    try {
      await apiFetch('/api/auth/me/password', {
        method: 'POST',
        body: JSON.stringify({ new_password: pw }),
      });
      toast.success('Senha atualizada');
      router.replace('/');
    } catch (err) {
      const msg =
        err instanceof ApiError
          ? err.message || 'Falha ao trocar senha'
          : 'Falha ao trocar senha';
      setError(msg);
    } finally {
      setLoading(false);
    }
  };

  return (
    <main className="mx-auto min-h-dvh w-full max-w-[520px] px-5 pb-12 pt-10">
      <motion.div
        initial={{ opacity: 0, y: 8 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ type: 'spring', stiffness: 320, damping: 32 }}
      >
        <div className="flex h-12 w-12 items-center justify-center rounded-pill bg-accent-blue/10 text-accent-blue">
          <ShieldCheck size={22} />
        </div>
        <h1 className="mt-4 text-[28px] font-semibold leading-[1.15] tracking-tight text-ink">
          Escolha uma nova senha
        </h1>
        <p className="mt-2 text-[15px] text-ink-2">
          Sua senha atual foi gerada temporariamente. Defina uma nova senha com pelo
          menos {MIN_LEN} caracteres pra continuar.
        </p>
      </motion.div>

      <div className="mt-6 space-y-4">
        <div className="field">
          <label className="field-label" htmlFor="new-password">
            Nova senha
          </label>
          <div className="relative">
            <input
              id="new-password"
              type={show ? 'text' : 'password'}
              autoComplete="new-password"
              value={pw}
              onChange={(e) => {
                setPw(e.target.value);
                if (error) setError(null);
              }}
              className="w-full rounded-[12px] border border-line bg-surface px-3.5 py-3 pr-11 text-base text-ink placeholder:text-ink-3 focus:border-[var(--accent)] focus:bg-surface-elev focus:outline-none"
              placeholder={`pelo menos ${MIN_LEN} caracteres`}
              disabled={loading}
            />
            <button
              type="button"
              onClick={() => setShow((v) => !v)}
              aria-label={show ? 'Ocultar senha' : 'Mostrar senha'}
              className="absolute right-2 top-1/2 flex h-8 w-8 -translate-y-1/2 items-center justify-center rounded-pill text-ink-3 hover:text-ink-2"
            >
              {show ? <EyeOff size={16} /> : <Eye size={16} />}
            </button>
          </div>
          {pw.length > 0 && !lenOk && (
            <p className="mt-1 text-xs text-warning-ink">
              Mínimo {MIN_LEN} caracteres.
            </p>
          )}
        </div>

        <div className="field">
          <label className="field-label" htmlFor="confirm-password">
            Confirme a nova senha
          </label>
          <input
            id="confirm-password"
            type={show ? 'text' : 'password'}
            autoComplete="new-password"
            value={confirm}
            onChange={(e) => {
              setConfirm(e.target.value);
              if (error) setError(null);
            }}
            className="w-full rounded-[12px] border border-line bg-surface px-3.5 py-3 text-base text-ink placeholder:text-ink-3 focus:border-[var(--accent)] focus:bg-surface-elev focus:outline-none"
            placeholder="repita a nova senha"
            disabled={loading}
          />
          {confirm.length > 0 && !matchOk && (
            <p className="mt-1 text-xs text-warning-ink">
              As senhas não conferem.
            </p>
          )}
        </div>

        {error && (
          <p className="rounded-card border border-critical/30 bg-critical-soft p-3 text-sm text-critical-ink">
            {error}
          </p>
        )}

        <button
          type="button"
          onClick={() => void submit()}
          disabled={!canSubmit}
          className="cta mt-2"
        >
          {loading ? 'Salvando…' : 'Salvar nova senha'}
        </button>
      </div>

      <ToastViewport />
    </main>
  );
}
