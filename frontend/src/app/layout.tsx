import type { Metadata } from "next";

/* The three typefaces, self-hosted from @fontsource. They used to arrive as an
 * `@import url(https://fonts.googleapis.com/...)` at the top of globals.css, which made
 * first paint wait on two internet round trips before any CSS applied — the single biggest
 * reason a phone on a slow (or school, or offline-LAN) connection saw an unstyled, slow,
 * "weird" app. Bundled woff2 files are served from this origin with immutable cache headers,
 * so the look is identical and the network is no longer in the critical path. */
import "@fontsource-variable/fraunces/full.css";
import "@fontsource-variable/fraunces/wght-italic.css";
import "@fontsource-variable/inter";
import "@fontsource-variable/inter/wght-italic.css";
import "@fontsource-variable/jetbrains-mono";
import "@fontsource-variable/jetbrains-mono/wght-italic.css";

import "./globals.css";

import { Chrome, SessionGate } from "@/components/chrome";
import { Opening } from "@/components/opening";

export const metadata: Metadata = {
  title: "ADAPT — adaptive practice that asks before it changes anything",
  description:
    "A learning app that measures how you study, proposes changes with the evidence attached, and only applies the ones you say yes to.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      {/* min-h-dvh (not 100vh): on a phone's browser the address bar means the visual
       *  viewport is shorter than the layout viewport, and 100vh leaves the footer
       *  stranded underneath the browser chrome. */}
      <body className="min-h-dvh antialiased">
        <a href="#main-content" className="skip-link">Skip to content</a>
        <div className="grain" aria-hidden="true" />
        <div aria-live="polite" className="sr-only" id="adapt-live-region" />
        <Opening>
          <SessionGate>
            <Chrome>{children}</Chrome>
          </SessionGate>
        </Opening>
      </body>
    </html>
  );
}
