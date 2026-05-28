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

export interface ApiMutateOptions {
  offlineQueue?: boolean;
}

/**
 * Wrapper around apiFetch that, on network failure, can enqueue the request
 * in the offline IndexedDB queue instead of throwing. Returns the parsed
 * response body on success, or `null` when the request was enqueued.
 */
export async function apiMutate<T>(
  path: string,
  init: RequestInit = {},
  options: ApiMutateOptions = {},
): Promise<T | null> {
  try {
    return await apiFetch<T>(path, init);
  } catch (err) {
    const isNetworkFailure =
      !(err instanceof ApiError) || (err instanceof ApiError && err.status === 0);
    if (options.offlineQueue && isNetworkFailure) {
      const { enqueue } = await import('@/lib/offline-queue');
      const headers: Record<string, string> = { 'Content-Type': 'application/json' };
      if (init.headers) {
        new Headers(init.headers).forEach((v, k) => {
          headers[k] = v;
        });
      }
      let body: string | null = null;
      if (typeof init.body === 'string') body = init.body;
      else if (init.body) body = JSON.stringify(init.body);
      await enqueue({
        url: path,
        method: (init.method ?? 'GET').toUpperCase(),
        headers,
        body,
      });
      return null;
    }
    throw err;
  }
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

export type ResourceSource = 'manual' | 'parser' | 'default';

export interface Bed {
  bed_number: number;
  patient_sigla: string | null;
  clinical_summary: string | null;
  occupied_since: string | null;
  last_updated_by: string | null;
  last_updated_at: string;
  version: number;
  source?: ResourceSource;
}

export interface Counter {
  sector_key: string;
  occupancy: number;
  capacity: number;
  last_updated_at: string;
  version: number;
  source?: ResourceSource;
}

export type SpecialistStatus = 'available' | 'unavailable' | 'on_call';

export interface Specialist {
  sector_key: string;
  status: SpecialistStatus;
  last_updated_at: string;
  version: number;
  source?: ResourceSource;
}

export type ExamStatus = 'working' | 'unavailable';

export interface Exam {
  sector_key: string;
  status: ExamStatus;
  unavailable_reason: string | null;
  last_updated_at: string;
  version: number;
  source?: ResourceSource;
}

export interface SectorConfig {
  sector_key: string;
  enabled: boolean;
  capacity: number | null;
}

export interface ParserSnapshot {
  received_at: string | null;
  is_critical: boolean;
  raw_text: string;
  unit_match_method: string | null;
}

export interface UnitState {
  unit: Unit;
  sectors_config: SectorConfig[];
  beds: Bed[];
  counters: Counter[];
  specialists: Specialist[];
  exams: Exam[];
  updated_at?: string;
  parser_snapshot?: ParserSnapshot | null;
}

// ---------------------------------------------------------------------------
// Auth / invite types — alinhados com backend (auth/schemas.py).
// ---------------------------------------------------------------------------

export type InviteKind = 'coordinator' | 'professional';

export interface InvitePreview {
  type: InviteKind;
  unit_name: string | null;
  inviter_name: string;
  expires_at: string;
}

export interface InviteAcceptPayload {
  name: string;
  cpf: string;
  phone: string;
  cargo: string;
  coren_crm: string | null;
  password: string;
  pin: string;
  photo_url: string;
  lgpd_accepted: boolean;
}

export interface DeviceSelfPairPayload {
  username?: string;
  cpf?: string;
  password: string;
  pin: string;
  device_fingerprint: string;
  label?: string | null;
}

export interface DeviceSelfPairResponse {
  device_id: string;
  unit_id: string;
  session_id: string;
  expires_at: string;
  must_change_password: boolean;
  user: {
    id: string;
    name: string;
    role: UserRole;
    cargo: string | null;
    photo_url: string | null;
    status: UserStatus;
    unit_id: string | null;
    cpf_masked: string;
  };
}

export interface PairingCodeResponse {
  pairing_code: string;
  expires_at: string;
}

export interface StaffMember {
  id: string;
  name: string;
  role: UserRole;
  cargo: string | null;
  photo_url: string | null;
  status: UserStatus;
  unit_id: string | null;
  cpf_masked: string;
}
