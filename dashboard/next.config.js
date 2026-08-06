/** @type {import('next').NextConfig} */
const nextConfig = {
  output: 'standalone',
  reactStrictMode: true,
  experimental: {
    optimizePackageImports: ['lucide-react', 'recharts'],
  },
  images: {
    remotePatterns: [
      {
        protocol: 'https',
        hostname: '**',
      },
    ],
  },
  async rewrites() {
    // Same-origin proxy for the whole gateway API: the browser talks to this
    // dev server (:3100) and Next forwards server-side to nexus-api. This
    // sidesteps CORS entirely and keeps working even if a browser tab holds
    // a bundle built with a stale NEXT_PUBLIC_API_URL. The dashboard has no
    // Next API routes of its own, so /api/* is safe to delegate wholesale.
    return [
      {
        source: '/api/:path*',
        destination: `${process.env.NEXT_PUBLIC_API_URL || 'http://127.0.0.1:9000'}/api/:path*`,
      },
    ];
  },
};

module.exports = nextConfig;
