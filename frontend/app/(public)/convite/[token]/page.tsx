'use client';

import { useEffect, useRef, useState, type ChangeEvent } from 'react';
import { AnimatePresence, motion } from 'framer-motion';
import { Bed, Camera, Check, Clock, Lock, ShieldCheck, X } from 'lucide-react';
import {
  apiFetch,
  ApiError,
  type InvitePreview,
  type InviteAcceptPayload,
} from '@/lib/api';
import { formatCpf, validateCpf, digitsOnly as cpfDigits } from '@/lib/cpf';
import { formatPhoneBR, isValidPhoneBR, digitsOnly as phoneDigits } from '@/lib/phone';
import { useToast } from '@/lib/toast';
import { ToastViewport } from '@/components/shared/ToastViewport';

const ROLES = ['Téc. enfermagem', 'Enfermeiro', 'Médico', 'Outro'];
const PROF_ROLES = new Set(['Enfermeiro', 'Médico']);

interface FormState {
  photo_data_url: string;
  photo_initials: string;
  name: string;
  cargo: string;
  coren_crm: string;
  phone: string;
  cpf: string;
  lgpd: boolean;
  password: string;
  password_confirm: string;
  pin: string;
}

const EMPTY: FormState = {
  photo_data_url: '',
  photo_initials: '',
  name: '',
  cargo: '',
  coren_crm: '',
  phone: '',
  cpf: '',
  lgpd: false,
  password: '',
  password_confirm: '',
  pin: '',
};

function calcStrength(pw: string): 0 | 1 | 2 | 3 | 4 {
  if (!pw) return 0;
  let s = 0;
  if (pw.length >= 8) s++;
  if (/[a-z]/.test(pw) && /[A-Z]/.test(pw)) s++;
  if (/\d/.test(pw)) s++;
  if (/[^a-zA-Z0-9]/.test(pw)) s++;
  return s as 0 | 1 | 2 | 3 | 4;
}
const STRENGTH_LABEL = ['', 'fraca', 'média', 'boa', 'forte'];

function initialsOf(name: string): string {
  const parts = name.trim().split(/\s+/).filter(Boolean);
  if (!parts.length) return '';
  return (parts[0]![0]! + (parts[1]?.[0] ?? '')).toUpperCase();
}

export default function InvitePage({ params }: { params: { token: string } }) {
  const toast = useToast();
  const [step, setStep] = useState<0 | 1 | 2 | 3 | 4>(0);
  const [preview, setPreview] = useState<InvitePreview | null>(null);
  const [previewError, setPreviewError] = useState<string | null>(null);
  const [loadingPreview, setLoadingPreview] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [data, setData] = useState<FormState>(EMPTY);

  const update = (patch: Partial<FormState>) =>
    setData((d) => ({ ...d, ...patch }));

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
      const payload: InviteAcceptPayload = {
        name: data.name,
        cpf: cpfDigits(data.cpf),
        phone: phoneDigits(data.phone),
        cargo: data.cargo,
        coren_crm: data.coren_crm.trim() || null,
        password: data.password,
        pin: data.pin,
        photo_url: data.photo_data_url,
        lgpd_accepted: data.lgpd,
      };
      await apiFetch(`/api/invites/${encodeURIComponent(params.token)}/accept`, {
        method: 'POST',
        body: JSON.stringify(payload),
      });
      setStep(4);
    } catch (err) {
      const msg = err instanceof ApiError ? err.message : 'Falha ao enviar cadastro';
      toast.error(msg);
    } finally {
      setSubmitting(false);
    }
  };

  // ── render ───────────────────────────────────────────────
  if (loadingPreview) {
    return (
      <InviteShell>
        <p className="mt-8 text-center text-sm text-ink-3">Carregando convite…</p>
      </InviteShell>
    );
  }

  if (previewError || !preview) {
    return <InviteInvalid message={previewError ?? 'Convite inválido'} />;
  }

  const stepNum = step >= 1 && step <= 3 ? (step as 1 | 2 | 3) : null;

  return (
    <InviteShell step={stepNum}>
      <AnimatePresence mode="wait">
        {step === 0 && (
          <Slide key="s0">
            <Welcome preview={preview} onStart={() => setStep(1)} />
          </Slide>
        )}
        {step === 1 && (
          <Slide key="s1">
            <Step1
              data={data}
              onChange={update}
              onNext={() => setStep(2)}
              onBack={() => setStep(0)}
            />
          </Slide>
        )}
        {step === 2 && (
          <Slide key="s2">
            <Step2
              data={data}
              onChange={update}
              onNext={() => setStep(3)}
              onBack={() => setStep(1)}
            />
          </Slide>
        )}
        {step === 3 && (
          <Slide key="s3">
            <Step3
              data={data}
              onChange={update}
              onNext={submit}
              onBack={() => setStep(2)}
              submitting={submitting}
            />
          </Slide>
        )}
        {step === 4 && (
          <Slide key="s4">
            <Success preview={preview} />
          </Slide>
        )}
      </AnimatePresence>
      <ToastViewport />
    </InviteShell>
  );
}

