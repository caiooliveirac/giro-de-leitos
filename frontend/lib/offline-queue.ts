'use client';

import { openDB, type IDBPDatabase } from 'idb';

const DB_NAME = 'giro-offline';
const DB_VERSION = 1;
const STORE = 'pending_mutations';
const MAX_ATTEMPTS = 5;

export interface PendingMutation {
  id?: number;
  url: string;
  method: string;
  headers: Record<string, string>;
  body: string | null;
  created_at: number;
  attempts: number;
  status?: 'pending' | 'failed';
}

let dbPromise: Promise<IDBPDatabase> | null = null;

function getDb(): Promise<IDBPDatabase> {
  if (typeof window === 'undefined') {
    return Promise.reject(new Error('IndexedDB unavailable (SSR)'));
  }
  if (!dbPromise) {
    dbPromise = openDB(DB_NAME, DB_VERSION, {
      upgrade(db) {
        if (!db.objectStoreNames.contains(STORE)) {
          db.createObjectStore(STORE, { keyPath: 'id', autoIncrement: true });
        }
      },
    });
  }
  return dbPromise;
}

export async function enqueue(req: Omit<PendingMutation, 'created_at' | 'attempts' | 'id'>): Promise<void> {
  try {
    const db = await getDb();
    await db.add(STORE, {
      ...req,
      created_at: Date.now(),
      attempts: 0,
      status: 'pending',
    } satisfies PendingMutation);
  } catch (err) {
    // Best-effort: if IndexedDB is unavailable, the mutation is simply lost.
    // eslint-disable-next-line no-console
    console.warn('[offline-queue] enqueue failed', err);
  }
}

export async function listPending(): Promise<PendingMutation[]> {
  try {
    const db = await getDb();
    return (await db.getAll(STORE)) as PendingMutation[];
  } catch {
    return [];
  }
}

let flushing = false;

export async function flush(): Promise<void> {
  if (typeof window === 'undefined') return;
  if (flushing) return;
  if (!navigator.onLine) return;
  flushing = true;
  try {
    const db = await getDb();
    const all = (await db.getAll(STORE)) as PendingMutation[];
    for (const item of all) {
      if (item.status === 'failed') continue;
      try {
        const res = await fetch(item.url, {
          method: item.method,
          headers: item.headers,
          body: item.body,
          credentials: 'include',
        });
        if (res.ok) {
          if (item.id !== undefined) await db.delete(STORE, item.id);
        } else if (res.status >= 400 && res.status < 500) {
          // Client errors won't succeed by retrying — mark as failed.
          if (item.id !== undefined) {
            await db.put(STORE, { ...item, attempts: item.attempts + 1, status: 'failed' });
          }
        } else {
          const attempts = item.attempts + 1;
          const status = attempts >= MAX_ATTEMPTS ? 'failed' : 'pending';
          if (item.id !== undefined) {
            await db.put(STORE, { ...item, attempts, status });
          }
        }
      } catch {
        const attempts = item.attempts + 1;
        const status = attempts >= MAX_ATTEMPTS ? 'failed' : 'pending';
        if (item.id !== undefined) {
          await db.put(STORE, { ...item, attempts, status });
        }
      }
    }
  } catch (err) {
    // eslint-disable-next-line no-console
    console.warn('[offline-queue] flush failed', err);
  } finally {
    flushing = false;
  }
}

let initialized = false;

export function initOfflineQueue(): void {
  if (typeof window === 'undefined') return;
  if (initialized) return;
  initialized = true;
  window.addEventListener('online', () => {
    void flush();
  });
  if (navigator.onLine) {
    void flush();
  }
}

export const offlineQueue = {
  enqueue,
  flush,
  listPending,
  init: initOfflineQueue,
};
