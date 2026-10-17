# Q → Y: iconography and wayfinding

## Delivered
- `components/icons/index.tsx`: consistent 24px React SVG set, 1.8px monoline strokes, round caps/joins, currentColor, `aria-hidden` and non-focusable.
- Shared Btn `icon` slot retains text labels; Home actions use play, route, sliders, bars, clock, speech-pen. Sidebar replaces numeric prefixes with destination icons and a matching chevron disclosure: **More destinations / Fewer destinations**. Interactive targets have a 44px minimum and visible keyboard focus.
- `GameCards.tsx`: key, magnifier, gears, coin, box cards under **See the games** on the format screen. These are explicitly previews, not fake navigation; the server still sequences the real sitting.
- Subject shortcuts use maths, language, science, history icons; “Your path” uses a signpost and “Build my path” a route. Format choices use the same set.

## Y integration applied
Shared chrome/nav and Home/sidebar labels; baseline GameCards and format icons; subject shortcut mount; shared shell focus/tap-target CSS. Browser route screenshots capture desktop/mobile navigation, Home, subjects and format cards. The legacy full-baseline keyboard suite checks real Tab/arrow/Enter/Space behavior and single-send trial receipts, without weakening its ledger assertions.
