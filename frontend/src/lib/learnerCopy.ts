/** Learner-facing wording for the contrast screen.

The page used to print `(n=0)` on a stated choice and "planned from profile
version 0, 0 pieces of evidence" under a demo profile. Both are display
problems: the numbers are real, the sentence was not.
*/

export function evidenceMark(
  source: string,
  n: number,
  evidenceIds: string[] = [],
  opts?: { includeSource?: boolean },
): string {
  const src = String(source || "").trim();
  const stated = src.toLowerCase() === "stated";
  const count = Number(n);
  const finite = Number.isFinite(count);
  if (stated && (!finite || count === 0)) return "stated";
  if (opts?.includeSource) {
    const ids = evidenceIds.length ? `, from ${evidenceIds.join(", ")}` : "";
    const shown = finite ? count : 0;
    if (stated) return shown > 0 ? `stated, n=${shown}${ids}` : `stated${ids}`;
    return `${src}, n=${shown}${ids}`;
  }
  if (stated) return "stated";
  return `(n=${finite ? count : 0})`;
}

/** Footer under a contrast lesson. Never names a profile version or a service. */
export function contrastFoot(input: { modelVersion?: number | null; evidenceN?: number }): string {
  const version = input.modelVersion == null ? 0 : Number(input.modelVersion);
  const n = Number(input.evidenceN) || 0;
  const lessons = "Lessons: simple mode.";
  const pieces = n === 1 ? "1 piece of evidence" : `${n} pieces of evidence`;
  if (!Number.isFinite(version) || version <= 0) {
    if (n <= 0) {
      return `${lessons} This lesson was planned from a demo profile, not from answers you have given.`;
    }
    return `${lessons} Planned from a demo profile, with ${pieces} behind it.`;
  }
  return `${lessons} Planned from this profile, with ${pieces} behind it.`;
}

/** The privacy list is public. Do not say it failed while it is still loading, or after it arrived. */
export function showPrivacyUnread(loading: boolean, recordedCount: number): boolean {
  const count = Number(recordedCount);
  return !loading && (!Number.isFinite(count) || count <= 0);
}
