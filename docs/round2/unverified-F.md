# Unverified — Phase F (baseline games and subject round)

Things phase F did not check, or checked only in part. Each one says why.

## Environment

* **Chromium only.** The suite ran on Chromium 153 (`/home/user/.cache/chromium-bin/chromium`, from the `@sparticuz/chromium@153` npm package, which needs `LD_LIBRARY_PATH=/home/user/.cache/chromium-bin/lib`). `/opt/pw-browsers/chromium` does not exist in this sandbox, and `playwright install` was not run. Firefox and WebKit were not run.
* **Emulated phone, not a device.** The mobile project is Chromium with a 390×844 viewport, touch and device-scale 2. No real phone, no iOS Safari, no Android Chrome.
* **Local runs, not CI.** The gate was run by hand in this sandbox (two runs in a row, exit 0). No CI pipeline was run.
* **Reduced motion.** The suite sets `reducedMotion: "reduce"`, so the vault's flashing is shorter and may not match what a learner who has motion on sees. The vault "flash" frame itself is not captured in any screenshot.

## Scope of the browser suite

* **Keyboard-only is for the baseline only.** The keyboard project plays every baseline game with Tab, arrows, Enter, Space and typing. The subject round is not played keyboard-only.
* **Subject round uses the mouse on both viewports.** It is not played by keyboard, and it is not checked for screen-reader behaviour.
* **Screen readers.** Nothing was run with a screen reader. The `aria-pressed` buttons and labels were checked from the DOM by the tests, not by listening to them.
* **Stopped sittings.** The honesty spec checks "stop straight away" and "stop part-way" on the desktop only (it is a wording and structure check). It is not repeated on mobile.

## Numbers the suite cannot vouch for

* **Pace is an artifact of automation.** The Playwright taps are far faster than a person's. In the gate runs the snapshot reported a median quick tap of about 50–74 ms, a drift of about +189% to +215%, and "pace: short blocks". The numbers match what was sent; they say nothing about a real learner's pace. The pace measurement has not been checked against a human-speed run.
* **Attention and confidence.** In the snapshot that was reviewed, "confidence prompting" stayed at its default (off) with "no calibration evidence yet", and "Knowing what you know" read "not measured". The suite does not assert either value on every run. The attention band is clamped at ±0.1, and whether the bands are sensible for real learners is not verified.
* **The snapshot's copy is not reviewed for every learner.** The ranges and n are checked; the wording is read by a person only in the screenshots (see below).

## Subject round

* **Model-rated subjects are not exercised.** The suite runs with no model configured, so the subject plan takes the fallback (`generic_fallback`, labelled "fallback") or the authored history plan. The path where a model rates the skills (`model_note` set from a real answer) is covered only by unit tests in `test_subjects.py`, not end to end.
* **Pop-up lessons.** The round says the pop-up lesson was skipped because no authored pair exists for the topic. Whether authored pairs exist for other subjects was not checked.

## Known defects not fixed

* **Cue shown twice in cued practice.** In the delayed-recognition and cued screens the cue word appears as the cue line and again in the prompt ("Quilo" then "Recognise: which place went with Quilo?"). It is cosmetic and was left alone.
* **Missing full stop in the snapshot copy.** "...blocks can stay long On the two questions where the obvious answer is wrong" runs two sentences together. The string comes from the baseline service's `statement` text. Not changed in this phase.
* **`model_note` is in the subject API.** It is not rendered on any page, but its name and wording mention "the model". `test_subjects.py` pins the wording, so it was not renamed. See `handoff-F.md`.

## Screenshots

* **Reviewed by a person, but not every frame.** The screenshots in `docs/screens/F/{desktop,mobile,keyboard}/` (120 files, about 32 MB) were checked by viewing about 20 of them, covering the questions, vault, shopkeeper, locked box, snapshot, delayed recognition and the subject map, on desktop and mobile. Machine practice, trap, predict and the keyboard set were not each viewed by a person.
* The first review found that the baseline's choices and inputs had no styling, so they ran together. That was fixed (`frontend/src/app/baseline/baseline.css`) and the affected screens were re-checked.
