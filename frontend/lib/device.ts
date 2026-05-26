const STORAGE_KEY = 'gl_device_id';

function uuid(): string {
  if (typeof crypto !== 'undefined' && 'randomUUID' in crypto) {
    return crypto.randomUUID();
  }
  // Fallback (very old browsers): RFC4122 v4-ish.
  return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, (c) => {
    const r = (Math.random() * 16) | 0;
    const v = c === 'x' ? r : (r & 0x3) | 0x8;
    return v.toString(16);
  });
}

export function getOrCreateDeviceFingerprint(): string {
  if (typeof window === 'undefined') {
    throw new Error('getOrCreateDeviceFingerprint can only be called in the browser');
  }
  let id = window.localStorage.getItem(STORAGE_KEY);
  if (!id) {
    id = uuid();
    window.localStorage.setItem(STORAGE_KEY, id);
  }
  return id;
}
