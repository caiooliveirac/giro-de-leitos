'use client';

import { useEffect, useMemo, useRef, useState } from 'react';
import { AnimatePresence, motion } from 'framer-motion';
import { useForm } from 'react-hook-form';
import { zodResolver } from '@hookform/resolvers/zod';
import { z } from 'zod';
import {
  ArrowLeft,
  ArrowRight,
  Camera,
  CheckCircle2,
  Lock,
  ShieldCheck,
  Sparkles,
  UserRound,
} from 'lucide-react';
import { apiFetch, ApiError } from '@/lib/api';
import { formatCpf, validateCpf, digitsOnly as cpfDigits } from '@/lib/cpf';
import { formatPhoneBR, isValidPhoneBR, digitsOnly as phoneDigits } from '@/lib/phone';
import { useToast } from '@/lib/toast';
import { ToastViewport } from '@/components/shared/ToastViewport';

interface InvitePreview {
  type: 'coordinator' | 'professional';
  unit_name: string | null;
  inviter_name: string;
  expires_at: string;
}

const CARGO_OPTIONS = ['Médico', 'Enfermeiro', 'Téc. Enfermagem', 'Outro'];
const PROFESSIONAL_CARGOS = new Set(['Médico', 'Enfermeiro']);

const stepATwoSchema = z.object({
  name: z.string().min(2, 'Informe o nome completo').max(160),
  cpf: z
    .string()
    .transform((v) => cpfDigits(v))
    .refine((v) => validateCpf(v), 'CPF inválido'),
  phone: z
    .string()
    .transform((v) => phoneDigits(v))
    .refine((v) => isValidPhoneBR(v), 'Telefone inválido'),
});

const stepAThreeSchema = z
  .object({
    cargo: z.enum(['Médico', 'Enfermeiro', 'Téc. Enfermagem', 'Outro']),
    coren_crm: z.string().max(40).optional().or(z.literal('')),
    photo_data_url: z.string().min(1, 'Adicione uma foto'),
  })
  .refine(
    (data) =>
      !PROFESSIONAL_CARGOS.has(data.cargo) || (data.coren_crm && data.coren_crm.length >= 3),
    {
      message: 'COREN/CRM obrigatório para médico e enfermeiro',
      path: ['coren_crm'],
    },
  );

const stepAFourSchema = z
  .object({
    password: z.string().min(8, 'Mínimo 8 caracteres'),
    password_confirm: z.string().min(8),
    pin: z.string().regex(/^\d{4}$/, 'PIN deve ter 4 dígitos'),
    pin_confirm: z.string().regex(/^\d{4}$/, 'PIN deve ter 4 dígitos'),
  })
  .refine((d) => d.password === d.password_confirm, {
    message: 'Senhas não conferem',
    path: ['password_confirm'],
  })
  .refine((d) => d.pin === d.pin_confirm, {
    message: 'PINs não conferem',
    path: ['pin_confirm'],
  });

type StepATwo = z.infer<typeof stepATwoSchema>;
type StepAThree = z.infer<typeof stepAThreeSchema>;
type StepAFour = z.infer<typeof stepAFourSchema>;

interface FormState {
  name: string;
  cpf: string;
  phone: string;
  cargo: string;
  coren_crm: string;
  photo_data_url: string;
  password: string;
  pin: string;
  lgpd_accepted: boolean;
}

const EMPTY: FormState = {
  name: '',
  cpf: '',
  phone: '',
  cargo: '',
  coren_crm: '',
  photo_data_url: '',
  password: '',
  pin: '',
  lgpd_accepted: false,
};

