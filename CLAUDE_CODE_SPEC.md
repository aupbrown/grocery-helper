# Grocery Helper — Coach UI: Implementation Spec for Claude Code

**Goal:** rebuild the frontend of the existing Flask/Jinja app (`grocery-helper/`) to match the **Coach** design.
**Stack constraint:** server-rendered Jinja templates + one plain CSS file + one small vanilla-JS file. **No React, no build step, no dependencies.**
**Visual source of truth:** `Coach UI.dc.html` — screens A1–A16 (anchors `#a2`…`#a16` inside that file). Where this document and the mockups disagree on pixels, the mockups win; where mockups are silent on behavior, this document wins.

---

## 0. Repo change map

| Existing file | Action |
|---|---|
| `app/templates/form.html` | Split into `home.html` (A1) + `wizard_basics.html` (A2) |
| `app/templates/_kitchen_fields.html` | Rework into `wizard_kitchen.html` (A3) — keep its field names/data model |
| `app/templates/targets.html` | Rebuild as A4 (incl. loading state) |
| `app/templates/results.html` | Rebuild as `plan.html` (A5) + partials |
| `app/templates/saved_plan.html` | Rebuild reusing plan partials; add repair variant (A15) |
| `app/templates/account.html` | Rebuild as A8 + empty state (A14) |
| `app/templates/login.html` | Rebuild as A11 |
| `app/templates/settings.html`, `pantry.html` | **Delete**; 301 both routes → `/plan/new?step=2&return=account` |
| `app/templates/thanks.html` | Delete (feedback becomes a dialog, A9) |
| `app/static/styles.css` | Replace wholesale (§2) |
| `app/static/ui.js` | Replace wholesale (§6) |

New templates: `generating.html`, partials `_summary_band.html`, `_meal_card.html`, `_meal_detail.html`, `_shopping_list.html`, `_save_sheet.html`, `_feedback_dialog.html`, `plan_failed.html` (A13).

## 1. Design tokens

```css
:root {
  /* canvas */
  --bg: #F7F8F5; --surface: #FFF; --border: #E4E8E0; --hairline: #F3EDE4;
  /* text */
  --ink: #2B2420; --muted: #6B6158; --faint: #8A7D71;
  /* accent (Fresh teal) */
  --accent: #2F8E7B; --accent-dark: #22615A;
  --accent-tint: #E0F0EC; --accent-tint-border: #BFDCD4;
  --accent-glow: rgba(47,142,123,.45);
  --band: #22403A;                       /* plan-page summary band */
  /* warning */
  --warn: #7A4A1E; --warn-tint: #FAEDDC; --warn-border: #E8C89E;
  /* misc */
  --dashed: #D0C4B4;
  --r-card: 24px; --r-mid: 16px; --r-sm: 14px; --r-pill: 999px;
}
```

**Type:** headings/buttons `Bricolage Grotesque` 700/800 (self-hosted woff2 in `app/static/fonts/`, `font-display: swap`); everything else the system stack. H1 `clamp(1.75rem, 5vw, 2.25rem)`. Inputs ≥16px (prevents iOS zoom). `font-variant-numeric: tabular-nums` on every money/macro figure.

**Shape & elevation:** cards `--r-mid`…`--r-card`; buttons/chips/badges `--r-pill`; sheets 24px top radius. Box-shadow only on primary CTAs (`0 6px 16px -6px var(--accent-glow)`) and sheets/modals.

**Copy tone:** warm second person, concrete numbers, light emoji ("Hey, hungry person 👋", "$2.14 to spare", "That one stumped us"). Links: `a { color: var(--accent-dark) }`, hover `#1A4A44`.

## 2. CSS component inventory (`styles.css`, in this order)

