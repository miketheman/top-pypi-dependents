import assert from "node:assert/strict";
import { test } from "node:test";
import { pastPage } from "../../src/top_pypi_dependents/assets/search.js";

const index = [
  ["requests", 1, 100, 120, null],
  ["requests-oauthlib", 2, 50, 60, 1],
  ["django", 3, 40, 45, -1],
  ["requests-toolbelt", 4, 30, 31, 0],
];

test("only projects ranked past the page are found", () => {
  const found = pastPage(index, "requests", 1);
  assert.deepEqual(
    found.map(([name]) => name),
    ["requests-oauthlib", "requests-toolbelt"],
  );
});

test("a needle matching nothing past the page finds nothing", () => {
  assert.deepEqual(pastPage(index, "django", 3), []);
});
