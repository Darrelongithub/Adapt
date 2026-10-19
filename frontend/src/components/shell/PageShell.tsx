"use client";

import { type ReactNode } from "react";
import { PageArt } from "./PageArt";
import { PageScene } from "./PageScene";
import "./shell.css";
import "../../styles/premium.css"; // last, so it refines tokens/globals/shell without forking them

/**
 * The shell around every page: a centred sheet flanked by two "rails" that carry the
 * contour pattern, and a quiet horizon scene that closes the page.
 *
 * Stacking is fixed so nothing can sit on top of art: rails (0) < sheet (1) < art (2).
 * The rails are their own grid columns, so the pattern is never behind words; the scene
 * is in the page flow after the last card, so it is never over words either.
 */
export function PageShell({ children, access = false }: { children: ReactNode; access?: boolean }) {
  return (
    <div className={`page-frame${access ? " page-frame-access" : ""}`} data-page-shell>
      <div className="pattern-zone pattern-left" data-pattern="left" aria-hidden="true">
        <div className="topo-fill" />
      </div>
      <div className="page-sheet" data-sheet>
        {children}
        {access ? null : <PageScene />}
      </div>
      <div className="pattern-zone pattern-right" data-pattern="right" aria-hidden="true">
        <div className="topo-fill" />
      </div>
      {access ? (
        <div className="access-flower" data-art="flower" aria-hidden="true">
          <PageArt name="flower" />
        </div>
      ) : null}
    </div>
  );
}
