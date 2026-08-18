---
name: Top PyPI Dependents
description: A monthly ranking of the PyPI projects the rest of PyPI depends on.
colors:
  paper: "#f4f1ea"
  ink: "#1c1a17"
  ink-muted: "#5a554d"
  ink-subtle: "#6b6559"
  rule: "#d8d2c4"
  surface-hover: "#e2d9c2"
  accent: "#3f5a3a"
  paper-dark: "#14130f"
  ink-dark: "#ece7d9"
  ink-muted-dark: "#a6a094"
  ink-subtle-dark: "#90897d"
  rule-dark: "#2d2b26"
  surface-hover-dark: "#2b2721"
  accent-dark: "#a2c087"
typography:
  display:
    fontFamily: "Petrona, ui-serif, Georgia, serif"
    fontSize: "clamp(2.0625rem, 1.77rem + 1.56vw, 2.875rem)"
    fontWeight: 400
    lineHeight: 1.1
    letterSpacing: "-0.015em"
  body:
    fontFamily: "'Hanken Grotesk', ui-sans-serif, system-ui, sans-serif"
    fontSize: "clamp(1rem, 0.96rem + 0.20vw, 1.0625rem)"
    fontWeight: 400
    lineHeight: 1.55
    letterSpacing: "normal"
  label:
    fontFamily: "'Hanken Grotesk', ui-sans-serif, system-ui, sans-serif"
    fontSize: "clamp(0.75rem, 0.73rem + 0.10vw, 0.8125rem)"
    fontWeight: 500
    lineHeight: 1.55
    letterSpacing: "0.14em"
  subhead:
    fontFamily: "'Hanken Grotesk', ui-sans-serif, system-ui, sans-serif"
    fontSize: "1em"
    fontWeight: 500
    lineHeight: 1.55
    letterSpacing: "normal"
  meta:
    fontFamily: "'Hanken Grotesk', ui-sans-serif, system-ui, sans-serif"
    fontSize: "clamp(0.75rem, 0.73rem + 0.10vw, 0.8125rem)"
    fontWeight: 400
    lineHeight: 1.55
    letterSpacing: "0.06em"
    fontFeature: "tabular-nums"
  code:
    fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace"
    fontSize: "0.9em"
    fontWeight: 400
    lineHeight: 1.55
    letterSpacing: "normal"
rounded:
  sm: "2px"
spacing:
  xs: "0.5rem"
  sm: "0.75rem"
  md: "1rem"
  lg: "1.5rem"
  xl: "3rem"
components:
  nav-link:
    textColor: "{colors.ink-muted}"
    typography: "{typography.label}"
  nav-link-hover:
    textColor: "{colors.ink}"
  input-search:
    backgroundColor: "transparent"
    textColor: "{colors.ink}"
    typography: "{typography.body}"
    rounded: "{rounded.sm}"
    padding: "0.75rem 1rem"
    width: "100%"
  table-header-cell:
    textColor: "{colors.ink-muted}"
    typography: "{typography.label}"
    padding: "0 0.75rem 0.75rem"
  table-row:
    backgroundColor: "transparent"
    textColor: "{colors.ink}"
    padding: "0.75rem"
  table-row-hover:
    backgroundColor: "{colors.surface-hover}"
---

# Design System: Top PyPI Dependents

## Overview

**Creative North Star: "The Specimen Label"**

The typed card pinned beside a mounted specimen. It is all label and no ornament:
warm paper, one hand of ink, hairline rules, dense tabular figures, and a single
living green that appears rarely enough to mean something. Nothing on the page
floats, glows, or asks to be admired. The specimen — a million projects and the
edges between them — is the interesting object; the sheet exists to hold it flat
and legible.

The system descends from the miketheman.dev "herbarium sheet," and stays
coherent with it without being bound to it. Where this project diverges, it
diverges toward the label: more numbers, tighter columns, and apparatus (a
filter, a live region, a published search index) that a personal site would never
need. That
apparatus is held to the same register as the prose around it — a button here is
a hairline rectangle, not a control that announces itself.

Density is moderate, not compressed. Prose sits on a 34rem measure inside a 52rem
page, so the table can run full width while paragraphs stay readable. The one
serif on the page is the h1; everything secondary is tracked caps in sans. Light
and dark are equal citizens, not a theme and its afterthought.

**Key Characteristics:**

- Warm paper, never white; warm near-black ink, never `#000`.
- Exactly one chromatic voice — a bottle sage green — across the whole system.
- Zero shadows. Depth is a 1px rule and a tint, and nothing else.
- Hairline borders at 1px, corners at 2px: present, almost unnoticed.
- Tracked caps for every secondary role; the serif reserved for the page title.
- Tabular numerals in every numeric column, always right-aligned.
- Self-contained: fonts are declared, never fetched.

