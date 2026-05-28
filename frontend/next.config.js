/** @type {import('next').NextConfig} */
const withPWA = require('next-pwa')({
  dest: 'public',
  register: true,
  skipWaiting: true,
  // Desabilitado temporariamente durante fase de bugfix:
  // o SW gerado pelo next-pwa estava cacheando bundles antigos com bugs
  // e dificultando a propagação de fixes. public/sw.js (kill-switch)
  // assume o controle, limpa caches e se desregistra automaticamente.
  disable: true,
});

const nextConfig = {
  reactStrictMode: true,
  output: 'standalone',
  experimental: {
    optimizePackageImports: ['lucide-react', 'framer-motion'],
  },
  async rewrites() {
    return [
      { source: '/api/:path*', destination: 'http://localhost:8000/api/:path*' },
      { source: '/ws/:path*', destination: 'http://localhost:8000/ws/:path*' },
    ];
  },
};

module.exports = withPWA(nextConfig);