export default function InvitePage({ params }: { params: { token: string } }) {
  const toast = useToast();
  const [step, setStep] = useState<1 | 2 | 3 | 4 | 5 | 6>(1);
  const [preview, setPreview] = useState<InvitePreview | null>(null);
  const [previewError, setPreviewError] = useState<string | null>(null);
  const [loadingPreview, setLoadingPreview] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [data, setData] = useState<FormState>(EMPTY);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const res = await apiFetch<InvitePreview>(
          `/api/invites/${encodeURIComponent(params.token)}/preview`,
        );
        if (!cancelled) setPreview(res);
      } catch (err) {
        if (!cancelled) {
          const msg =
            err instanceof ApiError ? err.message : 'Convite inválido ou expirado';
          setPreviewError(msg);
        }
      } finally {
        if (!cancelled) setLoadingPreview(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [params.token]);

  const submit = async () => {
    setSubmitting(true);
    try {
      await apiFetch(`/api/invites/${encodeURIComponent(params.token)}/accept`, {
        method: 'POST',
        body: JSON.stringify({
          name: data.name,
          cpf: data.cpf,
          phone: data.phone,
          cargo: data.cargo,
          coren_crm: data.coren_crm || null,
          password: data.password,
          pin: data.pin,
          photo_url: data.photo_data_url,
          lgpd_accepted: data.lgpd_accepted,
        }),
      });
      setStep(6);
    } catch (err) {
      const msg = err instanceof ApiError ? err.message : 'Falha ao enviar cadastro';
      toast.error(msg);
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <main className="mx-auto min-h-dvh w-full max-w-[520px] px-4 pb-16 pt-8">
      <ProgressBar step={step} />
      <AnimatePresence mode="wait">
        {loadingPreview && (
          <StepShell key="loading">
            <p className="text-center text-sm text-text-secondary">Carregando convite…</p>
          </StepShell>
        )}

        {!loadingPreview && previewError && (
          <StepShell key="invalid">
            <div className="text-center">
              <div className="mx-auto mb-3 flex h-12 w-12 items-center justify-center rounded-pill bg-accent-red/10 text-accent-red">
                <ShieldCheck size={24} />
              </div>
              <h2 className="text-xl font-semibold text-text-primary">
                Convite inválido ou expirado
              </h2>
              <p className="mt-2 text-sm text-text-secondary">{previewError}</p>
            </div>
          </StepShell>
        )}

        {!loadingPreview && !previewError && step === 1 && preview && (
          <StepWelcome key="s1" preview={preview} onNext={() => setStep(2)} />
        )}

        {step === 2 && (
          <StepPersonal
            key="s2"
            initial={data}
            onBack={() => setStep(1)}
            onNext={(v) => {
              setData((d) => ({ ...d, ...v }));
              setStep(3);
            }}
          />
        )}

        {step === 3 && (
          <StepRole
            key="s3"
            initial={data}
            onBack={() => setStep(2)}
            onNext={(v) => {
              setData((d) => ({ ...d, ...v }));
              setStep(4);
            }}
          />
        )}

        {step === 4 && (
          <StepSecurity
            key="s4"
            initial={data}
            onBack={() => setStep(3)}
            onNext={(v) => {
              setData((d) => ({ ...d, password: v.password, pin: v.pin }));
              setStep(5);
            }}
          />
        )}

        {step === 5 && (
          <StepLgpd
            key="s5"
            data={data}
            submitting={submitting}
            onBack={() => setStep(4)}
            onAccept={(v) => setData((d) => ({ ...d, lgpd_accepted: v }))}
            onSubmit={submit}
          />
        )}

        {step === 6 && <StepDone key="s6" />}
      </AnimatePresence>
      <ToastViewport />
    </main>
  );
}

function ProgressBar({ step }: { step: number }) {
  const total = 5;
  const current = Math.min(step, total);
  return (
    <div className="mb-6 flex items-center gap-1.5" aria-label={`Etapa ${current} de ${total}`}>
      {Array.from({ length: total }).map((_, i) => {
        const active = i + 1 <= current;
        return (
          <motion.div
            key={i}
            className={`h-1 flex-1 rounded-pill ${
              active ? 'bg-accent-blue' : 'bg-border'
            }`}
            animate={{ scale: active ? 1 : 0.98 }}
            transition={{ type: 'spring', stiffness: 360, damping: 30 }}
          />
        );
      })}
    </div>
  );
}

function StepShell({ children }: { children: React.ReactNode }) {
  return (
    <motion.section
      initial={{ opacity: 0, y: 16 }}
      animate={{ opacity: 1, y: 0 }}
      exit={{ opacity: 0, y: -16 }}
      transition={{ type: 'spring', stiffness: 320, damping: 32 }}
      className="rounded-card border border-border bg-card p-5 shadow-card"
    >
      {children}
    </motion.section>
  );
}

function PrimaryButton({
  children,
  onClick,
  type = 'button',
  disabled,
  loading,
}: {
  children: React.ReactNode;
  onClick?: () => void;
  type?: 'button' | 'submit';
  disabled?: boolean;
  loading?: boolean;
}) {
  return (
    <motion.button
      type={type}
      whileTap={{ scale: disabled ? 1 : 0.97 }}
      onClick={onClick}
      disabled={disabled || loading}
      className="flex w-full items-center justify-center gap-2 rounded-pill bg-accent-blue px-6 py-3.5 text-base font-semibold text-white disabled:opacity-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-blue focus-visible:ring-offset-2 focus-visible:ring-offset-card"
    >
      {loading ? 'Enviando…' : children}
    </motion.button>
  );
}

function SecondaryButton({
  children,
  onClick,
}: {
  children: React.ReactNode;
  onClick?: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="flex items-center gap-1.5 rounded-pill border border-border bg-surface px-4 py-2 text-sm font-medium text-text-secondary transition focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-blue hover:text-text-primary"
    >
      {children}
    </button>
  );
}

function StepWelcome({
  preview,
  onNext,
}: {
  preview: InvitePreview;
  onNext: () => void;
}) {
  const role = preview.type === 'coordinator' ? 'Coordenador' : 'Profissional';
  const target = preview.unit_name ? ` na ${preview.unit_name}` : '';
  return (
    <StepShell>
      <div className="text-center">
        <motion.div
          initial={{ scale: 0.6, opacity: 0 }}
          animate={{ scale: 1, opacity: 1 }}
          transition={{ type: 'spring', stiffness: 360, damping: 22 }}
          className="mx-auto mb-4 flex h-14 w-14 items-center justify-center rounded-pill bg-accent-blue/10 text-accent-blue"
        >
          <Sparkles size={26} />
        </motion.div>
        <h1 className="text-2xl font-semibold tracking-tight text-text-primary">
          Bem-vindo ao Giro
        </h1>
        <p className="mt-2 text-sm text-text-secondary">
          Você foi convidado{target} como <strong className="text-text-primary">{role}</strong> por{' '}
          <strong className="text-text-primary">{preview.inviter_name}</strong>.
        </p>
        <p className="mt-1 text-xs text-text-tertiary">
          Convite válido até {new Date(preview.expires_at).toLocaleString('pt-BR')}.
        </p>
      </div>
      <div className="mt-6">
        <PrimaryButton onClick={onNext}>
          Começar <ArrowRight size={18} />
        </PrimaryButton>
      </div>
    </StepShell>
  );
}

function StepPersonal({
  initial,
  onBack,
  onNext,
}: {
  initial: FormState;
  onBack: () => void;
  onNext: (v: StepATwo) => void;
}) {
  const {
    register,
    handleSubmit,
    formState: { errors },
    setValue,
    watch,
  } = useForm<StepATwo>({
    resolver: zodResolver(stepATwoSchema),
    defaultValues: {
      name: initial.name,
      cpf: initial.cpf,
      phone: initial.phone,
    },
  });

  const cpfValue = watch('cpf');
  const phoneValue = watch('phone');

  return (
    <StepShell>
      <h2 className="text-xl font-semibold text-text-primary">Seus dados</h2>
      <p className="mt-1 text-xs text-text-secondary">
        CPF é cifrado em repouso e só aparece mascarado.
      </p>

      <form className="mt-5 space-y-4" onSubmit={handleSubmit(onNext)} noValidate>
        <Field label="Nome completo" error={errors.name?.message}>
          <input
            {...register('name')}
            autoComplete="name"
            className="input-base"
            placeholder="Maria da Silva"
          />
        </Field>

        <Field label="CPF" error={errors.cpf?.message}>
          <input
            inputMode="numeric"
            autoComplete="off"
            value={formatCpf(cpfValue ?? '')}
            onChange={(e) => setValue('cpf', cpfDigits(e.target.value), { shouldValidate: true })}
            className="input-base"
            placeholder="000.000.000-00"
          />
        </Field>

        <Field label="Telefone (WhatsApp)" error={errors.phone?.message}>
          <input
            inputMode="tel"
            autoComplete="tel"
            value={formatPhoneBR(phoneValue ?? '')}
            onChange={(e) =>
              setValue('phone', phoneDigits(e.target.value), { shouldValidate: true })
            }
            className="input-base"
            placeholder="(11) 91234-5678"
          />
        </Field>

        <div className="flex items-center justify-between gap-3 pt-2">
          <SecondaryButton onClick={onBack}>
            <ArrowLeft size={16} /> Voltar
          </SecondaryButton>
          <PrimaryButton type="submit">
            Continuar <ArrowRight size={18} />
          </PrimaryButton>
        </div>
      </form>
      <InputStyles />
    </StepShell>
  );
}

function StepRole({
  initial,
  onBack,
  onNext,
}: {
  initial: FormState;
  onBack: () => void;
  onNext: (v: StepAThree) => void;
}) {
  const {
    register,
    handleSubmit,
    formState: { errors },
    setValue,
    watch,
  } = useForm<StepAThree>({
    resolver: zodResolver(stepAThreeSchema),
    defaultValues: {
      cargo: (initial.cargo as StepAThree['cargo']) || 'Médico',
      coren_crm: initial.coren_crm,
      photo_data_url: initial.photo_data_url,
    },
  });

  const cargo = watch('cargo');
  const photo = watch('photo_data_url');
  const fileRef = useRef<HTMLInputElement | null>(null);

  const handleFile = (file: File) => {
    const reader = new FileReader();
    reader.onload = () => {
      if (typeof reader.result === 'string') {
        setValue('photo_data_url', reader.result, { shouldValidate: true });
      }
    };
    reader.readAsDataURL(file);
  };

  const showCoren = PROFESSIONAL_CARGOS.has(cargo);

  return (
    <StepShell>
      <h2 className="text-xl font-semibold text-text-primary">Função e foto</h2>
      <p className="mt-1 text-xs text-text-secondary">
        A foto aparece pros colegas escolherem você ao iniciar plantão.
      </p>

      <form className="mt-5 space-y-4" onSubmit={handleSubmit(onNext)} noValidate>
        <Field label="Cargo" error={errors.cargo?.message}>
          <select {...register('cargo')} className="input-base">
            {CARGO_OPTIONS.map((c) => (
              <option key={c} value={c}>
                {c}
              </option>
            ))}
          </select>
        </Field>

        {showCoren && (
          <Field label="COREN ou CRM" error={errors.coren_crm?.message}>
            <input {...register('coren_crm')} className="input-base" placeholder="123456" />
          </Field>
        )}

        <Field label="Foto de perfil" error={errors.photo_data_url?.message}>
          <div className="flex items-center gap-4">
            <div className="flex h-24 w-24 shrink-0 items-center justify-center overflow-hidden rounded-full border border-border bg-surface">
              {photo ? (
                // eslint-disable-next-line @next/next/no-img-element
                <img src={photo} alt="Foto de perfil" className="h-full w-full object-cover" />
              ) : (
                <UserRound size={36} className="text-text-tertiary" aria-hidden />
              )}
            </div>
            <div className="flex-1">
              <input
                ref={fileRef}
                type="file"
                accept="image/*"
                capture="user"
                className="hidden"
                onChange={(e) => {
                  const f = e.target.files?.[0];
                  if (f) handleFile(f);
                }}
              />
              <button
                type="button"
                onClick={() => fileRef.current?.click()}
                className="flex items-center gap-1.5 rounded-pill border border-border bg-surface px-3 py-2 text-sm font-medium text-text-primary transition focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-blue hover:bg-border/40"
              >
                <Camera size={16} /> {photo ? 'Trocar foto' : 'Tirar/escolher foto'}
              </button>
            </div>
          </div>
        </Field>

        <div className="flex items-center justify-between gap-3 pt-2">
          <SecondaryButton onClick={onBack}>
            <ArrowLeft size={16} /> Voltar
          </SecondaryButton>
          <PrimaryButton type="submit">
            Continuar <ArrowRight size={18} />
          </PrimaryButton>
        </div>
      </form>
      <InputStyles />
    </StepShell>
  );
}

function passwordStrength(pwd: string): { score: 0 | 1 | 2 | 3 | 4; label: string } {
  let s = 0;
  if (pwd.length >= 8) s++;
  if (/[A-Z]/.test(pwd)) s++;
  if (/[0-9]/.test(pwd)) s++;
  if (/[^A-Za-z0-9]/.test(pwd)) s++;
  const labels = ['Muito fraca', 'Fraca', 'Razoável', 'Boa', 'Forte'];
  return { score: s as 0 | 1 | 2 | 3 | 4, label: labels[s] };
}

function StepSecurity({
  initial,
  onBack,
  onNext,
}: {
  initial: FormState;
  onBack: () => void;
  onNext: (v: { password: string; pin: string }) => void;
}) {
  const {
    register,
    handleSubmit,
    formState: { errors },
    watch,
  } = useForm<StepAFour>({
    resolver: zodResolver(stepAFourSchema),
    defaultValues: {
      password: initial.password,
      password_confirm: initial.password,
      pin: initial.pin,
      pin_confirm: initial.pin,
    },
  });

  const password = watch('password') ?? '';
  const strength = passwordStrength(password);

  return (
    <StepShell>
      <h2 className="text-xl font-semibold text-text-primary">Segurança</h2>
      <p className="mt-1 text-xs text-text-secondary">
        Senha pra login web. PIN de 4 dígitos pra iniciar plantão no tablet.
      </p>

      <form
        className="mt-5 space-y-4"
        onSubmit={handleSubmit((v) => onNext({ password: v.password, pin: v.pin }))}
        noValidate
      >
        <Field label="Senha" error={errors.password?.message}>
          <input
            type="password"
            autoComplete="new-password"
            {...register('password')}
            className="input-base"
            placeholder="Mínimo 8 caracteres"
          />
          {password.length > 0 && (
            <div className="mt-2">
              <div className="flex h-1.5 gap-1">
                {[0, 1, 2, 3].map((i) => (
                  <div
                    key={i}
                    className={`flex-1 rounded-pill ${
                      i < strength.score
                        ? strength.score >= 3
                          ? 'bg-accent-green'
                          : strength.score === 2
                            ? 'bg-accent-amber'
                            : 'bg-accent-red'
                        : 'bg-border'
                    }`}
                  />
                ))}
              </div>
              <p className="mt-1 text-[11px] text-text-tertiary">Força: {strength.label}</p>
            </div>
          )}
        </Field>

        <Field label="Confirmar senha" error={errors.password_confirm?.message}>
          <input
            type="password"
            autoComplete="new-password"
            {...register('password_confirm')}
            className="input-base"
          />
        </Field>

        <Field label="PIN (4 dígitos)" error={errors.pin?.message}>
          <input
            type="password"
            inputMode="numeric"
            maxLength={4}
            autoComplete="off"
            {...register('pin')}
            className="input-base tracking-[0.5em]"
            placeholder="••••"
          />
        </Field>

        <Field label="Confirmar PIN" error={errors.pin_confirm?.message}>
          <input
            type="password"
            inputMode="numeric"
            maxLength={4}
            autoComplete="off"
            {...register('pin_confirm')}
            className="input-base tracking-[0.5em]"
            placeholder="••••"
          />
        </Field>

        <div className="flex items-center justify-between gap-3 pt-2">
          <SecondaryButton onClick={onBack}>
            <ArrowLeft size={16} /> Voltar
          </SecondaryButton>
          <PrimaryButton type="submit">
            Continuar <ArrowRight size={18} />
          </PrimaryButton>
        </div>
      </form>
      <InputStyles />
    </StepShell>
  );
}

function StepLgpd({
  data,
  submitting,
  onBack,
  onAccept,
  onSubmit,
}: {
  data: FormState;
  submitting: boolean;
  onBack: () => void;
  onAccept: (v: boolean) => void;
  onSubmit: () => void;
}) {
  const summary = useMemo(
    () => [
      { label: 'Nome', value: data.name },
      { label: 'CPF', value: formatCpf(data.cpf) },
      { label: 'Telefone', value: formatPhoneBR(data.phone) },
      { label: 'Cargo', value: data.cargo + (data.coren_crm ? ` · ${data.coren_crm}` : '') },
    ],
    [data],
  );

  return (
    <StepShell>
      <div className="flex items-center gap-2">
        <div className="flex h-9 w-9 items-center justify-center rounded-pill bg-accent-blue/10 text-accent-blue">
          <Lock size={18} />
        </div>
        <h2 className="text-xl font-semibold text-text-primary">Privacidade e revisão</h2>
      </div>

      <div className="mt-4 rounded-card bg-surface p-4 text-xs leading-relaxed text-text-secondary">
        <p>
          O Giro segue a LGPD. Seus dados são usados apenas pra operação interna da UPA:
        </p>
        <ul className="mt-2 list-disc space-y-1 pl-5">
          <li>CPF é cifrado em repouso e exibido apenas mascarado (***.***.***-**).</li>
          <li>Toda ação (alta, óbito, transferência) gera log de auditoria.</li>
          <li>Seus dados não são compartilhados com terceiros.</li>
          <li>Você pode solicitar exclusão a qualquer momento via coordenador.</li>
        </ul>
      </div>

      <div className="mt-4 rounded-card border border-border bg-surface p-4">
        <p className="text-xs font-semibold uppercase tracking-wider text-text-tertiary">
          Resumo
        </p>
        <dl className="mt-2 space-y-1.5 text-sm">
          {summary.map((s) => (
            <div key={s.label} className="flex items-baseline justify-between gap-4">
              <dt className="text-text-secondary">{s.label}</dt>
              <dd className="truncate font-medium text-text-primary">{s.value}</dd>
            </div>
          ))}
        </dl>
      </div>

      <label className="mt-4 flex cursor-pointer items-start gap-3 rounded-card border border-border bg-card p-3">
        <input
          type="checkbox"
          checked={data.lgpd_accepted}
          onChange={(e) => onAccept(e.target.checked)}
          className="mt-0.5 h-4 w-4 accent-accent-blue"
        />
        <span className="text-sm text-text-primary">
          Li e aceito o tratamento dos meus dados conforme descrito acima.
        </span>
      </label>

      <div className="mt-5 flex items-center justify-between gap-3">
        <SecondaryButton onClick={onBack}>
          <ArrowLeft size={16} /> Voltar
        </SecondaryButton>
        <PrimaryButton onClick={onSubmit} disabled={!data.lgpd_accepted} loading={submitting}>
          Concluir cadastro
        </PrimaryButton>
      </div>
    </StepShell>
  );
}

function StepDone() {
  return (
    <StepShell>
      <div className="text-center">
        <motion.div
          initial={{ scale: 0.5, opacity: 0 }}
          animate={{ scale: 1, opacity: 1 }}
          transition={{ type: 'spring', stiffness: 360, damping: 22 }}
          className="mx-auto mb-4 flex h-16 w-16 items-center justify-center rounded-pill bg-accent-green/10 text-accent-green"
        >
          <CheckCircle2 size={32} />
        </motion.div>
        <h2 className="text-2xl font-semibold text-text-primary">Cadastro enviado</h2>
        <p className="mt-2 text-sm text-text-secondary">
          Aguarde a aprovação. Assim que liberar, você recebe um WhatsApp e já pode entrar
          no plantão.
        </p>
      </div>
    </StepShell>
  );
}

function Field({
  label,
  error,
  children,
}: {
  label: string;
  error?: string;
  children: React.ReactNode;
}) {
  return (
    <label className="block">
      <span className="mb-1.5 block text-xs font-medium text-text-secondary">{label}</span>
      {children}
      {error && (
        <span className="mt-1 block text-xs font-medium text-accent-red" role="alert">
          {error}
        </span>
      )}
    </label>
  );
}

function InputStyles() {
  return (
    <style jsx>{`
      :global(.input-base) {
        width: 100%;
        border-radius: 14px;
        border: 1px solid rgb(var(--border));
        background: rgb(var(--surface));
        padding: 12px 14px;
        font-size: 16px;
        color: rgb(var(--text-primary));
        outline: none;
        transition: border-color 0.15s ease;
      }
      :global(.input-base:focus) {
        border-color: rgb(var(--accent-blue));
        box-shadow: 0 0 0 3px rgb(var(--accent-blue) / 0.25);
      }
    `}</style>
  );
}
