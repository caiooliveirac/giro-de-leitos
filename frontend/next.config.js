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

// Suporte a basePath via env (padrao do repo maternidades-salvador):
// APP_BASE_PATH=/tabela/upas monta o dashboard sob o prefixo da tabela.
const configuredBasePath = (process.env.APP_BASE_PATH || "").trim();
const normalizedBasePath = configuredBasePath
  ? "/" + configuredBasePath.replace(/^\/+|\/+$/g, "")
  : "";

const nextConfig = {
  reactStrictMode: true,
  output: 'standalone',
  basePath: normalizedBasePath || undefined,
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
