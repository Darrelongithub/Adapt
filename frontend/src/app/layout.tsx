import type { Metadata } from "next";

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
      <body className="min-h-screen antialiased">
        <a href="#main-content" className="skip-link">Skip to content</a>
        <div className="grain" aria-hidden="true" />
        <div aria-live="polite" className="sr-only" id="adapt-live-region" />
        <Opening />
        <SessionGate>
          <Chrome>{children}</Chrome>
        </SessionGate>
      </body>
    </html>
  );
}
