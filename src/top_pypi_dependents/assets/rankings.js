import { canonical, fmt } from "./format.js";
import { pastPage } from "./search.js";

// Matches against data-name rather than a cell's text: position-independent,
// so inserting a column cannot silently make this filter the wrong one, and
// the value is already canonical lowercase so there is nothing to normalize
// per row.
//
// A search looks past the page. The page lists the top rows; every ranked
// project is in `search-index.json`, so a name someone can type is findable
// whether or not it was rendered. The index is fetched on the first search,
// never on arrival -- a reader who only reads the table pays nothing for it.

const rows = Array.from(document.querySelectorAll("#page-rows tr"));
const filter = document.getElementById("filter");
const shown = document.getElementById("shown");
const empty = document.getElementById("empty");
const beyond = document.getElementById("beyond");
const table = document.getElementById("rows");
const beyondNote = document.getElementById("beyond-note");
const announce = document.getElementById("announce");
// What the page itself carries, stated by the template on the table.
const onPage = Number(table.dataset.onPage);
const ranked = Number(table.dataset.ranked);
const showChange = "showChange" in table.dataset;
// Enough to prove a project exists and where it sits; a filter that matched
// thousands past the page would rebuild the very document this page shed.
const BEYOND_LIMIT = 50;
let announceTimer;
let index = null;
let indexState = "idle";

function loadIndex() {
  if (indexState !== "idle") {
    return;
  }
  indexState = "loading";
  fetch("search-index.json")
    .then((response) => {
      if (!response.ok) {
        throw new Error(response.status);
      }
      return response.json();
    })
    .then((data) => {
      index = data.projects;
      indexState = "ready";
      apply();
    })
    .catch(() => {
      indexState = "failed";
      apply();
    });
}

function cell(className, text) {
  const td = document.createElement("td");
  if (className) {
    td.className = className;
  }
  td.textContent = text;
  return td;
}

// The glyph carries the direction for anyone who can see it; the hidden word
// carries it for anyone who cannot. A rise and a fall must not be announced
// identically.
function movement(change) {
  const span = document.createElement("span");
  const said = document.createElement("span");
  said.className = "sr-only";
  if (change === null) {
    span.className = "new";
    span.textContent = "new";
    return span;
  }
  if (change > 0) {
    span.className = "up";
    span.append("\u25b2 ");
    said.textContent = "up ";
  } else if (change < 0) {
    span.className = "dn";
    span.append("\u25bc ");
    said.textContent = "down ";
  } else {
    span.className = "flat";
    span.append("\u2013");
    said.textContent = "unchanged";
  }
  span.append(said);
  if (change !== 0) {
    span.append(String(Math.abs(change)));
  }
  return span;
}

function beyondRow([project, rank, dependents, dependentsAll, change]) {
  const tr = document.createElement("tr");
  tr.append(cell("num rank", fmt(rank)));

  const name = document.createElement("td");
  const link = document.createElement("a");
  link.href = `https://pypi.org/project/${encodeURIComponent(project)}/`;
  link.target = "_blank";
  link.rel = "noopener";
  link.setAttribute("aria-describedby", "new-tab");
  link.textContent = project;
  name.append(link);
  tr.append(name);

  tr.append(cell("num", fmt(dependents)));
  tr.append(cell("num hide-narrow", fmt(dependentsAll)));

  if (showChange) {
    const move = cell("num move hide-narrow", "");
    move.append(movement(change));
    tr.append(move);
  }
  return tr;
}

// Returns how many matches exist past the page, which is not the same as how
// many were drawn: the count is the honest answer, the cap is what fits.
function fillBeyond(needle) {
  beyond.replaceChildren();
  if (needle === "" || indexState !== "ready") {
    return 0;
  }
  const found = pastPage(index, needle, onPage);
  beyond.append(...found.slice(0, BEYOND_LIMIT).map(beyondRow));
  return found.length;
}

function apply() {
  const needle = canonical(filter.value);
  let visible = 0;
  for (const row of rows) {
    const matches = needle === "" || row.dataset.name.includes(needle);
    row.hidden = !matches;
    if (matches) {
      visible += 1;
    }
  }
  if (needle !== "") {
    loadIndex();
  }

  const found = fillBeyond(needle);
  const drawn = Math.min(found, BEYOND_LIMIT);
  const searching = needle !== "" && indexState === "loading";

  shown.textContent = fmt(visible + drawn);

  if (searching) {
    beyondNote.textContent = `Searching all ${fmt(ranked)} ranked projects...`;
    beyondNote.hidden = false;
  } else if (found > BEYOND_LIMIT) {
    beyondNote.textContent = `${fmt(found)} matches rank past this page; the first ${BEYOND_LIMIT} are listed below.`;
    beyondNote.hidden = false;
  } else if (found > 0) {
    beyondNote.textContent = `${fmt(found)} of these rank past this page.`;
    beyondNote.hidden = false;
  } else {
    beyondNote.hidden = true;
  }

  // Three different nothings, and saying the wrong one is worse than silence:
  // the index has not arrived, the index never arrived, or the project is not
  // ranked at all.
  empty.hidden = visible !== 0 || drawn !== 0 || searching;
  // A head with five column labels over an empty body reads as a broken
  // page, which is a poor frame for the page's most careful sentence.
  table.hidden = visible === 0 && drawn === 0;
  if (!empty.hidden && indexState === "failed") {
    empty.textContent = `No match on this page, and the full index could not be loaded.`;
  }

  // Debounced, because this fires on every keystroke and an undebounced live
  // region reads a new total per character typed. The visible count above is
  // already current; only the announcement waits.
  clearTimeout(announceTimer);
  announceTimer = setTimeout(() => {
    const count = visible + drawn;
    if (searching) {
      return;
    }
    announce.textContent = count === 0 ? "Not ranked." : `Showing ${fmt(count)} of ${fmt(ranked)} ranked projects.`;
  }, 400);
}

filter.addEventListener("input", apply);