// ─── shell ────────────────────────────────────────────────
function InviteShell({
  step,
  children,
}: {
  step?: 1 | 2 | 3 | null;
  children: React.ReactNode;
}) {
  return (
    <div className="invite-app">
      <div className="invite-top">
        <div className="invite-brand">
          <span className="badge">
            <Bed size={14} aria-hidden />
          </span>
          <span>Giro de Leitos</span>
        </div>
        {step != null && (
          <div className="step-dots" aria-label={`Passo ${step} de 3`}>
            {[1, 2, 3].map((i) => (
              <span key={i} data-on={i <= step} />
            ))}
          </div>
        )}
      </div>
      <div className="invite-body">{children}</div>
    </div>
  );
}

function Slide({ children }: { children: React.ReactNode }) {
  return (
    <motion.div
      initial={{ opacity: 0, y: 12 }}
      animate={{ opacity: 1, y: 0 }}
      exit={{ opacity: 0, y: -12 }}
      transition={{ type: 'spring', stiffness: 320, damping: 32 }}
    >
      {children}
    </motion.div>
  );
}

// ─── A1 welcome ──────────────────────────────────────────
function Welcome({
  preview,
  onStart,
}: {
  preview: InvitePreview;
  onStart: () => void;
}) {
  const isCoord = preview.type === 'coordinator';
  const unit = preview.unit_name ?? 'sua unidade';
  const expires = new Date(preview.expires_at).toLocaleDateString('pt-BR', {
    day: '2-digit',
    month: 'long',
  });
  return (
    <>
      <h1 className="invite-h1">
        {isCoord ? (
          <>
            Você foi convidado pra
            <br />
            coordenar a {unit}
          </>
        ) : (
          <>
            Você foi convidado pra
            <br />
            fazer parte da equipe
            <br />
            da {unit}
          </>
        )}
      </h1>
      <p className="invite-sub">
        Convite de {preview.inviter_name} · válido até {expires}
      </p>

      <div className="invite-inviter">
        <div className="av">{initialsOf(preview.inviter_name) || '?'}</div>
        <div className="min-w-0 flex-1">
          <div className="name truncate">{preview.inviter_name}</div>
          <div className="meta">
            {isCoord ? 'Administrador central' : `Coordenador · ${unit}`}
          </div>
        </div>
      </div>

      <p className="text-[14px] leading-[1.5] text-ink-2">Você vai precisar de:</p>
      <ul className="mt-1 list-disc pl-5 text-[13px] leading-[1.7] text-ink-2">
        <li>Uma foto pra colegas te reconhecerem no plantão</li>
        <li>Seu CPF e telefone</li>
        <li>Cerca de 2 minutos</li>
      </ul>

      <button type="button" className="cta mt-6" onClick={onStart}>
        Começar cadastro
      </button>

      <p className="invite-fine">
        Ao continuar, você concorda com nossa{' '}
        <a href="#" tabIndex={-1}>política de privacidade</a>.<br />
        Seus dados são protegidos pela LGPD.
      </p>
    </>
  );
}

