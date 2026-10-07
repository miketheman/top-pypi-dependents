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

export const pypiUrl = (name) => `https://pypi.org/project/${encodeURIComponent(name)}/`;

// A link to a project's PyPI page, opening a new tab and saying so through the
// page's shared "opens in a new tab" description.
export function pypiLink(name) {
  const link = document.createElement("a");
  link.href = pypiUrl(name);
  link.target = "_blank";
  link.rel = "noopener";
  link.setAttribute("aria-describedby", "new-tab");
  link.textContent = name;
  return link;
}

// A JSON file from beside the page, or an error naming it and the status.
export async function fetchJson(url) {
  const response = await fetch(url);
  if (!response.ok) throw new Error(`${url}: HTTP ${response.status}`);
  return response.json();
}
