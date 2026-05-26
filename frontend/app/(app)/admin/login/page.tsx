'use client';

import { useState } from 'react';
import { useRouter } from 'next/navigation';
import { motion } from 'framer-motion';
import { useForm } from 'react-hook-form';
import { zodResolver } from '@hookform/resolvers/zod';
import { z } from 'zod';
import { ShieldCheck } from 'lucide-react';
import { apiFetch, ApiError } from '@/lib/api';
import { useToast } from '@/lib/toast';
import { ToastViewport } from '@/components/shared/ToastViewport';

const schema = z.object({
  email: z.string().email('Email inválido'),
  password: z.string().min(1, 'Senha obrigatória'),
});

type FormValues = z.infer<typeof schema>;

interface AdminLoginResponse {
  user: {
    id: string;
    name: string;
    role: 'admin';
  };
}

export default function AdminLoginPage() {
  const router = useRouter();
  const toast = useToast();
  const [loading, setLoading] = useState(false);
  const {
    register,
    handleSubmit,
    formState: { errors },
  } = useForm<FormValues>({ resolver: zodResolver(schema) });

  const onSubmit = async (values: FormValues) => {
    setLoading(true);
    try {
      const res = await apiFetch<AdminLoginResponse>('/api/auth/admin/login', {
        method: 'POST',
        body: JSON.stringify(values),
      });
      try {
        window.localStorage.setItem(
          'gl_admin_user',
          JSON.stringify({
            id: res.user.id,
            name: res.user.name,
            role: 'admin',
          }),
        );
      } catch {
        // ignore
      }
      toast.success('Bem-vindo, admin');
      router.push('/admin');
    } catch (err) {
      const msg = err instanceof ApiError ? err.message : 'Falha no login';
      toast.error(msg);
    } finally {
      setLoading(false);
    }
  };

  return (
    <main className="mx-auto flex min-h-dvh w-full max-w-[520px] flex-col px-4 pb-10 pt-16">
      <motion.div
        initial={{ opacity: 0, y: 12 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ type: 'spring', stiffness: 320, damping: 30 }}
        className="text-center"
      >
        <div className="mx-auto mb-4 flex h-14 w-14 items-center justify-center rounded-pill bg-accent-blue/10 text-accent-blue">
          <ShieldCheck size={26} />
        </div>
        <h1 className="text-2xl font-semibold tracking-tight text-text-primary">Admin global</h1>
        <p className="mt-2 text-sm text-text-secondary">
          Acesso restrito à administração do Giro.
        </p>
      </motion.div>

      <form
        className="mt-8 space-y-4 rounded-card border border-border bg-card p-5"
        onSubmit={handleSubmit(onSubmit)}
        noValidate
      >
        <label className="block">
          <span className="mb-1.5 block text-xs font-medium text-text-secondary">Email</span>
          <input
            {...register('email')}
            type="email"
            autoComplete="email"
            className="w-full rounded-xl border border-border bg-surface px-3.5 py-3 text-base text-text-primary focus:border-accent-blue focus:outline-none focus:ring-2 focus:ring-accent-blue/30"
            placeholder="admin@giro.app"
          />
          {errors.email && (
            <span className="mt-1 block text-xs text-accent-red" role="alert">
              {errors.email.message}
            </span>
          )}
        </label>
        <label className="block">
          <span className="mb-1.5 block text-xs font-medium text-text-secondary">Senha</span>
          <input
            {...register('password')}
            type="password"
            autoComplete="current-password"
            className="w-full rounded-xl border border-border bg-surface px-3.5 py-3 text-base text-text-primary focus:border-accent-blue focus:outline-none focus:ring-2 focus:ring-accent-blue/30"
          />
          {errors.password && (
            <span className="mt-1 block text-xs text-accent-red" role="alert">
              {errors.password.message}
            </span>
          )}
        </label>
        <button
          type="submit"
          disabled={loading}
          className="flex w-full items-center justify-center rounded-pill bg-accent-blue px-6 py-3 text-base font-semibold text-white disabled:opacity-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-blue focus-visible:ring-offset-2 focus-visible:ring-offset-card"
        >
          {loading ? 'Entrando…' : 'Entrar'}
        </button>
      </form>
      <ToastViewport />
    </main>
  );
}
