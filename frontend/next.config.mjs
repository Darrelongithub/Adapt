/** The dev server must accept the preview origin Arena hands to the browser, or Next
 *  rejects the HMR request with a host check. Add more with ADAPT_DEV_ORIGIN. */
const extra = (process.env.ADAPT_DEV_ORIGIN ?? "").split(",").map((s) => s.trim()).filter(Boolean);
/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  allowedDevOrigins: [...new Set([".e2b.app", "*.e2b.app", ".trycloudflare.com", "*.trycloudflare.com", "localhost", ...extra])],
};
export default nextConfig;
