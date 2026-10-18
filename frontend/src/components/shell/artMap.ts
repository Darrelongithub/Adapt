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
/** Single source of truth for route-owned illustration. Large art lives at the top (hero or PageHeader), never at the bottom of a scrolling page. */
export const ART_MAP: Record<string, ArtPlacement[]> = {
  "/": [{ art: "study", size: "large", position: "hero-top" }],
  "/learn": [
    { art: "classroom", size: "large", position: "hero-top" },
    { art: "desk-path", size: "small", position: "header-right" },
  ],
  "/review": [{ art: "horizon-memory", size: "large", position: "hero-top" }],
  "/progress": [{ art: "knowledge-tree", size: "large", position: "header-right" }],
  "/lesson": [{ art: "open-book", size: "large", position: "hero-top" }],
  "/journey": [{ art: "stepping-stones", size: "large", position: "hero-top" }],
  "/proposals": [{ art: "balance-scale", size: "large", position: "hero-top" }],
  "/contrast": [{ art: "split-cards", size: "large", position: "hero-top" }],
  "/settings": [{ art: "gears", size: "large", position: "header-right" }],
  "/baseline": [{ art: "brain", size: "large", position: "hero-top" }],
  "/consent": [{ art: "flower", size: "small", position: "bottom-left" }],
  "/signin": [{ art: "flower", size: "small", position: "bottom-left" }],
};
export const artForRoute = (path: string, art: ArtName) => ART_MAP[path]?.find((entry) => entry.art === art) ?? null;
