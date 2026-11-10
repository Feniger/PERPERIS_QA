# PERPERIS QA — family design system and workspace redesign

Status: milestone 1 of the redesign (application shell + Today dashboard), rebased onto the Cloud Foundation work (#1–#5). Frontend only: no backend, API, database, authentication or deployment change.

> **Input caveat.** The task named two reference screenshots (PERPERIS Today, WorkBuddy). They were **not attached** to the session that did this work, and the PERPERIS app needs a sign-in. The visual analysis below is therefore taken from the **PERPERIS source code** (`krummb/kattens-pt`: `src/styles.css`, `AppShell.tsx`, `velocity-page-title.tsx`, `nav-items.ts`), which is more exact than a screenshot. WorkBuddy was not available at all; the workspace layout follows the written brief (left work area about 60 %, right health overview about 40 %). Compare against the real screens before approving.

## 1. Visual analysis of PERPERIS (from source)

| Area | What PERPERIS does | Source |
|---|---|---|
| Display type | Barlow Condensed, weight 700, uppercase. Page title `clamp(3.5rem, 12vw, 6.5rem)`, line-height 0.78, letter-spacing 0. | `text-page-title` |
| Title block | Title sits on a 3 px ink rule. Optional right-aligned labels led by a pink `/`. | `VelocityPageTitle` |
| Text | Inter, weights 300–600. Eyebrow labels 10–13 px, uppercase, wide tracking (0.16–0.25 em). | `text-eyebrow`, `AppShell` |
| Readouts | Condensed, tabular numerals, light weight (200). | `num-display` |
| Canvas | Paper White, thin neutral borders, square controls, almost no shadow. | `.theme-v12` |
| Colour roles | Pink = featured energy only; Electric Blue = forward action, focus, circuit; Navy = structure; Acid Yellow = attention; Electric Green = confirmation. Ink text sits on pink and blue. | `.theme-v12` comments |
| Sidebar | 240 px, 1 px right border, "PERPERIS" in 13 px light with 0.25 em tracking, uppercase nav with a 2 px left marker on the active item. | `DesktopSidebar` |
| Mobile | Bottom navigation, 72 px targets, display-font labels. | `AppShell` |

## 2. Comparison with the previous QA UI

| | Previous QA UI | Now |
|---|---|---|
| Palette | Sage and soft orange, pastel chart colours | PERPERIS V12 tokens (same OKLCH values) |
| Shape | Rounded cards and pill chips | Square, thin rules, uppercase rows |
| Type | Mixed sans, sentence-case headings | Barlow Condensed display, Inter text |
| Layout | Single column of cards | Sidebar + 60/40 workspace |
| Claims | "LIVE DATA" tag, "Development health." | Headline is computed from data and never says quality is good without evidence |
| Mobile | Table scrolls sideways | Row cards, bottom nav, always-visible NEW BUG |

## 3. Design tokens

Exact production values are reused as OKLCH; hex is for reference only.

| Token | OKLCH | Approx. hex | Use |
|---|---|---|---|
| `--paper` | `0.973 0.006 106.5` | `#F7F7F3` | canvas |
| `--ink` | `0.145 0 0` | `#0C0C0C` | text, rules, active nav |
| `--surface-1/2` | `0.945 / 0.915 0.006 106.5` | `#EDEDE9 / #E3E3DE` | panels, hover |
| `--border` | `0.86 0.006 106.5` | `#D9D9D5` | thin dividers |
| `--muted` | `0.4 0.01 260` | `#45484D` | secondary text |
| `--tertiary` | `0.5 0.01 260` | `#606369` | metadata labels |
| `--navy` | `0.285 0.08 249.5` | `#062B4C` | structure, hero (attention) |
| `--blue` | `0.69 0.155 237.5` | `#18A8F5` | primary action, focus |
| `--pink` | `0.64 0.25 8.5` | `#FF0A5B` | critical, featured panel |
| `--yellow` | `0.918 0.204 112.2` | `#E8F000` | attention marker |
| `--green` | `0.819 0.211 150.1` | `#32E875` | verified marker |

The brief's approximate colours (`#F6F6F3`, `#090909`, `#FF0864`, `#03A8E9`) match these within rounding.

**Fonts.** PERPERIS uses Barlow Condensed and Inter from Google Fonts. The QA app's security policy (QA-CLOUD-05) allows fonts only from its own origin, and `app.py` serves only four named static files, so no web font is loaded. The stacks fall back to locally installed Barlow Condensed / Inter, then a system condensed face. Follow-up: vendor the two OFL font files and add them to the static allowlist in `app.py` and `.dockerignore` (a small backend change, kept out of this frontend PR).

**Accessibility rule.** Colour never carries meaning alone. Every status and severity has a text label; charts differ by line dash and marker shape and are labelled at the line end.

## 4. Component system (milestone 1)

- **Shell:** fixed sidebar (desktop), top bar + bottom navigation with a centre NEW BUG action (mobile).
- **Page title:** the PERPERIS title block with data labels (current round, ticket count).
- **Round strip:** one button per testing round from the data; the latest is selected first.
- **Hero panel:** one computed headline; background pink (critical open), navy (unverified backlog), ink (all verified), neutral (no data).
- **Metric strip:** six numbers (total, open, fixed, verified, critical open, backlog) with one-line definitions.
- **Quick report composer:** title, steps, severity, round, reporter. Plain form, posts to the existing `POST /api/tickets`. No AI. Screenshots go through **Add details**, which opens the full dialog.
- **Ticket dialog:** create or update; private screenshots (paste, drop, file picker) from QA-CLOUD-04; status options limited by role; Testers cannot change the assignee; edits send `expected_version` and show a clear message on a 409 conflict.
- **Sign-in and membership:** password mode, or managed sign-in with email, account registration and password reset (QA-CLOUD-03). Admins get a **Members** entry (sidebar, and the top bar on phones). The reporter is the signed-in member and read-only in managed mode.
- **Activity list:** latest ticket updates.
- **Bars:** severity distribution and ticket flow, with counts as text.
- **Trend chart:** found / fixed / verified per round, optional cumulative backlog, per-round crossover marker.
- **Attention list:** critical open, reopened, high open, awaiting verification, each with its reason as text.
- **Ticket register:** a real table (`#ticket-rows tr`): a 7-column grid on desktop, cards on phones; keyboard operable.
- **Buttons:** square; blue = primary, ink = secondary, pink = exceptional only.

## 5. Wireframes

Desktop (≥ 1024 px):

```
┌────────┬────────────────────────────────────────────────────┐
│PERPERIS│ TODAY                                  / R05       │
│ / QA   │ ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━  / 14 TICKETS│
│        │ [R01][R02][R03][R04][R05]                          │
│ TODAY  │ ┌────────────────────────────────────────────────┐ │
│ TICKETS│ │ 01 / QUALITY   2 CRITICAL OPEN.     [REPORT]   │ │
│ ·····  │ └────────────────────────────────────────────────┘ │
│ NEXT:  │ ┌───────────── 60 % ─────────┐┌──────── 40 % ────┐ │
│ WORK-  │ │ 02 / DISCOVERY             ││ 03 / RESOLUTION  │ │
│ SPACE  │ │ QUICK REPORT  (form)       ││ 6 metrics        │ │
│ ROUNDS │ │ RECENT ACTIVITY (8 rows)   ││ ROUND R05 panel  │ │
│ ANALYT.│ │                            ││ TREND chart      │ │
│ ·····  │ │                            ││ SEVERITY / FLOW  │ │
│ TEAM   │ │                            ││ NEEDS ATTENTION  │ │
│ SETTING│ └────────────────────────────┘└──────────────────┘ │
└────────┴────────────────────────────────────────────────────┘
```

Mobile (< 1024 px):

```
┌──────────────────────┐
│ PERPERIS / QA  SIGN OUT│
│ TODAY                │
│ ━━━━━━━━━━━━━━━━━━━━ │
│ [R03][R04][R05] →    │
│ HERO (compact)       │
│ QUICK REPORT         │
│ RECENT ACTIVITY      │
│ METRICS 2×3          │
│ TREND (scrolls →)    │
│ SEVERITY · FLOW      │
│ NEEDS ATTENTION      │
├──────────────────────┤
│ TODAY │ ＋ NEW BUG │ TICKETS │
└──────────────────────┘
```

## 6. Implementation breakdown

1. **Milestone 1 (this change):** shell, Today dashboard, restyled ticket register and ticket dialog. Reads `GET /api/analytics` and `GET /api/tickets`; writes through the unchanged create and update endpoints.
2. Milestone 2: ticket investigation view, built on the new `GET /api/tickets/{id}/history` (reproduction, expected and actual as structured fields).
3. Milestone 3: richer screenshot handling inside the investigation view (the storage, limits and access rules now exist from QA-CLOUD-04).
4. Milestone 4: testing rounds view and a real analytics view.
5. Later: team and settings (needs real accounts, see the README's security notes); an AI assistant only after proposals-only tooling exists.

Navigation entries for views that do not exist yet are shown **disabled and labelled NEXT**; they are not links and do nothing.

## 6a. Verification and a defect found on `main`

- **Browser checks:** 62 checks passed on two consecutive runs in Chrome: the original 39 (desktop 1440 px and phone 390 px; populated, empty and server-error states; quick report, ticket update, filters, keyboard, dialog focus, tokens, no horizontal overflow) plus the step sequences of `tests/browser_smoke.py` and `tests/identity_browser_smoke.py` (screenshot pick/paste/upload/delete, failed upload kept queued and retried, status update, sign out, no browser storage; managed sign-in, Developer/Tester status options, read-only reporter and assignee, verification, Admin membership change, disabled member refused) and a stale-edit 409 conflict.
- **How they ran:** against a throwaway stub of the API that mirrors the contract (the `X-QA-Request` guard, roles, versions and 409, attachments, members) and sends the **real Content-Security-Policy**, so script or policy violations would have shown. Synthetic `FIXTURE` data only. The stub is not in the repo. The real backend and its Python tests could **not** run in that environment (`fastapi`/`httpx` not installable); `app.py` is untouched.
- **Dialog defect:** the old dialog refused to save ticket updates because of hidden required fields. QA-CLOUD-03 fixed this on `main` independently; this redesign keeps the same approach (the inactive half of the dialog is disabled).

## 7. Risks and what is preserved

**Preserved:** all API endpoints and payloads, the `X-QA-Request` header, ticket data, status milestones, deterministic analytics (computed by the backend; unchanged), login and logout, the existing HTML escaping and `https`-only link handling, the focus-returning dialog.

**Risks and limits**
- No web fonts are loaded (security policy); see Fonts above.
- "Critical open" is computed in the browser from the ticket list (the API's `critical` counts all critical tickets, open or not).
- Recent activity is derived from `updated_at`; the history endpoint now exists and is the basis for milestone 2.
- No AI: the composer is a plain form and says so.
- Rebased onto Cloud Foundation #1–#5. Element IDs those tests use are kept (`#new-ticket`, `#k-total`, `#k-fixed`, `#k-verified`, `#ticket-rows tr`, `#login-form button.primary`, the screenshot and membership IDs). The two browser tests now open `#tickets`, because search and rows live on that view. Nothing is written to browser storage (the smoke test asserts this).
