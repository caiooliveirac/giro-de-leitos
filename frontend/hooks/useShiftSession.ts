'use client';

import { useEffect, useState } from 'react';

const STORAGE_KEY = 'gl_shift_user';

export interface ShiftUser {
  id: string;
  name: string;
  role?: string;
}

export function useShiftSession() {
  const [user, setUser] = useState<ShiftUser | null>(null);
  const [hydrated, setHydrated] = useState(false);

  useEffect(() => {
    try {
      const raw = window.localStorage.getItem(STORAGE_KEY);
      if (raw) setUser(JSON.parse(raw) as ShiftUser);
    } catch {
      // ignore
    }
    setHydrated(true);
  }, []);

  return { user, hasSession: Boolean(user), hydrated };
}