// ─── A2 step1: identity ──────────────────────────────────
function Step1({
  data,
  onChange,
  onNext,
  onBack,
}: {
  data: FormState;
  onChange: (patch: Partial<FormState>) => void;
  onNext: () => void;
  onBack: () => void;
}) {
  const fileRef = useRef<HTMLInputElement | null>(null);
  const canNext =
    data.name.trim().length >= 3 &&
    data.cargo &&
    (!PROF_ROLES.has(data.cargo) || data.coren_crm.trim().length >= 3);

  const handlePhoto = (e: ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    const reader = new FileReader();
    reader.onload = () => {
      if (typeof reader.result === 'string') {
        onChange({ photo_data_url: reader.result, photo_initials: '' });
      }
    };
    reader.readAsDataURL(file);
  };

  return (
    <>
      <button type="button" className="back-btn" onClick={onBack} aria-label="Voltar">
        ← voltar
      </button>
      <h1 className="invite-h1 mt-2">Quem é você?</h1>
      <p className="invite-sub">Passo 1 de 3 · vamos te identificar pra equipe.</p>

      <div className="avatar-pick">
        <button
          type="button"
          className="circle"
          data-filled={Boolean(data.photo_data_url || data.photo_initials)}
          onClick={() => fileRef.current?.click()}
          aria-label="Adicionar foto"
        >
          {data.photo_data_url ? (
            // eslint-disable-next-line @next/next/no-img-element
            <img src={data.photo_data_url} alt="" />
          ) : data.photo_initials ? (
            <span className="text-[28px] font-semibold">{data.photo_initials}</span>
          ) : (
            <Camera size={28} aria-hidden />
          )}
        </button>
        <div className="help">
          Foto do rosto, ajuda colegas a te reconhecerem no plantão.
          <br />
          <button
            type="button"
            onClick={() => fileRef.current?.click()}
            className="mt-1.5 text-[13px] font-semibold text-accent"
          >
            {data.photo_data_url ? 'Trocar foto' : 'Adicionar foto'}
          </button>
          {!data.photo_data_url && data.name && (
            <>
              {' · '}
              <button
                type="button"
                onClick={() =>
                  onChange({ photo_initials: initialsOf(data.name) })
                }
                className="text-[13px] text-ink-3 underline"
              >
                usar iniciais
              </button>
            </>
          )}
        </div>
        <input
          ref={fileRef}
          type="file"
          accept="image/*"
          capture="user"
          className="hidden"
          onChange={handlePhoto}
          aria-label="Selecionar foto"
        />
      </div>

      <div className="field-stack mt-5">
        <div className="field">
          <label className="field-label" htmlFor="iv-name">
            Nome completo
          </label>
          <input
            id="iv-name"
            className="input-shell"
            value={data.name}
            placeholder="Ex.: Mariana Soares"
            autoComplete="name"
            onChange={(e) => onChange({ name: e.target.value })}
          />
        </div>

        <div className="field">
          <span className="field-label">Cargo</span>
          <div className="chip-pick" role="radiogroup" aria-label="Cargo">
            {ROLES.map((r) => (
              <button
                key={r}
                type="button"
                role="radio"
                aria-checked={data.cargo === r}
                data-on={data.cargo === r}
                onClick={() => onChange({ cargo: r })}
              >
                {r}
              </button>
            ))}
          </div>
        </div>

        {PROF_ROLES.has(data.cargo) && (
          <div className="field">
            <label className="field-label" htmlFor="iv-coren">
              COREN ou CRM{' '}
              <span className="font-medium normal-case tracking-normal text-ink-3">
                · se aplicável
              </span>
            </label>
            <input
              id="iv-coren"
              className="input-shell"
              value={data.coren_crm}
              placeholder="Ex.: COREN/BA 412.388"
              onChange={(e) => onChange({ coren_crm: e.target.value })}
            />
          </div>
        )}
      </div>

      <button type="button" className="cta mt-6" disabled={!canNext} onClick={onNext}>
        Continuar
      </button>
    </>
  );
}