## Colors

A warm paper-and-ink palette with a single green — desaturated on both sides of
the light/dark boundary, so it reads as pigment rather than as a UI status color.

### Primary

- **Bottle Sage** (`#3f5a3a` light / `#a2c087` dark): the system's only chroma. It
  has exactly three jobs — the leaf ornament, the focus ring, and the upward
  rank-change marker — plus the favicon, which is the ornament's leaf. In dark
  mode it lightens to a sage that holds the same role at the same rarity.

### Neutral

- **Herbarium Paper** (`#f4f1ea` light / `#14130f` dark): the page ground. Warm and
  slightly yellowed; the dark counterpart is a warm near-black, not a neutral gray.
- **Iron Gall Ink** (`#1c1a17` light / `#ece7d9` dark): body text and every primary
  reading surface.
- **Faded Ink** (`#5a554d` light / `#a6a094` dark): labels, nav links at rest,
  column headers, lede paragraphs, and the rank-drop marker.
- **Pencil** (`#6b6559` light / `#90897d` dark): the quietest text — footer meta,
  rank numbers, placeholder text, and the "unchanged" and "new" markers.
- **Hairline** (`#d8d2c4` light / `#2d2b26` dark): every rule, border, and divider
  in the system, at 1px.
- **Warm Tint** (`#e2d9c2` light / `#2b2721` dark): the only fill in the system.
  Used for row and button hover, never as a resting background. It has to clear
  roughly 1.15:1 against paper to register at all, on a wide table whose only
  other row-tracking aid is a hairline.

### Named Rules

**The One Green Rule.** The accent has three jobs and no fourth: the ornament (and
the favicon drawn from it), the focus ring, and the upward rank-change marker.
Everything else — including the link underline that darkens on row hover — is ink.
A rank *drop* stays in Faded Ink; introducing a red would create a second chromatic
voice, which this system rejects. The glyph (▲ / ▼), not the color, is what makes
direction unambiguous.

**The Warm Neutral Rule.** No neutral in this system is achromatic. Every paper,
ink, rule, and tint carries a warm cast. A gray that sits at hue-neutral will look
broken beside them, in either mode.

**The Inverted Selection Rule.** Text selection is Ink on Paper, swapped —
`::selection` is authored, never left to the browser. The browser's selection blue
is one of two foreign hues that can reach this page, and inverting two tokens the
system already owns removes it without admitting a third color. The other is the
search field's clear button, which the browser draws in its own blue; `color-scheme`
does **not** reach it, because the glyph is a UA-drawn mask rather than a color, so
it is suppressed and redrawn as an ink × via `-webkit-mask`. `color-scheme: light
dark` is still declared at the root, for the caret and the scrollbar.

## Typography

**Display Font:** Petrona (falls back to `ui-serif`, Georgia, serif)
**Body Font:** Hanken Grotesk (falls back to `ui-sans-serif`, `system-ui`, sans-serif)
**Mono Font:** `ui-monospace`, SFMono-Regular, Menlo, monospace

**Character:** A quiet grotesque doing all the work, interrupted once per page by a
transitional serif at the title. Neither face is fetched — self-containment
outranks typographic fidelity, so most readers see the `ui-serif` / `ui-sans-serif`
fallbacks, and the system is designed to hold up when they do.

### Hierarchy

- **Display** (Petrona, 400, `clamp(2.0625rem, 1.77rem + 1.56vw, 2.875rem)`, line-height 1.1,
  letter-spacing -0.015em): the `h1`, once per page. The only serif on the page.
- **Body** (Hanken Grotesk, 400, `clamp(1rem, 0.96rem + 0.20vw, 1.0625rem)`, line-height 1.55):
  all prose, list items, and table cells. Constrained to a 34rem measure.
- **Label** (Hanken Grotesk, 500, `clamp(0.75rem, 0.73rem + 0.10vw, 0.8125rem)`,
  letter-spacing 0.14em, uppercase): every secondary role — section headings (`h2`),
  nav links, column headers, and field labels. Set in Faded Ink.
- **Subhead** (Hanken Grotesk, 500, `1em`, sentence case): the `h3`. It sits
  *below* the Label role, not above it — a subsection reads as a lead-in to the
  paragraph beneath it, so it is body size at label weight. Full Ink, `1.5rem`
  above and `0.5rem` below.
- **Meta** (Hanken Grotesk, 400, same size as Label, letter-spacing 0.06em,
  tabular numerals): footer provenance lines. Set in Pencil.
- **Code** (mono, 0.9em): inline identifiers and the SQL and JSON blocks.

### Named Rules

**The One Serif Rule.** Petrona appears exactly once per page, at the `h1`. A serif
subhead, serif pull quote, or serif number would dissolve the contrast that makes
the title land.

