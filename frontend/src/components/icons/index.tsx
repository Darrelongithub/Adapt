import type { ReactNode } from "react";

const paths = {
  play: <path d="m9 5 11 7-11 7Z" />,
  route: (
    <>
      <path d="M12 3v18M5 5h13l3 3-3 3H5l-3-3ZM7 13h12v6H7l-3-3Z" />
    </>
  ),
  sliders: (
    <>
      <path d="M4 6h16M4 12h16M4 18h16" />
      <path d="M8 3v6m8 0v6M10 15v6" />
    </>
  ),
  bars: (
    <>
      <path d="M4 20V10m8 10V4m8 16v-7M2 21h20" />
    </>
  ),
  clock: (
    <>
      <circle cx="12" cy="12" r="9" />
      <path d="M12 6v6l4 2" />
    </>
  ),
  message: (
    <>
      <path d="M20 11V4H4v13h4v4l5-4h3m0-3 5-5 2 2-5 5-3 1Z" />
    </>
  ),
  key: (
    <>
      <circle cx="8" cy="8" r="5" />
      <path d="m12 12 9 9m-3-3 3-3m-6 0 3-3" />
    </>
  ),
  search: (
    <>
      <circle cx="10" cy="10" r="6" />
      <path d="m15 15 6 6" />
    </>
  ),
  gears: (
    <>
      <path d="m9 3 1 3h4l1-3 3 2-1 3 2 3 3 1-1 4-3-1-3 2-1 4h-4l-1-4-3-2-3 1-1-4 3-1 2-3-1-3Z" />
      <circle cx="12" cy="12" r="3" />
    </>
  ),
  coin: (
    <>
      <circle cx="12" cy="12" r="9" />
      <path d="M15 8h-4a2 2 0 0 0 0 4h2a2 2 0 0 1 0 4H9m3-10v12" />
    </>
  ),
  box: (
    <>
      <path d="m3 7 9-4 9 4v12l-9 3-9-3Zm0 0 9 4 9-4M12 11v11m-5-17 10 4v5" />
    </>
  ),
  maths: (
    <>
      <path d="M3 6h7M6.5 3v6m8-3h7M3 15l7 7m0-7-7 7m11-6h7m-7 5h7" />
    </>
  ),
  language: (
    <>
      <path d="M3 4h12M9 2v2m-4 0c0 6 5 9 9 10M13 4c0 5-5 10-10 11m11 6 4-11 4 11m-6-4h4" />
    </>
  ),
  science: (
    <>
      <path d="M9 3h6m-5 0v6L4 19a1 1 0 0 0 1 2h14a1 1 0 0 0 1-2L14 9V3M7 15h10" />
      <circle cx="11" cy="17" r=".5" />
    </>
  ),
  history: (
    <>
      <path d="m3 8 9-5 9 5H3Zm2 3v7m7-7v7m7-7v7M3 21h18" />
    </>
  ),
  home: (
    <>
      <path d="m3 11 9-8 9 8M5 9v12h14V9M10 21v-7h4v7" />
    </>
  ),
  book: (
    <>
      <path d="M12 5v16m0-16C9 3 5 3 2 4v15c3-1 7-1 10 2 3-3 7-3 10-2V4c-3-1-7-1-10 1Z" />
    </>
  ),
  mic: (
    <>
      <rect x="9" y="2" width="6" height="13" rx="3" />
      <path d="M5 10v2a7 7 0 0 0 14 0v-2M12 19v3m-4 0h8" />
    </>
  ),
  compare: (
    <>
      <path d="M3 4h7v16H3Zm11 0h7v16h-7M6 8h1m10 0h1M6 12h1m10 0h1" />
    </>
  ),
  chevron: <path d="m6 9 6 6 6-6" />,
  refresh: (
    <>
      <path d="M20 8a8 8 0 1 0 0 8m0-13v5h-5" />
    </>
  ),
  plus: <path d="M12 5v14M5 12h14" />,
} satisfies Record<string, ReactNode>;
export type IconName = keyof typeof paths;
export function Icon({
  name,
  className = "",
}: {
  name: IconName;
  className?: string;
}) {
  return (
    <svg
      className={`monoline-icon ${className}`}
      width="24"
      height="24"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.8"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      focusable="false"
      style={{ flexShrink: 0 }}
    >
      {paths[name]}
    </svg>
  );
}
export const routeIcon: Record<string, IconName> = {
  "/": "home",
  "/learn": "book",
  "/lesson": "book",
  "/first-lesson": "book",
  "/review": "clock",
  "/progress": "bars",
  "/proposals": "message",
  "/baseline": "play",
  "/subject": "maths",
  "/contrast": "compare",
  "/evidence": "search",
  "/voice": "mic",
  "/settings": "sliders",
  "/journey": "route",
  "/diagnose": "play",
  "/onboarding": "sliders",
};
export function gameIcon(game: string): IconName {
  return game === "vault"
    ? "key"
    : game.startsWith("codewords")
      ? "search"
      : game === "machine"
        ? "gears"
        : game === "locked_box"
          ? "box"
          : game === "shopkeeper"
            ? "coin"
            : "play";
}
