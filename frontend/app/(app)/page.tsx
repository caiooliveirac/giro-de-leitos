'use client';

import { useMemo } from 'react';
import { LayoutGroup } from 'framer-motion';
import type { Bed, ExamStatus, SpecialistStatus } from '@/lib/api';
import { SECTORS } from '@/lib/sectors';
import { useToast } from '@/lib/toast';
import { TopBar } from '@/components/shared/TopBar';
import { OfflineBanner } from '@/components/shared/OfflineBanner';
import { ToastViewport } from '@/components/shared/ToastViewport';
import { RedRoomBed } from '@/components/beds/RedRoomBed';
import { CounterSector } from '@/components/beds/CounterSector';
import { SpecialistCard } from '@/components/beds/SpecialistCard';
import { ExamCard } from '@/components/beds/ExamCard';

// ---------------------------------------------------------------------------
// TODO Fase 7: substituir mocks por dados reais via useUnitState(unitId) +
// mutações com If-Match/version. Por enquanto a tela é totalmente client-side
// pra validação visual da estética iOS Health.
// ---------------------------------------------------------------------------

interface CounterMock {
  key: string;
  label: string;
  occupancy: number;
  capacity: number;
  version: number;
}

interface SpecialistMock {
  key: string;
  label: string;
  status: SpecialistStatus;
}

interface ExamMock {
  key: string;
  label: string;
  status: ExamStatus;
  unavailable_reason: string | null;
}

const NOW = () => new Date().toISOString();

const MOCK_BEDS: Array<Bed | null> = [
  {
    id: 1,
    unit_id: 'mock',
    bed_number: 1,
    patient_sigla: 'JCO',
    clinical_summary: 'Dor torácica · suspeita IAM',
    occupied_since: new Date(Date.now() - 1000 * 60 * 95).toISOString(),
    last_updated_by: null,
    last_updated_at: NOW(),
    version: 1,
  },
  null,
  {
    id: 3,
    unit_id: 'mock',
    bed_number: 3,
    patient_sigla: 'MSL',
    clinical_summary: 'AVCi em janela · TC solicitada',
    occupied_since: new Date(Date.now() - 1000 * 60 * 25).toISOString(),
    last_updated_by: null,
    last_updated_at: NOW(),
    version: 1,
  },
  null,
  {
    id: 5,
    unit_id: 'mock',
    bed_number: 5,
    patient_sigla: 'ABF',
    clinical_summary: 'Sepse · ATB em curso',
    occupied_since: new Date(Date.now() - 1000 * 60 * 60 * 4).toISOString(),
    last_updated_by: null,
    last_updated_at: NOW(),
    version: 1,
  },
  null,
];

const MOCK_COUNTERS_YELLOW: CounterMock[] = [
  { key: 'yellow_female', label: 'Feminino', occupancy: 4, capacity: 6, version: 1 },
  { key: 'yellow_male', label: 'Masculino', occupancy: 5, capacity: 5, version: 1 },
  { key: 'yellow_unisex', label: 'Unissex', occupancy: 3, capacity: 4, version: 1 },
];

const MOCK_COUNTERS_ISOLATION: CounterMock[] = [
  { key: 'isolation_adult_m', label: 'Adulto M', occupancy: 1, capacity: 2, version: 1 },
  { key: 'isolation_adult_f', label: 'Adulto F', occupancy: 2, capacity: 2, version: 1 },
  { key: 'isolation_adult_unisex', label: 'Adulto unissex', occupancy: 0, capacity: 1, version: 1 },
  { key: 'isolation_pediatric', label: 'Pediátrico', occupancy: 1, capacity: 2, version: 1 },
];

const MOCK_SPECIALISTS: SpecialistMock[] = [
  { key: 'surgeon', label: 'Cirurgião', status: 'available' },
  { key: 'orthopedist', label: 'Ortopedista', status: 'on_call' },
  { key: 'dentist', label: 'Dentista', status: 'unavailable' },
  { key: 'pediatrician', label: 'Pediatra', status: 'available' },
];

const MOCK_EXAMS: ExamMock[] = [
  { key: 'xray', label: 'Raio-X', status: 'working', unavailable_reason: null },
  { key: 'ecg', label: 'ECG', status: 'working', unavailable_reason: null },
  { key: 'lab', label: 'Laboratório', status: 'unavailable', unavailable_reason: 'Sem reagente de troponina' },
  { key: 'ultrasound', label: 'Ultrassom', status: 'working', unavailable_reason: null },
  { key: 'tomography', label: 'Tomografia', status: 'unavailable', unavailable_reason: 'Em manutenção' },
];

