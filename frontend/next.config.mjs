/** The dev server must accept the preview origin Arena hands to the browser, or Next
 *  rejects the HMR request with a host check. Add more with ADAPT_DEV_ORIGIN. */
const extra = (process.env.ADAPT_DEV_ORIGIN ?? "").split(",").map((s) => s.trim()).filter(Boolean);
/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  // `127.0.0.1` is not an alias of `localhost` to Next's host check. Left out, a page loaded
  // by IP gets its HTML and its CSS and has its `/_next/*` chunks refused — which renders a
  // *complete-looking* screen whose buttons do nothing, because React never hydrated. The
  // probe drives the app that way, and so does anyone else who types an address.
  allowedDevOrigins: [
    ...new Set([".e2b.app", "*.e2b.app", ".trycloudflare.com", "*.trycloudflare.com", "localhost", "127.0.0.1", "[::1]", "::1", ...extra]),
  ],
};
export default nextConfig;
