'use client';

import { useCallback, useEffect, useState } from 'react';
import { AnimatePresence, LayoutGroup, motion } from 'framer-motion';
import { ArrowRightLeft, Check, Eraser, X } from 'lucide-react';
import type { Bed } from '@/lib/api';
import { formatRelative } from '@/lib/time';
import { PinPad } from '@/components/auth/PinPad';
import { useLongPress } from '@/hooks/useLongPress';

interface RedRoomBedProps {
  bed: Bed | null;
  bedNumber: number;
  onSave: (data: { patient_sigla: string; clinical_summary: string }) => void | Promise<void>;
  onDischarge: () => void | Promise<void>;
  onDeath: (pin: string) => void | Promise<void>;
  onTransfer: () => void | Promise<void>;
  onClear: () => void | Promise<void>;
  /** Modo somente-leitura: dado ao vivo do WhatsApp, não editável até "assumir". */
  live?: boolean;
  /** Leito acima da capacidade configurada (over-capacity). */
  isExtra?: boolean;
}

// Tags fiéis ao design/src/data.jsx TAG_OPTIONS.
const QUICK_CHIPS = [
  'IAM',
  'AVC',
  'sepse',
  'TCE',
  'PCR revertida',
  'IRpA',
  'choque',
  'crise convulsiva',
  'politrauma',
];

