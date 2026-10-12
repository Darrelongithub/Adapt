import type { Metadata } from "next";

import "./globals.css";

import { Chrome, SessionGate } from "@/components/chrome";

export const metadata: Metadata = {
  title: "ADAPT — adaptive practice that asks before it changes anything",
  description:
    "A learning app that measures how you study, proposes changes with the evidence attached, and only applies the ones you say yes to.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body className="min-h-screen antialiased">
        <SessionGate>
          <Chrome>{children}</Chrome>
        </SessionGate>
      </body>
    </html>
  );
}
