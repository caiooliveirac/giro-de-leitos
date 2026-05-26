// Lightweight fetch wrapper. Real auth/refresh logic comes in later phases.

export class ApiError extends Error {
  status: number;
  body: unknown;
  constructor(message: string, status: number, body: unknown) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.body = body;
  }
}

export async function apiFetch<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  if (!headers.has('Content-Type') && init.body && !(init.body instanceof FormData)) {
    headers.set('Content-Type', 'application/json');
  }
  headers.set('Accept', 'application/json');

  const res = await fetch(path, {
    ...init,
    headers,
    credentials: 'include',
  });

  const contentType = res.headers.get('content-type') ?? '';
  const isJson = contentType.includes('application/json');
  const body = isJson ? await res.json().catch(() => null) : await res.text().catch(() => null);

  if (!res.ok) {
    const message =
      (isJson && body && typeof body === 'object' && 'detail' in body && String((body as { detail: unknown }).detail)) ||
      res.statusText ||
      'Request failed';
    throw new ApiError(message, res.status, body);
  }

  return body as T;
}

// ---------------------------------------------------------------------------
// Domain types (placeholders aligned with migrations/001_auth_and_beds.sql).
// Will be refined in Phase 5/7 as the API contract is finalized.
// ---------------------------------------------------------------------------

export type UserRole = 'admin' | 'coordinator' | 'professional';
export type UserStatus = 'pending' | 'active' | 'suspended';

export interface User {
  id: string;
  name: string;
  role: UserRole;
  status: UserStatus;
  cargo: string | null;
  coren_crm: string | null;
  phone: string | null;
  photo_url: string | null;
  unit_id: string | null;
  email: string | null;
  created_at: string;
  approved_at: string | null;
}

export interface Unit {
  id: string;
  code: string;
  canonical_name: string;
  slug: string;
  active: boolean;
}

export interface Bed {
  id: number;
  unit_id: string;
  bed_number: number;
  patient_sigla: string | null;
  clinical_summary: string | null;
  occupied_since: string | null;
  last_updated_by: string | null;
  last_updated_at: string;
  version: number;
}

export interface Counter {
  unit_id: string;
  sector_key: string;
  occupancy: number;
  capacity: number;
  last_updated_at: string;
  version: number;
}

export type SpecialistStatus = 'available' | 'unavailable' | 'on_call';

export interface Specialist {
  unit_id: string;
  sector_key: string;
  status: SpecialistStatus;
  last_updated_at: string;
  version: number;
}

export type ExamStatus = 'working' | 'unavailable';

export interface Exam {
  unit_id: string;
  sector_key: string;
  status: ExamStatus;
  unavailable_reason: string | null;
  last_updated_at: string;
  version: number;
}

export interface UnitState {
  unit: Unit;
  beds: Bed[];
  counters: Counter[];
  specialists: Specialist[];
  exams: Exam[];
  updated_at: string;
}
