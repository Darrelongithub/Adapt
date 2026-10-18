export type ArtName = "brain" | "classroom" | "study" | "gears" | "flower";
export type ArtPlacement = { art: ArtName; size: "large" | "small"; position: string };
/** Single source of truth for route-owned illustration. */
export const ART_MAP: Record<string, ArtPlacement[]> = {
  "/": [
    { art: "flower", size: "small", position: "bottom-left" }, // splash only
    { art: "study", size: "large", position: "hero-top" },
  ],
  "/baseline": [{ art: "brain", size: "large", position: "hero-top-right" }],
  "/journey": [{ art: "classroom", size: "large", position: "hero-top" }],
  "/learn": [{ art: "classroom", size: "large", position: "hero-top" }],
  "/settings": [{ art: "gears", size: "large", position: "hero-top-right" }],
  "/consent": [{ art: "flower", size: "small", position: "bottom-left" }],
  "/signin": [{ art: "flower", size: "small", position: "bottom-left" }],
};
export const artForRoute = (path: string, art: ArtName) => ART_MAP[path]?.find((entry) => entry.art === art) ?? null;
