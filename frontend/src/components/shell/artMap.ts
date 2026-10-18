export type ArtName =
  | "brain"
  | "classroom"
  | "study"
  | "gears"
  | "flower"
  | "desk-path"
  | "horizon-memory"
  | "knowledge-tree"
  | "open-book"
  | "stepping-stones"
  | "balance-scale"
  | "split-cards";

export type ArtPlacement = { art: ArtName; size: "large" | "small"; position: string };

/**
 * Single source of truth for route-owned illustration.
 *
 * Rules:
 * - Use only the stronger illustrations (`study`, `classroom`, `brain`, `gears`,
 *   `flower`, `stepping-stones`, `balance-scale`).
 * - Art must be visible without scrolling (top of page hero or PageHeader).
 * - Never repeat the same illustration both small in the header and large in the
 *   empty state on the same page.
 * - One well-placed illustration per page.
 */
export const ART_MAP: Record<string, ArtPlacement[]> = {
  "/": [{ art: "study", size: "large", position: "hero-top" }],
  "/learn": [{ art: "classroom", size: "small", position: "header-right" }],
  "/journey": [{ art: "stepping-stones", size: "small", position: "header-right" }],
  "/proposals": [{ art: "balance-scale", size: "small", position: "header-right" }],
  "/settings": [{ art: "gears", size: "small", position: "header-right" }],
  "/baseline": [{ art: "brain", size: "large", position: "hero-top" }],
  "/consent": [{ art: "flower", size: "small", position: "bottom-left" }],
  "/signin": [{ art: "flower", size: "small", position: "bottom-left" }],
};

export const artForRoute = (path: string, art: ArtName) =>
  ART_MAP[path]?.find((entry) => entry.art === art) ?? null;
