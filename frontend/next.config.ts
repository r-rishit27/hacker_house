import type { NextConfig } from "next";

/**
 * One origin for the whole product. The browser only ever talks to this app; Next forwards
 *   /api/*      -> the console API (api/main.py)
 *   /v2/*       -> the v2 service (api_v2: sign-in, approvals, regulator data, Ask Sentinel)
 *   /regulator  -> the regulator's overview page, served by the v2 service
 * so the HttpOnly session cookie set at sign-in reaches both services, and nothing needs CORS.
 */
const API_BASE = (process.env.SENTINEL_API_BASE ?? "http://127.0.0.1:8000").replace(/\/$/, "");
const V2_BASE = (process.env.SENTINEL_V2_BASE ?? "http://127.0.0.1:8100").replace(/\/$/, "");

const config: NextConfig = {
  reactStrictMode: true,
  typescript: { ignoreBuildErrors: false },
  eslint: { ignoreDuringBuilds: false },
  async rewrites() {
    return [
      { source: "/api/:path*", destination: `${API_BASE}/api/:path*` },
      { source: "/v2/:path*", destination: `${V2_BASE}/v2/:path*` },
      { source: "/regulator", destination: `${V2_BASE}/regulator` },
    ];
  },
  async redirects() {
    // The v2 service's own case portal is superseded by the console; its links land here.
    return [{ source: "/portal", destination: "/", permanent: false }];
  },
};

export default config;
