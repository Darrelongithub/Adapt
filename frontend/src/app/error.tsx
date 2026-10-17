"use client";

import { RouteError } from "@/components/route-error";

/** Every route that does not ship its own boundary lands here (Round 6, Track E). */
export default function RootError({ error, reset }: { error: Error & { digest?: string }; reset: () => void }) {
  return <RouteError error={error} reset={reset} />;
}
