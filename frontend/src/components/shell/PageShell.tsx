"use client";

import { type ReactNode } from "react";
import { PageArt } from "./PageArt";
import "./shell.css";

/**
 * The shell around every page (Round 6, Track A): a centred sheet with two empty
 * gutters that carry the topo pattern — the only place the shell decorates, and
 * never behind words. Art is page-owned now (the hero on Home, classroom on the
 * learn empty state, gears on Settings, brain on the mind check, flower on the
 * signed-out welcome), so nothing floats at the bottom of a scrolling page.
 */
export function PageShell({ children, access = false }: { children: ReactNode; access?: boolean }) {
  return (
    <div className={`page-frame${access ? " page-frame-access" : ""}`} data-page-shell>
      <div className="pattern-zone pattern-left" data-pattern="left" aria-hidden="true">
        <div className="topo-fill" />
      </div>
      <div className="page-sheet" data-sheet>
        {children}
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
