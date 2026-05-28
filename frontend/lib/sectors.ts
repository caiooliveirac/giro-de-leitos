import type { LucideIcon } from 'lucide-react';
import {
  Activity,
  AlertOctagon,
  Baby,
  Bone,
  FlaskConical,
  HeartPulse,
  Microscope,
  Pill,
  Scan,
  ScanLine,
  Scissors,
  Skull,
  Stethoscope,
  Syringe,
  UserRound,
  Users,
  Waves,
  Wind,
  Zap,
} from 'lucide-react';

export type SectorKey =
  | 'red_room'
  | 'yellow_female'
  | 'yellow_male'
  | 'yellow_unisex'
  | 'isolation_adult_m'
  | 'isolation_adult_f'
  | 'isolation_adult_unisex'
  | 'isolation_pediatric'
  | 'obituary'
  | 'pediatric_observation'
  | 'medication_room'
  | 'ward_internment'
  | 'ward_pediatric_internment'
  | 'surgeon'
  | 'orthopedist'
  | 'dentist'
  | 'pediatrician'
  | 'psychiatrist'
  | 'xray'
  | 'ecg'
  | 'lab'
  | 'ultrasound'
  | 'tomography';

export type SectorType = 'beds' | 'counter' | 'specialist' | 'exam';

export interface SectorMeta {
  key: SectorKey;
  label: string;
  type: SectorType;
  icon: LucideIcon;
  order: number;
}

export const SECTORS: Record<SectorKey, SectorMeta> = {
  red_room: { key: 'red_room', label: 'Sala vermelha', type: 'beds', icon: HeartPulse, order: 1 },
  yellow_female: {
    key: 'yellow_female',
    label: 'Sala amarela — Feminino',
    type: 'counter',
    icon: UserRound,
    order: 2,
  },
  yellow_male: {
    key: 'yellow_male',
    label: 'Sala amarela — Masculino',
    type: 'counter',
    icon: UserRound,
    order: 3,
  },
  yellow_unisex: {
    key: 'yellow_unisex',
    label: 'Sala amarela — Unissex',
    type: 'counter',
    icon: Users,
    order: 4,
  },
  isolation_adult_m: {
    key: 'isolation_adult_m',
    label: 'Isolamento adulto M',
    type: 'counter',
    icon: Wind,
    order: 5,
  },
  isolation_adult_f: {
    key: 'isolation_adult_f',
    label: 'Isolamento adulto F',
    type: 'counter',
    icon: Wind,
    order: 6,
  },
  isolation_adult_unisex: {
    key: 'isolation_adult_unisex',
    label: 'Isolamento adulto unissex',
    type: 'counter',
    icon: Wind,
    order: 7,
  },
  isolation_pediatric: {
    key: 'isolation_pediatric',
    label: 'Isolamento pediátrico',
    type: 'counter',
    icon: Baby,
    order: 8,
  },
  obituary: { key: 'obituary', label: 'Óbitário', type: 'counter', icon: Skull, order: 9 },
  pediatric_observation: {
    key: 'pediatric_observation',
    label: 'Observação pediátrica',
    type: 'counter',
    icon: Baby,
    order: 10,
  },
  medication_room: {
    key: 'medication_room',
    label: 'Sala de medicação / verde',
    type: 'counter',
    icon: Syringe,
    order: 11,
  },
  ward_internment: {
    key: 'ward_internment',
    label: 'Internamento',
    type: 'counter',
    icon: Users,
    order: 12,
  },
  ward_pediatric_internment: {
    key: 'ward_pediatric_internment',
    label: 'Internamento pediátrico',
    type: 'counter',
    icon: Baby,
    order: 13,
  },
  surgeon: { key: 'surgeon', label: 'Cirurgião', type: 'specialist', icon: Scissors, order: 14 },
  orthopedist: {
    key: 'orthopedist',
    label: 'Ortopedista',
    type: 'specialist',
    icon: Bone,
    order: 15,
  },
  dentist: { key: 'dentist', label: 'Dentista', type: 'specialist', icon: Pill, order: 16 },
  pediatrician: {
    key: 'pediatrician',
    label: 'Pediatra',
    type: 'specialist',
    icon: Stethoscope,
    order: 17,
  },
  psychiatrist: {
    key: 'psychiatrist',
    label: 'Psiquiatra',
    type: 'specialist',
    icon: Activity,
    order: 18,
  },
  xray: { key: 'xray', label: 'Raio-X', type: 'exam', icon: ScanLine, order: 19 },
  ecg: { key: 'ecg', label: 'ECG', type: 'exam', icon: HeartPulse, order: 20 },
  lab: { key: 'lab', label: 'Laboratório', type: 'exam', icon: FlaskConical, order: 21 },
  ultrasound: { key: 'ultrasound', label: 'Ultrassom', type: 'exam', icon: Waves, order: 22 },
  tomography: { key: 'tomography', label: 'Tomografia', type: 'exam', icon: Scan, order: 23 },
};

export const SECTOR_LIST: SectorMeta[] = Object.values(SECTORS).sort((a, b) => a.order - b.order);

export function sectorsByType(type: SectorType): SectorMeta[] {
  return SECTOR_LIST.filter((s) => s.type === type);
}

// Reserved icons (silence unused imports if tree-shaking strips them in dev).
export const _reserved = { AlertOctagon, Microscope, Syringe, Zap };
