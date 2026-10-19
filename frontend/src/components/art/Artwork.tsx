/**
 * Line art, tinted with the accent. Each file is a transparent mask (the ink is the
 * alpha channel), so the colour always comes from the theme and never from the file.
 * `ART_FILES` is the one place that knows which file a name points at and how wide it
 * is, so layout can reserve the right box before the image has loaded.
 */
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
  | "split-cards"
  | "balance-scale"
  | "road-ahead"
  | "terrain-map";

export const ART_FILES: Record<ArtName, { src: string; w: number; h: number }> = {
  brain: { src: "/art/brain.svg", w: 1081, h: 992 },
  classroom: { src: "/art/classroom.svg", w: 1277, h: 816 },
  study: { src: "/art/study.svg", w: 1345, h: 784 },
  gears: { src: "/art/gears.svg", w: 1118, h: 944 },
  flower: { src: "/art/flower.svg", w: 800, h: 1315 },
  "desk-path": { src: "/art/desk-path.png", w: 950, h: 791 },
  "horizon-memory": { src: "/art/horizon-memory.png", w: 1231, h: 422 },
  "knowledge-tree": { src: "/art/knowledge-tree.png", w: 842, h: 829 },
  "open-book": { src: "/art/open-book.png", w: 514, h: 296 },
  "stepping-stones": { src: "/art/stepping-stones.png", w: 990, h: 732 },
  "split-cards": { src: "/art/split-cards.png", w: 1243, h: 373 },
  "balance-scale": { src: "/art/balance-scale.png", w: 766, h: 664 },
  "road-ahead": { src: "/art/road-ahead.png", w: 446, h: 252 },
  "terrain-map": { src: "/art/terrain-map.png", w: 497, h: 280 },
};

/** `scene` marks decoration that is not a page's illustration (the opening, the closing horizon):
 *  it is tagged `data-scene` instead of `data-art`, so "one illustration per page" stays true. */
export function Artwork({ name, className = "", scene = false }: { name: ArtName; className?: string; scene?: boolean }) {
  const file = ART_FILES[name];
  const url = `url(${file.src})`;
  return (
    <div
      {...(scene ? { "data-scene-art": name } : { "data-art": name })}
      aria-hidden="true"
      className={`artwork ${className}`}
      style={{
        maskImage: url,
        WebkitMaskImage: url,
        aspectRatio: `${file.w} / ${file.h}`,
        pointerEvents: "none",
      }}
    />
  );
}
