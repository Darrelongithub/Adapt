"use client";

import { RouteError } from "@/components/route-error";

/** Per-route boundary (Round 6, Track E): never a white page. */
export default function AnimatorError({ error, reset }: { error: Error & { digest?: string }; reset: () => void }) {
  return <RouteError error={error} reset={reset} />;
}
