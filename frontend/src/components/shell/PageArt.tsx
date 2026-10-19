"use client";
import { usePathname } from "next/navigation";
import { Artwork } from "../art/Artwork";
import { artForRoute, headerArtForRoute, type ArtName } from "./artMap";

/** Route-checked art renderer; pages never select an SVG directly. */
export function PageArt({ name, className = "" }: { name: ArtName; className?: string }) {
  const route = usePathname() || "/";
  const placement = artForRoute(route, name);
  if (!placement) return null;
  return <Artwork name={name} className={`art-size-${placement.size} art-position-${placement.position} ${className}`} />;
}

/** The illustration the current route owns, per `ART_MAP`. Used by <PageHeader> so every page has one. */
export function RouteArt({ className = "" }: { className?: string }) {
  const route = usePathname() || "/";
  const placement = headerArtForRoute(route);
  if (!placement) return null;
  return <Artwork name={placement.art} className={`art-size-${placement.size} art-position-${placement.position} ${className}`} />;
}
