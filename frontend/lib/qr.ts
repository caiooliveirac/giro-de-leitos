// Tiny URL-only QR encoder helper that returns a Google Charts proxy URL.
// Real QR generation will arrive in a later phase — keeping the surface here
// small so the UI can drop in a real <svg> later.

export function qrImageUrl(text: string, size = 240): string {
  const enc = encodeURIComponent(text);
  return `https://api.qrserver.com/v1/create-qr-code/?size=${size}x${size}&data=${enc}`;
}
