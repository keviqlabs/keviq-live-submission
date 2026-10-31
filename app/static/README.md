# app/static

Static files for the seller console and the broadcast overlay. The API serves this directory
flat (`/static/<name>`), so there are no subfolders.

| File | Role |
| --- | --- |
| `shared.css` | One stylesheet for the console, the overlay and the session list. Orange is reserved for primary actions; system font; `prefers-reduced-motion` respected. |
| `i18n.js` | The EN/VI dictionary and the language helpers. Every visible string lives here. |
| `console-render.js` | Renderer: builds HTML from the console state, writes it to the DOM, records paint timestamps. |
| `console.js` | Data adapter: SSE stream with cursor resume, event handlers, seller actions, boot. |
| `overlay.js` | Overlay: newest live card, price-change flash, hide button, `?size=`. |
| `placeholder.svg` | Product illustration used when the console has no usable product photo. |

## Language

`?lang=vi` or `?lang=en` picks the language for one page load. The VI | EN toggle in the console
header switches every label at once, stores the choice in `localStorage` (`keviq.lang`) and
carries it into the overlay link. With nothing stored the default is English. Product names,
prices, SKUs and customer text come from events and are never translated. The session list at `/`
stays English and is outside the switch.

Known order reasons are translated in the console and their amounts use the same currency format as
the cards. An unrecognized reason is shown unchanged so information is not lost. Known event sources
have translated labels; unknown source values remain visible as received. Technical identifiers and
reference details remain available in the source details and order log.

## Overlay

`/overlay/<session>` draws the newest live card only: right side, upper-middle third of a 9:16
source. `?size=30|36|43` sets the card width in percent of the source width (default 36). When the
price of the card on screen changes, the old price is struck through for three seconds and a toast
appears under the card. The hide button is browser-local: it does not change any server state.

## Honesty rules on screen

- The mode group is hidden while the API does not expose a known run mode; the simulation strip
  remains visible whenever the mode is not known to be LIVE. The event source is shown separately.
- The header clock is local machine time, not the replay or session clock. Increasing replay speed
  does not change this clock.
- Missing, simulated or failed product photos use an illustration labeled as such. The illustration
  does not claim to show the actual product or a generated cutout.
- Spark / SAM / Voice status comes from `model_call` events only: a dot for the last result and a
  p50 with `n` once at least one call was measured, otherwise `—`.
- Model cost comes from `/api/cost`: warning at the daily warning line, blocked at the daily cap.
- Each card records when its event arrived and when it was painted (`data-received-ms`,
  `data-painted-ms`, `window.__renderTimings`). No latency figure is rendered from these values.

## Tests

`node --test tests/console_*_ui.mjs` runs all five console test files against a simulated DOM
(`tests/console_harness.mjs` loads the three console scripts into one context).