1. Reset + tokens + type scale + `a` colors + `:focus-visible` (2px `--accent-dark` ring, offset 2px)
2. `.btn` (pill, `--accent`, white Bricolage 800, min-height 48px) + `.btn-dark` (ink bg) + `.btn-secondary` (white bg, `--accent-tint-border` border, `--accent-dark` text) + `.btn-ghost` (transparent, dashed `--dashed` border, `--faint` text) + `.btn-warn` (`--warn` bg)
3. `.chip` — `<label>` + visually-hidden checkbox/radio; pill; `:has(:checked)` → `--accent-tint` bg + `--accent` border (`.chip-own` variant: ink bg, white text, for pantry items)
4. `.option-card` — card-size radio chip (goal, kitchen presets): emoji, bold label, optional caption; grids of 2–3
5. `.progress-steps` — 3 flex spans; filled = `--accent`
6. `.slider-row` — range input (`accent-color: var(--accent)`) + big `<output>` + caption line
7. `.unit-input` — bordered flex wrapper, borderless number input + unit span
8. `.stat-edit` — number input as display type, `border-bottom: 2px dashed`
9. `.meal-card` — grid `auto 1fr auto`: 44px icon tile (`--accent-tint`), eyebrow (11.5px caps, `--accent-dark`), name (Bricolage 700), stats line; modifiers `.is-dim` (opacity .55) and `.is-warn` (warn border + warn reason line)
10. `.action-card` — meal-card skeleton for recovery options (A12)
11. `.stat-chip` — small pill (kcal / protein / time / $)
12. `.summary-band` — `--band` bg, eyebrow, display figure + "of $40", stat-chip row; `.is-warn` variant (`--warn` bg)
13. `.tabs` — two pill `role="tab"` buttons; selected = ink bg white text
14. `.sheet` — `<dialog>` bottom sheet: drag-handle bar, slide-up transform animation; ≥768px becomes centered modal (max-width 420px)
15. `.check-row` — shopping list label grid: 22px checkbox, name + pack size, price; `:has(:checked)` → strike-through + muted
16. `.ingredient-row` — flex, name (+ badge) left, qty right, `--hairline` divider
17. `.badge` — mini pill: tint (`from your shelf`, `✓ ready`) and warn-tint (`needs fixes`) variants
18. `.callout` — warn-tint card: bold title, body, CTA (A15)
19. `.empty-card` — dashed border, centered emoji/title/caption/CTA (A14)
20. `.action-bar` — `position: sticky; bottom: 0`, top gradient fade to `--bg`
21. `.avatar` — 34px tinted circle, initial

## 3. Routes & screens

| Route | Template | Mockup | Notes |
|---|---|---|---|
| `GET /` | `home.html` | A1 | Landing: logo bar + login link, greeting, H1, 3 benefit rows, CTA → `/plan/new?step=1`, "free · ~1 min · no account" |
| `GET/POST /plan/new?step=1` | `wizard_basics.html` | A2 | Progress 1/3. Budget slider ($20–120) with live `$` output + per-meal caption; goal option-cards; weight + cook-time unit inputs; activity pills. POST → session → step 2 |
| `GET/POST /plan/new?step=2` | `wizard_kitchen.html` | A3 | Progress 2/3. Kitchen preset cards 2×2; equipment chips in `<details>`; pantry ingredient chips — checking one reveals inline amount row (number + unit select); "+ something else" free-text row; diet/allergies `<details>`; "Skip, I'll use defaults" link. Pre-filled for logged-in users; `?return=account` posts back to `/account` |
| `GET/POST /plan/new?step=3` | `targets.html` | A4 | Progress 3/3. Calories hero card + 3 tinted macro cards, all `.stat-edit` inputs; helper line (protein hard, calories soft); CTA POSTs to `/plan/generate` |
| `POST /plan/generate` → `GET /plan/generating` | `generating.html` | A4 lower | See §5 |
| `GET /plan` | `plan.html` | A5 / A12 | Summary band (warn variant + recovery cards when over budget), tabs Budget/Protein-first, meal cards, ghost swap button, sticky action bar (Shopping list · $ / Save week) |
| — sheets on `/plan` | `_meal_detail.html`, `_shopping_list.html`, `_save_sheet.html` | A10 / A6 / A7 | Server-rendered `<dialog>`s in the page, opened by JS; no-JS fallback: same content at `/plan#list` etc. as visible sections |
| `GET /plan/failed` | `plan_failed.html` | A13 | Inputs preserved in session; Try again re-POSTs generate |
| `GET/POST /login` | `login.html` | A11 | Login only + "Plan a week first" secondary CTA |
| `GET /account` | `account.html` | A8 / A14 | Welcome, pantry line + edit link, "Plan a new week" (→ step 3 directly, prefilled), saved week cards with badges; empty state A14 |
| `GET /plans/<id>` | `saved_plan.html` | A5 minus tabs / A15 | If backend validation fails: A15 callout + highlighted meals |
| `POST /plans/<id>/repair` | — | A15 | Re-solve keeping valid meals pinned; redirect back |
| `POST /regenerate` | partial | A5/A10 | Returns `_meal_card.html` partial for fetch swap-in; full page without JS |
| `POST /feedback` | — | A9 | `{rating, comment, newsletter_optin, email?}` |

Old `/settings`, `/pantry` → 301 to `/plan/new?step=2&return=account`.

## 4. Flows

- **First-time:** A1 → A2 → A3 (skippable) → A4 → generating → A5 (or A12/A13) → A10 details & swaps → A6 list → A7 save+account → saved view → A9 feedback (once).
- **Returning:** `/account` → "Plan a new week" lands on A4 (basics + pantry prefilled from DB; edit links go back to A2/A3) → A5 → Save is one tap (no A7).
- **Over budget (A12):** three action-card `<form>` POSTs — `swap_priciest`, `relax_protein` (−10g), `raise_budget` (+$5) — each mutates stored inputs and re-queues generation; plus "keep this plan anyway" link. Reuse the generator's `_over_budget_feedback` re-prompt machinery.
- **Repair (A15):** single primary "Fix it for me" POST; problem meals get warn styling with plain-language reason, valid meals dim.
- **Feedback trigger:** render `_feedback_dialog.html` only on the first page-load after a user's first save (server flag on the user/session); JS `showModal()` after 800ms; submitting or dismissing sets the server flag and `localStorage.fb_done`. Never re-shown.

