/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  eslint: { ignoreDuringBuilds: true },
  async rewrites() {
    const backend = process.env.NEXT_PUBLIC_API_BASE_URL || 'http://localhost:8000';
    return [
      // In dev, proxy /api/* requests to the FastAPI backend so we don't hit CORS.
      { source: '/api/:path*', destination: `${backend}/api/:path*` },
    ];
  },
};

module.exports = nextConfig;