export function RedRoomBed({
  bed,
  bedNumber,
  onSave,
  onDischarge,
  onDeath,
  onTransfer,
  onClear,
  live = false,
  isExtra = false,
}: RedRoomBedProps) {
  const isOccupied = Boolean(bed && bed.patient_sigla);
  const [expanded, setExpanded] = useState(false);
  const [sigla, setSigla] = useState(bed?.patient_sigla ?? '');
  const [summary, setSummary] = useState(bed?.clinical_summary ?? '');
  const [tags, setTags] = useState<string[]>([]);
  const [pinOpen, setPinOpen] = useState(false);
  const [pinError, setPinError] = useState<string | null>(null);
  const [hintObito, setHintObito] = useState(false);

  useEffect(() => {
    if (!expanded) {
      setSigla(bed?.patient_sigla ?? '');
      setSummary(bed?.clinical_summary ?? '');
      setPinOpen(false);
      setPinError(null);
      setHintObito(false);
    }
  }, [expanded, bed]);

  const toggle = useCallback(() => setExpanded((v) => !v), []);

  const collapse = useCallback(() => {
    setExpanded(false);
    setPinOpen(false);
  }, []);

  const toggleChip = (chip: string) => {
    setTags((cur) => (cur.includes(chip) ? cur.filter((c) => c !== chip) : [...cur, chip]));
    // também acrescenta ao summary se ainda não estiver lá
    setSummary((cur) => {
      if (!cur) return chip;
      if (cur.toLowerCase().includes(chip.toLowerCase())) return cur;
      return `${cur} · ${chip}`;
    });
  };

  const handleSave = async () => {
    await onSave({
      patient_sigla: sigla.trim().toUpperCase(),
      clinical_summary: summary.trim(),
    });
    collapse();
  };

  // long-press com ring (500ms — fiel ao design)
  const lp = useLongPress(
    () => setPinOpen(true),
    () => {
      setHintObito(true);
      setTimeout(() => setHintObito(false), 1600);
    },
    500,
  );

  const handlePinSubmit = async (pin: string) => {
    setPinError(null);
    try {
      await onDeath(pin);
      collapse();
    } catch {
      setPinError('PIN incorreto · tente de novo');
    }
  };

  // shells por estado (critical vs empty dashed)
  const shellClass = isOccupied
    ? 'border-[color-mix(in_oklch,var(--critical)_35%,var(--line))]'
    : 'border-dashed border-line-strong bg-transparent';

  return (
    <LayoutGroup>
      <motion.div
        layout
        transition={{ type: 'spring', stiffness: 340, damping: 32 }}
        className={`overflow-hidden rounded-card border bg-surface-elev shadow-card ${shellClass} ${
          expanded ? 'shadow-pop' : ''
        }`}
        data-expanded={expanded}
      >
        <motion.button
          layout
          type="button"
          onClick={live ? undefined : toggle}
          aria-expanded={live ? undefined : expanded}
          aria-label={`Leito ${bedNumber}${isExtra ? ' (extra)' : ''} ${isOccupied ? 'ocupado' : 'vago'}`}
          whileTap={live ? undefined : { scale: 0.97 }}
          className={`flex w-full items-center gap-3.5 px-4 py-4 text-left focus-visible:outline-none ${
            live ? 'cursor-default' : ''
          }`}
        >
          <div
            className={`flex h-[46px] w-[46px] shrink-0 items-center justify-center rounded-[14px] text-[19px] font-semibold tabular-nums ${
              isOccupied ? 'bg-critical-soft text-critical-ink' : 'bg-surface-2 text-ink'
            }`}
          >
            {bedNumber}
          </div>

          <div className="min-w-0 flex-1">
            {isOccupied ? (
              <>
                <div className="flex items-center gap-2">
                  <span className="text-[17px] font-semibold tracking-wider tabular-nums text-ink">
                    {bed?.patient_sigla}
                  </span>
                  {isExtra && (
                    <span className="rounded-full bg-critical-soft px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-critical-ink">
                      extra
                    </span>
                  )}
                </div>
                <div className="mt-0.5 truncate text-[13px] text-ink-2">
                  {bed?.clinical_summary || '—'}
                </div>
              </>
            ) : (
              <>
                <div className="text-[17px] font-medium text-ink-3">Leito vago</div>
                <div className="mt-0.5 text-[13px] text-ink-3">
                  {live ? '—' : 'Toque para adicionar paciente'}
                </div>
              </>
            )}
          </div>

          {isOccupied && bed?.occupied_since && (
            <div className="shrink-0 text-xs tabular-nums text-ink-3">
              {formatRelative(bed.occupied_since)}
            </div>
          )}
          {!isOccupied && !live && (
            <div className="shrink-0 text-base font-semibold text-[var(--accent)]">+</div>
          )}
        </motion.button>

        <AnimatePresence initial={false}>
          {!live && expanded && (
            <motion.div
              key="body"
              layout
              initial={{ opacity: 0, height: 0 }}
              animate={{ opacity: 1, height: 'auto' }}
              exit={{ opacity: 0, height: 0 }}
              transition={{ type: 'spring', stiffness: 280, damping: 32 }}
              className="overflow-hidden"
            >
              <div className="border-t border-line px-4 pb-4 pt-1">
                {/* Sigla */}
                <div className="mt-3.5">
                  <label className="mb-2 block text-[11px] font-semibold uppercase tracking-[0.12em] text-ink-3">
                    Sigla do paciente
                  </label>
                  <input
                    type="text"
                    value={sigla}
                    onChange={(e) =>
                      setSigla(e.target.value.toUpperCase().slice(0, 8))
                    }
                    maxLength={8}
                    autoCapitalize="characters"
                    placeholder="J.S.M."
                    aria-label="Sigla do paciente"
                    className="w-full rounded-[12px] border border-line bg-surface px-3.5 py-3 text-base font-semibold uppercase tracking-wider tabular-nums text-ink placeholder:text-ink-3 focus:border-[var(--accent)] focus:bg-surface-elev focus:outline-none"
                  />
                </div>

                {/* Quadro clínico */}
                <div className="mt-3.5">
                  <label className="mb-2 block text-[11px] font-semibold uppercase tracking-[0.12em] text-ink-3">
                    Quadro clínico
                  </label>
                  <textarea
                    value={summary}
                    onChange={(e) => setSummary(e.target.value)}
                    rows={2}
                    placeholder='curto · ex: "IAM, hipotenso, pós-trombólise"'
                    aria-label="Quadro clínico"
                    className="min-h-[64px] w-full resize-none rounded-[12px] border border-line bg-surface px-3.5 py-3 text-[15px] leading-snug text-ink placeholder:text-ink-3 focus:border-[var(--accent)] focus:bg-surface-elev focus:outline-none"
                  />

                  <div className="mt-2.5 flex flex-wrap gap-1.5">
                    {QUICK_CHIPS.map((chip) => {
                      const on = tags.includes(chip);
                      return (
                        <motion.button
                          key={chip}
                          type="button"
                          whileTap={{ scale: 0.94 }}
                          onClick={() => toggleChip(chip)}
                          className={`min-h-[36px] rounded-full border px-3 py-2 text-[13px] font-medium transition-colors ${
                            on
                              ? 'border-critical bg-critical text-[var(--ink-on-color)]'
                              : 'border-line bg-surface text-ink-2'
                          }`}
                        >
                          {chip}
                        </motion.button>
                      );
                    })}
                  </div>
                </div>

                {/* Save / Cancel */}
                <div className="mt-4 flex gap-2">
                  <motion.button
                    type="button"
                    whileTap={{ scale: 0.96 }}
                    onClick={handleSave}
                    className="flex-1 rounded-2xl bg-ink px-4 py-3 text-sm font-semibold text-[var(--bg)]"
                  >
                    Salvar
                  </motion.button>
                  <motion.button
                    type="button"
                    whileTap={{ scale: 0.96 }}
                    onClick={collapse}
                    className="rounded-2xl border border-line bg-surface px-4 py-3 text-sm font-medium text-ink-2"
                  >
                    Cancelar
                  </motion.button>
                </div>

                {/* Ações em grid 2×2 */}
                <div className="mt-4 grid grid-cols-2 gap-2">
                  <ActionButton
                    kind="alta"
                    disabled={!isOccupied}
                    onClick={() => void onDischarge()}
                  >
                    <Check size={18} />
                    <span>Alta</span>
                  </ActionButton>

                  <ObitoButton
                    disabled={!isOccupied}
                    lp={lp.lp}
                    active={lp.active}
                    hint={hintObito && !lp.active}
                    bind={lp.bind}
                  />

                  <ActionButton
                    kind="transf"
                    disabled={!isOccupied}
                    onClick={() => void onTransfer()}
                  >
                    <ArrowRightLeft size={16} />
                    <span>Transferir</span>
                  </ActionButton>

                  <ActionButton kind="vazio" onClick={() => void onClear()}>
                    <Eraser size={16} />
                    <span>Esvaziar leito</span>
                  </ActionButton>
                </div>

                {pinOpen && (
                  <div className="mt-4 rounded-2xl border border-line bg-surface p-4">
                    <p className="text-center text-sm font-semibold text-ink">
                      Identifique-se pra confirmar óbito
                    </p>
                    <p className="mt-1 text-center text-xs text-ink-2">
                      Leito {bedNumber} · {bed?.patient_sigla} · este registro fica auditado.
                    </p>
                    <PinPad
                      length={4}
                      compact
                      onSubmit={handlePinSubmit}
                      onCancel={() => setPinOpen(false)}
                      error={pinError}
                    />
                  </div>
                )}
              </div>
            </motion.div>
          )}
        </AnimatePresence>
      </motion.div>
    </LayoutGroup>
  );
}

