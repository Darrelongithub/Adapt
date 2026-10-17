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
  const hero: ArtName | null = access
    ? "flower"
    : path === "/journey" || path === "/"
      ? "study"
      : path === "/settings"
        ? "gears"
        : path === "/first-lesson" || path === "/learn"
          ? "classroom"
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
