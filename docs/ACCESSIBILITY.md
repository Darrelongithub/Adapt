# Accessibility

ADAPT is designed to be usable by every learner, including those using assistive
technology, low-end devices, or who have asked the app not to use timers.

## Keyboard operation

Every screen and every game in the baseline is operable by keyboard:

- **Tab / Shift+Tab** moves focus between interactive elements.
- **Enter / Space** activates the focused element.
- **Arrow keys** navigate within composite widgets (the vault grid, radio groups).
- **Escape** closes modals and returns to the previous screen.

The vault grid game (part of the baseline) is navigable with arrow keys + Enter.
Focus is managed with a roving tabindex pattern (see
`frontend/src/lib/a11y/useRovingTabIndex.ts`).

Focus is trapped inside modals and dialogs (see
`frontend/src/lib/a11y/useFocusTrap.ts`) so a screen reader never reads content
behind an open dialog.

## Reduced motion

When the user's operating system has `prefers-reduced-motion: reduce` set, the
app:

- Disables CSS transitions and animations.
- Shows lesson frames as static cards rather than animated transitions.
- Replaces slide/fade transitions with instant display changes.

This is implemented via `@media (prefers-reduced-motion: reduce)` in
`frontend/src/app/globals.css`.

## Readable contrast

All text meets WCAG 2.1 AA contrast requirements (4.5:1 for normal text, 3:1
for large text). The colour palette is tested against common colour-vision
deficiencies. Status indicators never rely on colour alone: every status
includes a text label or icon.

## No forced timers

**This is a hard rule, not a preference.** When a learner tells the baseline
that timers stress them (the `timers_help` question), all visible timers are
hidden throughout the app. The app still measures latency quietly for the
learning model, but the learner never sees a countdown.

Implementation:

- The baseline stores the preference as `timers_visible: false` in the levers.
- Every screen that shows a timer checks this lever before rendering it.
- The study-phase 45-second timer in the codewords game is replaced with a
  "Ready" button when timers are off.
- The locked box and machine games hide their timeout displays.

This is tested: `test_no_timers_forced_on_learner_who_asked_for_them_off`
asserts that a learner with `timers_visible: false` never receives a visible
timer in any game task.

## Screen reader support

- All interactive elements have accessible names.
- Live regions announce game state changes (correct/wrong, new frame, hint opened).
- Images carry alt text or are marked decorative.
- The lesson player announces frame transitions and prediction prompts.

## Low-end device performance

The baseline games are designed to run smoothly on a low-end phone:

- No WebGL or heavy canvas rendering.
- Animations use CSS transforms (GPU-accelerated) and are disabled under reduced motion.
- Asset sizes are minimised; fonts are system fonts by default.
- The voice pipeline processes audio in a worker thread so the UI stays responsive.

## Text sizing

The app respects the user's browser text-size setting. Layouts use `rem` units
and flex/grid so content reflows at 200% zoom without horizontal scrolling.

## Language

The language question in the baseline sets the language of every explanation.
The app supports English and Kiswahili, with the ability to add more languages
through the content engine.
