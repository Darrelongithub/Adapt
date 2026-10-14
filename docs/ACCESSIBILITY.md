# Accessibility and performance check

This is a small, repeatable audit for the lesson/player and baseline. It records what is
already enforced in code, separates automated checks from manual checks, and does not claim
a colour or device measurement that has not been run.

## Safeguards present in the repository

- Reduced-motion preference is read by the scene player. `autoplayAllowed()` returns false
  when `prefers-reduced-motion: reduce` is active, while the learner keeps the manual play,
  step, back, seek, and replay controls. The lesson page also avoids autoplay for reduced
  motion, and the global stylesheet reduces/removes selected animations and transitions.
- The navigation drawer uses a focus trap only while it is open; Escape closes it and returns
  focus to the button that opened it. Controls use native buttons, links, inputs, and labels.
- The baseline lever contract includes `timers_visible`; the existing backend baseline test
  asserts that its default is false. This is a regression check for the default, not proof that
  every future learner preference path is respected.
- The scene engine has deterministic state transitions and bounded frame processing, so a
  performance rehearsal can use the same script and scenes on each device.

These are implementation observations, not a WCAG conformance claim.

## Automated checks in this checkout

Run from `frontend/`:

```bash
npm run lint
npm run test:engine
npm run test:lib
npm run probe:mock
```

`npm run lint` includes the repository's route/mock/environment contract checker and TypeScript
checking. `probe:mock` exercises the current mock-backed browser flow. The baseline timer
regression is in `backend/tests/test_baseline.py`; run it with the backend suite.

## Manual keyboard and reduced-motion pass

1. In browser settings or the OS, enable reduced motion. Reload `/lesson` and `/learn`.
2. Verify no lesson auto-advances or scene starts playing without a learner action. Use each
   manual scene control with keyboard only.
3. Starting at the address bar, use Tab / Shift+Tab / Enter / Space through sign-in, consent,
   lesson navigation, answer controls, hints, and the scene player. Focus must remain visible.
4. Open the mobile navigation drawer; verify focus stays in the open drawer, Escape closes it,
   and focus returns to the opening control.
5. Use browser zoom at 200% and a 320 CSS-pixel-wide viewport. Confirm controls remain reachable
   and content does not require horizontal scrolling.
6. For a learner who turned timers off, verify no countdown, auto-submit, or time-pressure copy
   appears in the baseline. The current automated default assertion is not a substitute for this
   per-preference check.

Record browser, OS, viewport, and the exact page if any step fails. Do not describe an unrun
manual step as verified.

## Contrast and low-end phone pass

Readable contrast must be measured, not guessed from a palette. For each page used in the demo,
measure foreground/background pairs for body text, muted text, buttons, borders that convey
meaning, focus indicators, and error/status text with a contrast tool. Record the measured ratio,
colour pair, and whether the text is normal or large; target WCAG AA (4.5:1 for normal text and
3:1 for large text and meaningful graphical controls). No contrast ratios are claimed in this
file until that pass is run.

For performance, use a physical low-end phone (or a throttled browser profile) and the same
baseline flow: sign in, answer the first baseline game, move through a scene, and return to
practice. Record device/browser, CPU/network throttle, elapsed time, dropped frames, memory if
available, and whether input remains responsive. The sandbox has no installed Chromium/device
emulator, so a browser-level phone result is pending and must not be reported as passed here.
