// Text helpers shared by the rankings and cascade pages.

// Jinja's `{:,}` is en-US whatever the reader's locale, so this matches it
// rather than following the browser: rendered and scripted numbers share a page.
export const fmt = (n) => n.toLocaleString("en-US");

export const plural = (n, word) => `${fmt(n)} ${word}${n === 1 ? "" : "s"}`;

// Every name in the payload and the graph is PEP 503 canonical, so a needle
// has to be too. Without this, `zope.interface` and `typing_extensions` -- the
// way their own docs spell them -- match nothing.
export const canonical = (value) =>
  value
    .trim()
    .toLowerCase()
    .replace(/[-_.]+/g, "-");