**The Tracked-Caps Rule.** Letterspacing at 0.14em belongs to uppercase and only to
uppercase. Never letterspace lowercase text.

**The Tabular Rule.** Every number in a column aligns. Numeric cells are
right-aligned with `font-variant-numeric: tabular-nums` and `white-space: nowrap`,
so ranks, counts, and dates form a straight edge down the page.

## Layout

A single centered column, `max-width: 52rem`, with `1.5rem 1rem 3rem` of padding.
Tables run the full column width; prose and lists are separately capped at
`max-width: 34rem`, which is the reading measure. That two-width arrangement is the
whole spatial model — there is no grid, no sidebar, and no card system.

Vertical rhythm runs on a coarse scale: `0.5rem` between list items, `0.75rem`
under the title, `1rem` under a section heading, `1.5rem` after a form control or
ornament, and `3rem` between major sections. Section headings take `3rem` of space
above and `1rem` below, so a heading belongs visibly to what follows it.

The page has three horizontal rules and no more: under the nav, under the table
head, and above the footer — plus one per table row. Structure is expressed by
that ruling and by the `3rem` gaps, not by boxes.

One breakpoint, at `34rem` (the measure width). Below it, table cell padding tightens
from `0.75rem` to `0.6rem 0.4rem`, and columns carrying `.hide-narrow` — currently
"Incl. extras" — drop out entirely rather than wrapping or scrolling.

Motion is a single token pair: `180ms` on `cubic-bezier(0.2, 0.8, 0.2, 1)`, applied
only to color and border-color changes on hover. `prefers-reduced-motion: reduce`
sets the duration to `0ms` at the root, which disables every transition in the
system at once.

### Named Rules

**The Two-Width Rule.** Data gets the full 52rem column; prose never exceeds 34rem.
A paragraph that runs the width of the table is a defect.

## Elevation & Depth

**There are no shadows in this system.** Not on hover, not on focus, not on any
surface. `box-shadow` does not appear in the stylesheet, and adding one would be
the single most out-of-character change available.

Depth is conveyed two ways, both flat: a 1px Hairline rule, which separates; and a
Warm Tint fill, which indicates that a row or control is under the cursor. Nothing
in the system is ever described as sitting *above* anything else — this is ink on
paper, and paper has one plane.

### Named Rules

**The One Plane Rule.** No shadows, no blurs, no translucency, no `transform`-based
lift. If an element needs to be distinguished, rule it or tint it.

## Shapes

Rectangles with a 2px corner radius — present enough to soften a stroke ending,
small enough that nothing reads as a pill, a card, or a rounded button. The same
2px applies to the search field and the focus ring.

Borders are always `1px solid` in Hairline. There is no second border weight, no
double rule, and no dashed or dotted variant. The one departure from strict
rectangles is the leaf ornament: a 120×16 inline SVG of a rule interrupted by a
lens-shaped leaf, stroked in the accent at 1px and 0.6px, used once per page as a
section break under the lede.

## Components

### Navigation

- **Style:** a flex row of text links, no background, separated from the page by a
  1px bottom rule with `3rem` of space beneath it.
- **Typography:** Label role — uppercase, 500, 0.14em tracking, no underline.
- **States:** Faded Ink at rest, transitioning to full Ink on hover over 180ms. The
  current page sits at full ink with `aria-current="page"` — on a two-page site that
  state is the only wayfinding there is.
- **Mobile:** wraps with `0.75rem 1.5rem` gaps; no menu, no disclosure.

### Inputs / Fields

- **Style:** transparent background, 1px **Pencil** border (not Hairline), 2px
  radius, `0.75rem 1rem` padding, `max-width: 24rem`, inheriting the body font at
  full size.
- **Label:** a separate Label-role element above the field, never a floating or
  inline placeholder label.
- **Placeholder:** Pencil.
- **Hover:** border shifts to full Ink.
- **Clear button:** the native affordance suppressed and redrawn as an ink ×.
- **Focus:** the global focus ring — a 2px accent outline at 3px offset.
- **Error / Disabled:** none exist in the system. Nothing here can be submitted or
  fail.

### Tables

The signature component. Everything else on the site exists to frame it.

- **Structure:** `border-collapse: collapse`, full width, 1px Hairline under the
  head and under every row. No vertical rules, no zebra striping, no outer border.
- **Head:** Label typography in Faded Ink, left-aligned except numeric columns,
  `0.75rem` padding, `white-space: nowrap`. **Sticky** on wide screens, with its
  hairline drawn as an `::after` pseudo-element — a `border-bottom` on a sticky
  cell in a collapsed table scrolls away with the content it was meant to sit
  under. Static below `34rem`, where the search block takes the pinned slot.