// ─────────────────────────────────────────────
// Action buttons (alta / transferir / esvaziar)
// ─────────────────────────────────────────────
type ActionKind = 'alta' | 'transf' | 'vazio';

function actionStyles(kind: ActionKind) {
  switch (kind) {
    case 'alta':
      return 'bg-success-soft text-success-ink';
    case 'transf':
      return 'bg-warning-soft text-warning-ink';
    case 'vazio':
      return 'bg-surface-2 text-ink-2';
  }
}

function ActionButton({
  kind,
  disabled,
  onClick,
  children,
}: {
  kind: ActionKind;
  disabled?: boolean;
  onClick?: () => void;
  children: React.ReactNode;
}) {
  return (
    <motion.button
      type="button"
      whileTap={{ scale: 0.96 }}
      onClick={onClick}
      disabled={disabled}
      className={`relative flex min-h-[52px] items-center justify-center gap-2 overflow-hidden rounded-2xl px-3 py-3.5 text-[15px] font-semibold ${actionStyles(
        kind,
      )} ${disabled ? 'opacity-40' : ''}`}
    >
      {children}
    </motion.button>
  );
}

// Óbito: long-press com ring de progresso visível.
function ObitoButton({
  disabled,
  lp,
  active,
  hint,
  bind,
}: {
  disabled?: boolean;
  lp: number;
  active: boolean;
  hint: boolean;
  bind: {
    onPointerDown: (e: React.PointerEvent) => void;
    onPointerUp: () => void;
    onPointerLeave: () => void;
    onPointerCancel: () => void;
  };
}) {
  return (
    <button
      type="button"
      disabled={disabled}
      aria-label="Óbito — pressione e segure"
      {...bind}
      className={`relative flex min-h-[52px] select-none items-center justify-center gap-2 overflow-hidden rounded-2xl bg-obit-soft px-3 py-3.5 text-[15px] font-semibold text-obit ${
        disabled ? 'opacity-40' : ''
      } ${active ? 'scale-[0.97]' : ''}`}
      style={{
        // ring progress via pseudo using CSS var
        ['--lp' as string]: lp,
      }}
    >
      {/* progress fill */}
      <span
        aria-hidden
        className="pointer-events-none absolute inset-0 origin-left bg-[var(--obit)] transition-transform"
        style={{
          transform: `scaleX(${lp})`,
          opacity: active ? 0.28 : 0,
          transition: 'transform 30ms linear, opacity 200ms ease',
        }}
      />
      <span className="relative inline-flex items-center gap-2">
        <X size={18} />
        Óbito
      </span>
      {/* hint tooltip */}
      <span
        aria-hidden
        className={`pointer-events-none absolute -top-2 left-1/2 -translate-x-1/2 -translate-y-full whitespace-nowrap rounded-md bg-ink px-2.5 py-1.5 text-xs text-[var(--bg)] transition-opacity ${
          hint ? 'opacity-100' : 'opacity-0'
        }`}
      >
        segure para confirmar
      </span>
    </button>
  );
}
