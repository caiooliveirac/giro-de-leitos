'use client';

import { useEffect, useState } from 'react';

const SHIFT_KEY = 'gl_shift_user';
const ADMIN_KEY = 'gl_admin_user';

export interface CurrentUser {
  id: string;
  name: string;
  role: 'admin' | 'coordinator' | 'professional';
  unit_id?: string | null;
  source: 'shift' | 'admin';
}

export function useCurrentUser() {
  const [user, setUser] = useState<CurrentUser | null>(null);
  const [hydrated, setHydrated] = useState(false);

  useEffect(() => {
    try {
      const admin = window.localStorage.getItem(ADMIN_KEY);
      if (admin) {
        const parsed = JSON.parse(admin) as Omit<CurrentUser, 'source'>;
        setUser({ ...parsed, source: 'admin' });
        setHydrated(true);
        return;
      }
      const shift = window.localStorage.getItem(SHIFT_KEY);
      if (shift) {
        const parsed = JSON.parse(shift) as Omit<CurrentUser, 'source'>;
        setUser({ ...parsed, source: 'shift' });
      }
    } catch {
      // ignore
    }
    setHydrated(true);
  }, []);

  return {
    user,
    hydrated,
    isAdmin: user?.role === 'admin',
    isCoordinator: user?.role === 'coordinator' || user?.role === 'admin',
  };
}
