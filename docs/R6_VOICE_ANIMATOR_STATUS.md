# R6 — voice and animator: what is real, what is not

Written so nobody has to trust a claim. Every row says where the code lives and how it was
checked in this round. Nothing here is a stub that pretends to work; where an engine is not
installed in a deployment, the product says so on the face of the UI (the voice console tags
read "listening: in the browser" / "speaking: in the browser" rather than pretending a local
engine is running).

## Animator — the interactive scene surface

| Thing | State | Where | Checked by |
| --- | --- | --- | --- |
| Template library (23 templates, closed parameter schemas, bounds, no markup/code) | **real** | `backend/src/adapt/learning/template_library.py` | `tests/test_track_f_scenes.py`, `tests/test_scenespec.py` (268 tests) |
| Drawing vocabulary: shapes (circle/rectangle), **person**, **car**, **triangle**, **number line** (ticked line), **graph** (curve + node network) | **real** (R6 added `car`, `person`, `graph` frames so each template looks like its subject) | `_frame()` in `template_library.py`; drawn by `components/animator/index.tsx` | `test_track_f_scenes.py::test_the_animator_six_kinds_cover_the_whole_library` |
| AI 3 builds **4 lessons' scenes** from the library (triangle, car, number line, graphs lesson) | **real** — a lesson writer may only name a template + bounded params; the validator dry-runs to frames before any browser sees it | `validate_and_dry_run()`; `learning/scene_api.py` | `test_track_f_scenes.py::test_four_lessons_build_their_scenes_from_the_template_library` |
| Determinism: same spec → same frames; resuming a lesson after a restart replays the identical trace | **real** | `template_library.dry_run`, `sims.frames_for` | `test_track_f_scenes.py::test_frames_are_deterministic_and_carry_no_code` |
| **4 simulations** (binary search, insertion sort, logic gates, CPU cycle) | **real** — application code computes every step; a model may narrate, never decide | `backend/src/adapt/learning/sims/` (4 modules, ~same shape each: `run`/`explain`) | `tests/test_sims.py` + `test_track_f_scenes.py` (7 parametrised checks) |
| No answer leaks in prediction prompts | **real** — the trace never carries the answer to its own prompt; the check re-runs the sim | `sims/__init__.py` (`expected_after`) | sims tests; `test_each_simulation_validates…` |
| Scene player (play/pause/step/scrub/speed, keyboard, respects reduced motion) | **real** | `frontend/src/lib/engine/player.ts` + `/lesson` page | `engine/player.test.mjs`; browser run |
| Sim stages (the 4 sims draw as arrays, cards, gates, CPU) + narration fallback for unknown kinds | **real** | `frontend/src/components/scenes/stages.tsx` | lint contract (kinds agree across engine/stages/mock) |
| AI 3 "why did that happen?" answers | **real** — deterministic `explain()` first (source `simulation`), cache, then model (source `llm`); the source is on the payload | `ai/scene_narrator.py` | existing AI-3 tests |

## Voice

| Thing | State | Where | Checked by |
| --- | --- | --- | --- |
| Greeting on sign-in/entry | **real** — "Welcome back, {name}. Ready to continue your learning?" then listens 7s | `chrome.tsx` `VoiceGreeting` | browser run (silent with voice off) |
| Read-aloud | **real** — on every lesson frame (title + narration) and every review question; plus the voice console | `components/voice/ReadAloud.tsx`, `/lesson`, `/review` | code path + silent-when-off no-op |
| Listen-and-answer | **real** — the voice console runs the full loop (greeting → read-aloud → dictate → graded answer); the greeting itself listens for a reply | `/voice` page, `voice/context.tsx` (`listenOnce`, `onCommand`) | code path; mic needs a secure context (https/localhost) — the console says so on plain http |
| Toggle app-wide, **silent when off** | **real** — sidebar switch; `speak`/`listenOnce` resolve immediately without making a sound when disabled; server stops synthesising too | `VoiceControl` in `chrome.tsx`; `post("me/voice")` persists it | `speak()` early-return contract; browser run |
| Browser speech (speechSynthesis + SpeechRecognition) | **real** — these are the platform APIs, not a mock; they work with no server and nothing is uploaded | `voice/context.tsx` | runs in Chromium |
| Server voice pipeline (VAD → STT → TTS, binary websocket + REST fallback, editable transcripts) | **real code, optional engine** — `backend/src/adapt/voice/` (audio/vad/stt/tts/pipeline ≈ 1430 lines). The engine dependency is an extra (`pip install 'adapt-backend[voice]'`, pulls torch ~2 GB). In the Round-6 sandbox the engine is **not importable**, so the app says "in the browser" and uses the platform APIs. This is a deployment state, not a stub. | `voice/pipeline.py`, `api/routes/system.py` (`/voice/capabilities`, `/voice/transcribe`, `/voice/speak`, `/ws/voice`) | capability payload reports `local_stt`/`local_tts` truthfully; `/voice` console shows the tags |

## Not claimed

- No voice cloning, no singing, no music: speech is text-to-speech and dictation only.
- No local STT/TTS quality claim in this sandbox — the engine is not installed here. With the
  extra installed, `/voice/capabilities` flips the tags to "on this machine" and the pipeline
  serves them; nothing in the UI changes code paths.
- Microphones require a secure context (https or localhost); on a plain-http LAN address the
  listen path is disabled by the browser and the console says so.

## The Round-6 additions (this branch)

- Frame vocabulary: `car` (car-motion), `person` (grammar-sentence-diagram), `graph`
  (function-plot curves) — templates now draw what they name; number-line/timeline frames
  carry ticks.
- `ReadAloud` on `/lesson` frames and `/review` questions.
- `backend/tests/test_track_f_scenes.py` — 8 tests pinning the four-lessons contract,
  the six drawing kinds, determinism, and the 4 sims' validation/determinism/no-leak.
