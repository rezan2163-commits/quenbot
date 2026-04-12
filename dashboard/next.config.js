/** @type {import('next').NextConfig} */
const nextConfig = {
  output: "standalone",
  async rewrites() {
    const API_TARGET = process.env.API_TARGET || "http://127.0.0.1:3001";
    return [
      { source: "/api/:path*", destination: `${API_TARGET}/api/:path*` },
    ];
  },
};

module.exports = nextConfig;
