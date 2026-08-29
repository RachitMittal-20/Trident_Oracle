import type { NextConfig } from "next";

// Security headers applied to every response. CSP in particular is scoped
// to what this app actually needs, not a boilerplate policy:
// - script-src needs 'unsafe-inline' in BOTH dev and production, not just
//   dev. A nonce-based script-src (Next's documented strict-CSP pattern --
//   generate a per-request nonce in middleware, thread it through the CSP
//   header) was tried and tested live against a production build: Next's
//   own internal inline bootstrap scripts (the RSC streaming payload
//   pushes -- self.__next_f.push(...), page-specific data, so not
//   hash-allowlistable either) did NOT pick up the nonce in practice, and
//   the app stayed blank under strict-CSP+nonce exactly as it did under
//   strict-CSP+nothing. 'unsafe-inline' is therefore a real, load-bearing
//   requirement of this policy for this app/Next version, not dev-only
//   scaffolding -- documented here so a future tightening attempt doesn't
//   re-discover this the hard way.
// - connect-src needs 'self' plus any http(s) origin in dev, since
//   NEXT_PUBLIC_API_BASE_URL points at a separately-run FastAPI dev server
//   on an arbitrary local port (no fixed production API domain exists yet
//   for this project) -- tightened to 'self' only once a real deployed API
//   origin exists to name explicitly instead.
// - img-src allows any https origin: invoice/eval-run thumbnails are
//   signed Supabase Storage URLs (a different subdomain per project), not
//   a fixed host this config can name once and for all.
// - style-src allows 'unsafe-inline': components throughout this app set
//   inline `style` attributes directly (dynamic chart colors, anime.js/
//   Recharts writing computed styles) -- this is the same "restrict
//   scripts, not inline style attributes" tradeoff most real-world Next.js
//   CSPs make, since a nonce-based style-src would require plumbing a
//   per-request nonce through every one of those call sites for no
//   meaningful reduction in actual XSS risk (inline style injection alone
//   isn't a script-execution vector).
const isDev = process.env.NODE_ENV !== "production";

const csp = [
  "default-src 'self'",
  `script-src 'self' 'unsafe-inline'${isDev ? " 'unsafe-eval'" : ""}`,
  "style-src 'self' 'unsafe-inline'",
  "img-src 'self' data: https:",
  `connect-src 'self'${isDev ? " http://localhost:* ws://localhost:*" : ""}`,
  "font-src 'self' data:",
  "object-src 'none'",
  "base-uri 'self'",
  "form-action 'self'",
  "frame-ancestors 'none'",
].join("; ");

const securityHeaders = [
  { key: "Content-Security-Policy", value: csp },
  { key: "Strict-Transport-Security", value: "max-age=63072000; includeSubDomains; preload" },
  { key: "X-Content-Type-Options", value: "nosniff" },
  { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
  { key: "X-Frame-Options", value: "DENY" },
];

const nextConfig: NextConfig = {
  async headers() {
    return [
      {
        source: "/:path*",
        headers: securityHeaders,
      },
    ];
  },
};

export default nextConfig;
