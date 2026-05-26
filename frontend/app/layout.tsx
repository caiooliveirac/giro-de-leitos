import type { Metadata, Viewport } from 'next';
import { Suspense } from 'react';
import { Providers } from '@/components/providers';
import './globals.css';

// Geist via Google Fonts <link> — Next 14.2 ainda não exporta Geist em
// next/font/google. Quando subir pra Next 15+, trocar pelo helper oficial.

export const metadata: Metadata = {
  title: 'Giro de Leitos',
  description: 'Gestão em tempo real de leitos hospitalares',
  manifest: '/manifest.json',
  appleWebApp: {
    capable: true,
    statusBarStyle: 'black-translucent',
    title: 'Giro',
  },
};

export const viewport: Viewport = {
  themeColor: [
    // PWA manifests não aceitam oklch — usamos aproximação hex.
    { media: '(prefers-color-scheme: light)', color: '#f7f5f0' },
    { media: '(prefers-color-scheme: dark)', color: '#171821' },
  ],
  width: 'device-width',
  initialScale: 1,
  maximumScale: 1,
  userScalable: false,
};

// Inline script to avoid FOUC: aplica data-theme="dark|light" antes do React montar.
const themeBootScript = `
(function () {
  try {
    var stored = localStorage.getItem('gl_theme');
    var prefersDark = window.matchMedia('(prefers-color-scheme: dark)').matches;
    var theme = stored === 'dark' || stored === 'light'
      ? stored
      : (prefersDark ? 'dark' : 'light');
    document.documentElement.setAttribute('data-theme', theme);
  } catch (_) {
    document.documentElement.setAttribute('data-theme', 'light');
  }
})();
`;

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="pt-BR" suppressHydrationWarning>
      <head>
        <link rel="preconnect" href="https://fonts.googleapis.com" />
        <link rel="preconnect" href="https://fonts.gstatic.com" crossOrigin="anonymous" />
        <link
          href="https://fonts.googleapis.com/css2?family=Geist:wght@400;500;600;700&display=swap"
          rel="stylesheet"
        />
        <script dangerouslySetInnerHTML={{ __html: themeBootScript }} />
      </head>
      <body>
        <Suspense fallback={null}>
          <Providers>{children}</Providers>
        </Suspense>
      </body>
    </html>
  );
}
