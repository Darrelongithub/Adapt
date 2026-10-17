"use client";

/**
 * The last line of defence (Round 6, Track E): a crash inside the root layout
 * itself. This one must render its own <html> — the layout is gone.
 */
export default function GlobalError({ error, reset }: { error: Error & { digest?: string }; reset: () => void }) {
  return (
    <html lang="en">
      <body style={{ fontFamily: "system-ui, sans-serif", background: "#f4eee0", color: "#123a8f", padding: 48 }}>
        <h1 style={{ fontSize: 30, marginBottom: 12 }}>Something went wrong here</h1>
        <p style={{ color: "#3c4a68", maxWidth: 480 }}>
          The app could not finish loading. Nothing you answered was lost — your work is saved on the server.
        </p>
        <button
          type="button"
          onClick={reset}
          style={{ marginTop: 24, padding: "12px 22px", borderRadius: 14, border: "none", background: "#2456c8", color: "#fff", fontSize: 15, fontWeight: 600, cursor: "pointer" }}
        >
          Try again
        </button>
      </body>
    </html>
  );
}
