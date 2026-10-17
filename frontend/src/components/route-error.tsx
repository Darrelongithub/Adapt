"use client";

/**
 * The one error screen (Round 6, Track E). A route crash never shows a white
 * page: it shows this — what happened in plain words, what was not lost, and one
 * button that tries the route again. Route folders import it in their error.tsx.
 */

import { useEffect } from "react";
import { Btn, Card } from "@/components/ui";

export function RouteError({ error, reset }: { error: Error & { digest?: string }; reset: () => void }) {
  useEffect(() => {
    // Keep the fact, not the payload: the message can quote a learner's answer.
    console.error("route error:", error.name, error.digest ?? "");
  }, [error]);

  return (
    <div className="page-pad" data-route-error>
      <Card title="Something went wrong here" tone="peach">
        <p>
          This screen could not finish loading. Nothing you answered was lost — your work is saved on the server.
        </p>
        <div className="wrap-2" style={{ marginTop: "var(--s3)" }}>
          <Btn icon="refresh" kind="primary" onClick={reset}>
            Try again
          </Btn>
          <Btn kind="secondary" href="/">
            Go home
          </Btn>
        </div>
        {error?.digest ? <p className="t-small" style={{ marginTop: "var(--s2)" }}>Reference: {error.digest}</p> : null}
      </Card>
    </div>
  );
}