Every step is POST → redirect → GET; back/refresh always safe. JS enhances, never gates.

## 5. Plan generation (async)

Current generator is a synchronous blocking call. Change to:
1. `POST /plan/generate` — snapshot inputs, spawn a thread (or job row), redirect to `/plan/generating`.
2. `generating.html` — spinner + "Building your week…" + rotating status lines (fixed list, swap every 4s) + "usually ~20 seconds"; `role="status" aria-live="polite"`. JS polls `GET /plan/status` every 2s.
3. `GET /plan/status` → `{"state": "queued"|"running"|"done"|"failed", "over_budget": bool}`. `done` → JS location `/plan`; `failed` → `/plan/failed`.
4. No-JS fallback: `<meta http-equiv="refresh" content="3">` on the generating page; server redirects when done.

Swap/regenerate stays synchronous but scoped: the affected `.meal-card` gets a skeleton-shimmer class while `fetch('/regenerate')` runs.

## 6. JavaScript (`ui.js`, ~200 lines, no deps)

1. Dialog helpers: open/close for `.sheet`s and the feedback modal (`showModal()`, backdrop + ESC close, focus return to opener)
2. Tabs: click + arrow keys, `aria-selected`, panel toggling (no-JS: both panels visible stacked)
3. Budget slider → `<output>` + per-meal caption (`$X ≈ $Y per meal — tight but doable 💪`)
4. Pantry chip → reveal amount row; "+ something else" → append free-text row
5. Generation polling (§5)
6. Shopping-list checkbox persistence: `localStorage["list:" + planId]`
7. "Share / copy list": `navigator.clipboard.writeText(plainTextList)` with `<textarea>` fallback; button flashes "Copied ✓"
8. Feedback dialog trigger + flag
9. Meal-swap fetch + partial swap-in

## 7. Responsive

- ≤640px: single column (as mocked, 390px frames).
- ≥768px: content column 640px; option-card grids 3–4 across; sheets become centered modals.
- ≥1024px (**A16**): header gains "Saved weeks" nav + avatar; `/plan` becomes `grid-template-columns: 1fr 340px` — meal cards 2-up left, shopping list a sticky right rail (`align-self: start; position: sticky; top: 24px`) containing the Save CTA; `.action-bar` hidden. Landing two-column. Saved plans 2-up. Wizard stays single column (max 560px).
- Hover styles inside `@media (hover: hover)` only.

## 8. States checklist

- Loading: generating page (§5); swap shimmer; buttons `disabled` + `aria-busy` while posting.
- Empty: A14 saved-weeks card; A14 pantry card with starter chips.
- Over budget: A12 warn band + 3 recovery cards + keep-anyway link — never a dead end.
- Failure/timeout: A13 — friendly copy, inputs preserved, Try again.
- Repair: A15.
- Validation: inline below field, warn color, `aria-describedby`, never clear user input.
- Reduced motion: spinner → pulsing dots; no sheet slide; status lines still rotate (text swap only).

## 9. Accessibility (non-negotiable)

- Landmarks + one `<h1>`/page; native forms; all chips/option-cards are real inputs + labels.
- Visible `:focus-visible` ring everywhere interactive; touch targets ≥44px.
- Contrast: small text/links use `--accent-dark`, never `--accent`; warn text `--warn` on `--warn-tint`; verify all pairs ≥4.5:1 (large/bold ≥3:1).
- Emoji `aria-hidden="true"` with text alongside; A9 rating buttons get `aria-label`s.
- Native `<dialog>` semantics; tabs with full keyboard support; wizard `role="progressbar"` + visually-hidden "Step n of 3"; loading `role="status"`.

## 10. Build order & acceptance

**Milestones:**
1. Tokens + styles.css core components; home + wizard (A1–A4) with session round-trip
2. Async generation (§5) + plan page + sheets (A5, A10, A6)
3. Save/account/login/saved plans (A7, A11, A8, A14)
4. Edge states (A12, A13, A15) + feedback dialog (A9)
5. Desktop layout (A16) + a11y sweep + reduced-motion

**Accept when:**
- Full anonymous flow works with JS disabled (stacked panels/sections instead of tabs/sheets; meta-refresh loading)
- Full flow works on a 390px viewport with JS on; plan page matches A5/A16 at 390/1024
- Keyboard-only run-through of the entire flow succeeds; axe (or similar) reports no criticals
- Over-budget, failure, empty, and repair states all reachable and match A12–A15
- Lighthouse a11y ≥ 95; no console errors; total JS < 10KB unminified