export default function OperatorHomePage() {
  const toast = useToast();

  const beds = useMemo(() => MOCK_BEDS, []);

  return (
    <>
      <OfflineBanner />
      <TopBar unitName="UPA Centro" shiftLabel="Enf. Ana Lima" />

      <main className="mx-auto w-full max-w-[520px] px-4 pb-24 pt-4">
        {/* Sala vermelha — leitos individuais */}
        <Section title="Sala vermelha" subtitle="Leitos críticos com paciente identificado">
          <LayoutGroup>
            <div className="space-y-3">
              {beds.map((bed, idx) => {
                const bedNumber = idx + 1;
                return (
                  <RedRoomBed
                    key={bedNumber}
                    bed={bed}
                    bedNumber={bedNumber}
                    onSave={(data) => {
                      // TODO Fase 7: PUT /api/unit/{id}/beds/{n} com If-Match
                      // eslint-disable-next-line no-console
                      console.log('save bed', bedNumber, data);
                      toast.success(`Leito ${bedNumber} salvo`);
                    }}
                    onDischarge={() => {
                      // eslint-disable-next-line no-console
                      console.log('discharge', bedNumber);
                      toast.success(`Alta no leito ${bedNumber}`);
                    }}
                    onDeath={(pin) => {
                      // eslint-disable-next-line no-console
                      console.log('death', bedNumber, 'pin len', pin.length);
                      toast.show(`Óbito registrado no leito ${bedNumber}`, 'warning');
                    }}
                    onTransfer={() => {
                      // eslint-disable-next-line no-console
                      console.log('transfer', bedNumber);
                      toast.show(`Transferência no leito ${bedNumber}`, 'warning');
                    }}
                    onClear={() => {
                      // eslint-disable-next-line no-console
                      console.log('clear', bedNumber);
                      toast.show(`Leito ${bedNumber} esvaziado`);
                    }}
                  />
                );
              })}
            </div>
          </LayoutGroup>
        </Section>

        {/* Sala amarela — counters */}
        <Section title="Sala amarela" subtitle="Ocupação por gênero">
          <div className="grid grid-cols-1 gap-3">
            {MOCK_COUNTERS_YELLOW.map((c) => (
              <CounterSector
                key={c.key}
                sector={{ ...c, icon: SECTORS[c.key as keyof typeof SECTORS]?.icon }}
                onSave={(next) => {
                  // eslint-disable-next-line no-console
                  console.log('save counter', c.key, next);
                  toast.success(`${c.label} atualizado`);
                }}
              />
            ))}
          </div>
        </Section>

        {/* Isolamento — counters */}
        <Section title="Isolamento" subtitle="Quartos com precaução">
          <div className="grid grid-cols-1 gap-3">
            {MOCK_COUNTERS_ISOLATION.map((c) => (
              <CounterSector
                key={c.key}
                sector={{ ...c, icon: SECTORS[c.key as keyof typeof SECTORS]?.icon }}
                onSave={(next) => {
                  // eslint-disable-next-line no-console
                  console.log('save counter', c.key, next);
                  toast.success(`${c.label} atualizado`);
                }}
              />
            ))}
          </div>
        </Section>

        {/* Especialistas */}
        <Section title="Especialistas" subtitle="Toque pra ciclar · pressione pra escolher">
          <div className="grid grid-cols-2 gap-3">
            {MOCK_SPECIALISTS.map((s) => (
              <SpecialistCard
                key={s.key}
                sectorKey={s.key}
                label={s.label}
                status={s.status}
                icon={SECTORS[s.key as keyof typeof SECTORS]?.icon}
                onChange={(next) => {
                  // eslint-disable-next-line no-console
                  console.log('specialist', s.key, next);
                  toast.success(`${s.label}: ${next}`);
                }}
              />
            ))}
          </div>
        </Section>

        {/* Exames */}
        <Section title="Exames" subtitle="Disponibilidade de equipamentos">
          <div className="space-y-2.5">
            {MOCK_EXAMS.map((e) => (
              <ExamCard
                key={e.key}
                sectorKey={e.key}
                label={e.label}
                status={e.status}
                unavailable_reason={e.unavailable_reason}
                icon={SECTORS[e.key as keyof typeof SECTORS]?.icon}
                onChange={(next) => {
                  // eslint-disable-next-line no-console
                  console.log('exam', e.key, next);
                  toast.success(`${e.label} atualizado`);
                }}
              />
            ))}
          </div>
        </Section>
      </main>

      <ToastViewport />
    </>
  );
}

function Section({
  title,
  subtitle,
  children,
}: {
  title: string;
  subtitle?: string;
  children: React.ReactNode;
}) {
  return (
    <section className="mt-6 first:mt-2">
      <div className="mb-2 flex items-baseline justify-between gap-3 px-1 pt-3">
        <h2 className="truncate text-[11px] font-semibold uppercase tracking-[0.14em] text-ink-3">
          {title}
        </h2>
        {subtitle && (
          <p className="shrink-0 text-[13px] tabular-nums text-ink-2">{subtitle}</p>
        )}
      </div>
      {children}
    </section>
  );
}
