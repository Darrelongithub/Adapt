"use client";

import { usePathname } from "next/navigation";
import { type ReactNode } from "react";
import { Icon, routeIcon } from "../icons";
import { Artwork, type ArtName } from "../art/Artwork";
import "./shell.css";

/** Decorations have their own grid cells. The opaque sheet is never patterned. */
export function PageShell({
  children,
  access = false,
}: {
  children: ReactNode;
  access?: boolean;
}) {
  const path = usePathname();
  const brain = !access && path === "/baseline";
  // Art has a product meaning, not a page-theme meaning. Keep it on the exact screens where
  // the owner asked for it: study on Home, classroom while choosing a subject, brain on the
  // mind check, gears on Settings, and the flower only in the signed-out welcome.
  const hero: ArtName | null = access
    ? "flower"
    : path === "/"
      ? "study"
      : path === "/journey"
        ? "classroom"
        : path === "/settings"
          ? "gears"
          : null;
  return (
    <div
      className={`page-shell${access ? " shell-access" : ""}${brain ? " shell-brain" : ""}`}
      data-page-shell
    >
      <div
        className="pattern-zone pattern-left"
        data-pattern
        aria-hidden="true"
      />
      <div className="page-sheet" data-sheet>
        {!access && !hero && !brain ? (
          <div className="shell-header-icon">
            <Icon name={routeIcon[path ?? ""] ?? "book"} />
          </div>
        ) : null}
        {children}
      </div>
      {brain ? (
        <div className="brain-zone">
          <Artwork name="brain" />
        </div>
      ) : (
        <div
          className={`pattern-zone pattern-right${hero && !["classroom", "flower"].includes(hero) ? " omit-pattern" : ""}`}
          data-pattern
          aria-hidden="true"
        />
      )}
      {hero ? (
        <div className={`hero-zone hero-${hero}`} data-hero-zone>
          <Artwork name={hero} />
        </div>
      ) : null}
    </div>
  );
}