// ─── A3 step2: contact ───────────────────────────────────
function Step2({
  data,
  onChange,
  onNext,
  onBack,
}: {
  data: FormState;
  onChange: (patch: Partial<FormState>) => void;
  onNext: () => void;
  onBack: () => void;
}) {
  const [cpfTouched, setCpfTouched] = useState(false);
  const phoneOk = isValidPhoneBR(data.phone);
  const cpfOk = validateCpf(data.cpf);
  const cpfShowErr = cpfTouched && cpfDigits(data.cpf).length === 11 && !cpfOk;
  const canNext = phoneOk && cpfOk && data.lgpd;

  return (
    <>
      <button type="button" className="back-btn" onClick={onBack} aria-label="Voltar">
        ← voltar
      </button>
      <h1 className="invite-h1 mt-2">Como te encontramos?</h1>
      <p className="invite-sub">
        Passo 2 de 3 · vamos avisar você no WhatsApp quando seu cadastro for aprovado.
      </p>

      <div className="field-stack">
        <div className="field">
          <label className="field-label" htmlFor="iv-tel">
            Telefone celular
          </label>
          <input
            id="iv-tel"
            className="input-shell"
            value={data.phone}
            placeholder="(71) 99488-3120"
            inputMode="tel"
            autoComplete="tel"
            onChange={(e) => onChange({ phone: formatPhoneBR(e.target.value) })}
          />
          <div className="help">
            Vamos usar pra te avisar quando seu cadastro for aprovado.
          </div>
        </div>

        <div className="field">
          <label className="field-label" htmlFor="iv-cpf">
            CPF
          </label>
          <input
            id="iv-cpf"
            className="input-shell tnum"
            value={data.cpf}
            placeholder="000.000.000-00"
            inputMode="numeric"
            data-err={cpfShowErr}
            onBlur={() => setCpfTouched(true)}
            onChange={(e) => onChange({ cpf: formatCpf(e.target.value) })}
          />
          {cpfShowErr ? (
            <div className="help text-critical-ink">
              CPF inválido — verifique os dígitos.
            </div>
          ) : (
            <div className="secure-hint">
              <Lock size={14} aria-hidden className="mt-[2px] flex-shrink-0" />
              <span>
                Seu CPF é criptografado e usado apenas pra confirmar sua identidade. Não
                compartilhamos com terceiros.
              </span>
            </div>
          )}
        </div>

        <button
          type="button"
          className="lgpd"
          data-on={data.lgpd}
          onClick={() => onChange({ lgpd: !data.lgpd })}
          aria-pressed={data.lgpd}
        >
          <span className="box">
            <Check size={14} aria-hidden />
          </span>
          <span>
            Autorizo o tratamento dos meus dados conforme a{' '}
            <a href="#" onClick={(e) => e.stopPropagation()}>
              política de privacidade
            </a>{' '}
            e LGPD.
          </span>
        </button>
      </div>

      <button type="button" className="cta mt-6" disabled={!canNext} onClick={onNext}>
        Continuar
      </button>
    </>
  );
}

