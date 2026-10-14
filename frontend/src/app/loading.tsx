/** Keep the shared navigation usable while Next loads a destination's route bundle. */
export default function Loading() {
  return (
    <section aria-busy="true" aria-label="Loading page" className="space-y-4 py-6">
      <p role="status" className="text-sm text-[var(--muted)]">Opening page…</p>
      <p className="text-xs text-[var(--muted)]">You can still choose another section from the menu.</p>
    </section>
  );
}
