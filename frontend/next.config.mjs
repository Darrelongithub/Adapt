/** The dev server must accept the preview origin Arena hands to the browser, or Next
 *  rejects the HMR request with a host check. Add more with ADAPT_DEV_ORIGIN. */
const extra = (process.env.ADAPT_DEV_ORIGIN ?? "").split(",").map((s) => s.trim()).filter(Boolean);
/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  async headers() {
    return [{ source: "/art/:path*", headers: [{ key: "Cache-Control", value: "public, max-age=86400, stale-while-revalidate=604800" }] }];
  },
  // A second output directory is the only clean way to run `next build` while `next dev` is
  // serving: they share `.next`, and a build under a live dev server makes every page answer 500
  // ("Could not find the module ... in the React Client Manifest") until the directory is deleted.
  // `ADAPT_NEXT_DIST=.next-build npm run build` keeps both honest.
  distDir: process.env.ADAPT_NEXT_DIST ?? ".next",
  // `127.0.0.1` is not an alias of `localhost` to Next's host check. Left out, a page loaded
  // by IP gets its HTML and its CSS and has its `/_next/*` chunks refused — which renders a
  // *complete-looking* screen whose buttons do nothing, because React never hydrated. The
  // probe drives the app that way, and so does anyone else who types an address.
  allowedDevOrigins: [
    ...new Set([".e2b.app", "*.e2b.app", ".trycloudflare.com", "*.trycloudflare.com", "localhost", "127.0.0.1", "[::1]", "::1", ...extra]),
  ],
};
export default nextConfig;