// ─── A4 step3: password + PIN ────────────────────────────
function Step3({
  data,
  onChange,
  onNext,
  onBack,
  submitting,
}: {
  data: FormState;
  onChange: (patch: Partial<FormState>) => void;
  onNext: () => void;
  onBack: () => void;
  submitting: boolean;
}) {
  const strength = calcStrength(data.password);
  const match = data.password.length > 0 && data.password === data.password_confirm;
  const minOk =
    data.password.length >= 8 &&
    /[a-zA-Z]/.test(data.password) &&
    /\d/.test(data.password);
  const pinOk = /^\d{4}$/.test(data.pin);
  const canNext = minOk && match && pinOk && !submitting;

  return (
    <>
      <button type="button" className="back-btn" onClick={onBack} aria-label="Voltar">
        ← voltar
      </button>
      <h1 className="invite-h1 mt-2">Crie uma senha</h1>
      <p className="invite-sub">
        Passo 3 de 3 · você usa essa senha pra abrir o app no celular.
      </p>

      <div className="field-stack">
        <div className="field">
          <label className="field-label" htmlFor="iv-pw">
            Senha
          </label>
          <input
            id="iv-pw"
            type="password"
            className="input-shell"
            value={data.password}
            autoComplete="new-password"
            placeholder="8+ caracteres, com letra e número"
            onChange={(e) => onChange({ password: e.target.value })}
          />
          {data.password.length > 0 && (
            <>
              <div className="pw-meter" data-strength={strength}>
                {[1, 2, 3, 4].map((i) => (
                  <span key={i} />
                ))}
              </div>
              <div className="pw-meter-label">{STRENGTH_LABEL[strength]}</div>
            </>
          )}
        </div>

        <div className="field">
          <label className="field-label" htmlFor="iv-pw2">
            Repita a senha
          </label>
          <input
            id="iv-pw2"
            type="password"
            className="input-shell"
            value={data.password_confirm}
            autoComplete="new-password"
            placeholder="mesma senha"
            data-err={data.password_confirm.length > 0 && !match}
            onChange={(e) => onChange({ password_confirm: e.target.value })}
          />
          {data.password_confirm.length > 0 && !match && (
            <div className="help text-critical-ink">As senhas não batem.</div>
          )}
        </div>

        <div className="field">
          <label className="field-label" htmlFor="iv-pin">
            PIN de plantão · 4 dígitos
          </label>
          <input
            id="iv-pin"
            type="password"
            inputMode="numeric"
            maxLength={4}
            className="input-shell tnum tracking-[0.5em]"
            value={data.pin}
            placeholder="••••"
            onChange={(e) =>
              onChange({ pin: e.target.value.replace(/\D/g, '').slice(0, 4) })
            }
          />
          <div className="help">
            Esse PIN é o que você usa pra entrar em cada plantão pelo tablet.
          </div>
        </div>
      </div>

      <button type="button" className="cta mt-6" disabled={!canNext} onClick={onNext}>
        {submitting ? 'Enviando…' : 'Finalizar cadastro'}
      </button>

      <p className="invite-fine">
        Ao finalizar, seus dados vão pra aprovação do coordenador.
      </p>
    </>
  );
}

// ─── A5 success ───────────────────────────────────────────
function Success({ preview }: { preview: InvitePreview }) {
  const isCoord = preview.type === 'coordinator';
  return (
    <>
      <div className="wait-art" aria-hidden>
        <Clock size={44} />
      </div>
      <h1 className="invite-h1 text-center" style={{ margin: '6px 0 8px' }}>
        Cadastro enviado!
      </h1>
      <p className="invite-sub mx-auto max-w-[320px] text-center">
        {isCoord
          ? 'O administrador central foi notificado e vai aprovar em instantes. Você recebe uma mensagem no WhatsApp quando estiver liberado.'
          : 'Seu coordenador foi notificado e vai aprovar em instantes. Você recebe uma mensagem no WhatsApp quando estiver liberado pra usar o app.'}
      </p>

      <div className="invite-inviter">
        <div className="av">
          <ShieldCheck size={18} aria-hidden />
        </div>
        <div className="min-w-0 flex-1">
          <div className="name truncate">{preview.inviter_name}</div>
          <div className="meta">
            notificado · {isCoord ? 'admin central' : 'coordenador'}
          </div>
        </div>
      </div>

      <p className="invite-fine text-center">Pode fechar esta página.</p>
    </>
  );
}

// ─── invalid / expired ────────────────────────────────────
function InviteInvalid({ message }: { message: string }) {
  return (
    <InviteShell>
      <div className="err-art" aria-hidden>
        <X size={44} />
      </div>
      <h1 className="invite-h1 text-center" style={{ margin: '6px 0 8px' }}>
        Convite não está mais válido
      </h1>
      <p className="invite-sub mx-auto max-w-[320px] text-center">
        {message}. Peça um novo convite ao seu coordenador.
      </p>
      <p className="invite-fine text-center">
        Convites valem 7 dias e funcionam uma vez só, pra sua segurança.
      </p>
    </InviteShell>
  );
}
