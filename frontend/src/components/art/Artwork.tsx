/** Cached external masks preserve currentColor without inlining traced paths in JS. */
export type ArtName = "brain" | "classroom" | "study" | "gears" | "flower";
export function Artwork({
  name,
  className = "",
}: {
  name: ArtName;
  className?: string;
}) {
  return (
    <div
      data-art={name}
      aria-hidden="true"
      className={`artwork ${className}`}
      style={{
        maskImage: `url(/art/${name}.svg)`,
        WebkitMaskImage: `url(/art/${name}.svg)`,
        pointerEvents: "none",
      }}
    />
  );
}
