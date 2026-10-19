import type { ArtName } from "../art/Artwork";
export type { ArtName };

export type ArtPlacement = { art: ArtName; size: "large" | "small"; position: string };

/**
 * Single source of truth for route-owned illustration.
 *
 * Every page gets exactly ONE header illustration, drawn by <PageHeader> (or the Home
 * hero) straight from this table, so no page can forget it and no page can pick a
 * different one. Rules:
 * - One illustration per page, at the top, visible without scrolling.
 * - A page never shows the same illustration twice.
 * - The bottom "scene" (`SCENE_ART`) and the side-rail pattern are decoration, drawn by
 *   the shell, never by a page; they sit in their own boxes and never under text.
 */
export const ART_MAP: Record<string, ArtPlacement[]> = {
  "/": [{ art: "study", size: "large", position: "hero-top" }],
  "/learn": [{ art: "classroom", size: "small", position: "header-right" }],
  "/lesson": [{ art: "desk-path", size: "small", position: "header-right" }],
  "/first-lesson": [{ art: "desk-path", size: "small", position: "header-right" }],
  "/review": [{ art: "road-ahead", size: "small", position: "header-right" }],
  "/progress": [{ art: "knowledge-tree", size: "small", position: "header-right" }],
  "/journey": [{ art: "stepping-stones", size: "small", position: "header-right" }],
  "/proposals": [{ art: "balance-scale", size: "small", position: "header-right" }],
  "/evidence": [{ art: "split-cards", size: "small", position: "header-right" }],
  "/voice": [{ art: "open-book", size: "small", position: "header-right" }],
  "/settings": [{ art: "gears", size: "small", position: "header-right" }],
  "/baseline": [{ art: "brain", size: "large", position: "hero-top" }],
  "/onboarding": [{ art: "terrain-map", size: "small", position: "header-right" }],
  "/diagnose": [{ art: "road-ahead", size: "small", position: "header-right" }],
  "/consent": [{ art: "flower", size: "small", position: "bottom-left" }],
  "/signin": [{ art: "flower", size: "small", position: "bottom-left" }],
};

/** The quiet band at the foot of every signed-in page. */
export const SCENE_ART: ArtName = "horizon-memory";

export const artForRoute = (path: string, art: ArtName) =>
  ART_MAP[path]?.find((entry) => entry.art === art) ?? null;

/** The header illustration a route owns, if any. */
export const headerArtForRoute = (path: string): ArtPlacement | null =>
  ART_MAP[path]?.find((entry) => entry.position !== "bottom-left") ?? null;
