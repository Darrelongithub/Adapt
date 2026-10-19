import { Artwork } from "../art/Artwork";
import { SCENE_ART } from "./artMap";

/**
 * A line of ideas along a horizon, closing every signed-in page. It lives in the normal
 * flow after the page's last card, so it can never sit under text or float over a
 * scrolling page (the old fixed bottom-left art did both).
 */
export function PageScene() {
  return (
    <div className="page-scene" data-scene="horizon" aria-hidden="true">
      <Artwork name={SCENE_ART} scene />
    </div>
  );
}