- **Cells:** `0.75rem` padding, body typography.
- **Numeric cells:** right-aligned, tabular numerals, no wrapping.
- **Row hover:** the entire row fills with Warm Tint over 180ms; the project link's
  underline simultaneously darkens from Hairline to Faded Ink. The link itself has
  no `text-decoration` — its underline is a bottom border, which is why it can
  respond to the row's state.
- **Ranked-column colors:** rank numbers in Pencil, change markers in Faded Ink,
  upward markers in the accent, "unchanged" and "new" in Pencil.

### Search Block

- **Structure:** the Label-role caption, the field, and the running count, grouped
  as one unit (`.search`) because they are read as one.
- **Narrow screens:** pinned to the top of the viewport, bled to the page edges so
  rows do not scroll through the body padding, closed with a hairline. A phone with
  its keyboard open has roughly 500px of viewport; unpinned, typing a name left one
  result row visible. The ornament is also dropped below `34rem` — decoration
  between the lede and the controls, costing 64px of that budget.

### Notes

Search results and recovery messages (`.note`) are set apart from the running count
they sit beneath, quoted with the same 1px left hairline a code block uses. They
share the count's type but not its voice: a message about what the search found must
not read as the count itself.

### Code Blocks

- **Style:** quoted material, ruled the way everything else on the page is ruled —
  a 1px Hairline on the left with `1rem` of padding beside it. No fill, no box, no
  second border weight. Warm Tint is a hover state and never becomes a resting
  background, here or anywhere.
- **Type:** the mono stack at `0.9em`, set on `pre` as well as `code` so a block
  without a `<code>` child cannot fall back to the browser's default mono.
- **Overflow:** `overflow-x: auto` on the block. This is load-bearing rather than
  cosmetic — a SQL line is wider than a phone, and without it the document itself
  scrolls sideways and every paragraph on the page ends up narrower than the screen.

### Favicon

The ornament's leaf, alone, as an inline `data:` URI — two of them, the second
carrying `media="(prefers-color-scheme: dark)"` in the light accent, because the
tab strip has a theme of its own and the dark green vanishes against a dark one.
Inline rather than a file: a fetched icon would break the self-containment rule,
and no icon at all is a 404 on every visit.

### Focus Ring

One treatment for the entire site: `:focus-visible` draws a 2px accent outline at
3px offset with a 2px radius. It is the only element-level use of the accent that
appears on every page, and it is never overridden per component.

### Screen-Reader Region

A visually hidden, politely announced status line (`.sr-only`, `role="status"`)
positioned off-flow via `clip-path: inset(50%)` — deliberately not
`display: none`, which would remove it from the accessibility tree and silence it.
Visually absent, structurally required.

## Do's and Don'ts

### Do:

- **Do** keep the accent to its three established jobs, and add a fourth only by
  deliberately amending The One Green Rule.
- **Do** theme the surfaces the stylesheet does not draw — selection, caret,
  scrollbar, focus ring, underline offset, favicon, and the search field's clear
  button. They ship with defaults that belong to no design system.
- **Do** outline controls in Pencil (5.13:1), never in Hairline (1.34:1). A
  control with no fill is its border; WCAG 1.4.11 asks 3:1 of it. Rules and
  dividers stay Hairline — they are decoration, and exempt.
- **Do** give any block that can exceed the column — a code block, a wide table —
  its own `overflow-x`, so the document never scrolls sideways.
- **Do** set every secondary role — headings, nav, column heads, field labels — in
  tracked uppercase at `0.14em`, in Faded Ink.
- **Do** give every numeric column `text-align: right`, `tabular-nums`, and
  `white-space: nowrap`.
- **Do** separate with a 1px Hairline rule or a `3rem` gap. Those are the system's
  two dividers.
- **Do** define every new color in both `:root` and the
  `prefers-color-scheme: dark` block. A token that exists in only one mode is a bug.
- **Do** route all motion through `--dur` and `--ease`, so
  `prefers-reduced-motion` continues to disable everything from one place.
- **Do** keep prose at a 34rem measure even when the container is wider.

### Don't:

- **Don't** add a `box-shadow`, blur, gradient, or `transform` lift anywhere.
- **Don't** introduce a second chroma — no red for a rank drop, no blue for links,
  no yellow for a warning. Ink weight and glyphs carry those distinctions.
- **Don't** use pure white or pure black. Every neutral in this system is warm.
- **Don't** letterspace lowercase text.
- **Don't** add a second serif use. Petrona appears at the `h1` and nowhere else.
- **Don't** enlarge the corner radius past 2px, or add a second border weight.
- **Don't** fetch a webfont, script, or image from another origin. The page must
  stay self-contained.
- **Don't** hide a table column by shrinking it. Below `34rem`, columns are dropped
  with `.hide-narrow` — horizontal scrolling of the table is not part of this system.
