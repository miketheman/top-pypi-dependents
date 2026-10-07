import assert from "node:assert/strict";
import { test } from "node:test";
import { canonical, fmt, plural } from "../../src/top_pypi_dependents/assets/format.js";

test("canonical matches how PyPI normalizes a name", () => {
  assert.equal(canonical("  Zope.Interface "), "zope-interface");
  assert.equal(canonical("typing_extensions"), "typing-extensions");
  assert.equal(canonical("ruamel..yaml"), "ruamel-yaml");
});

test("fmt groups thousands the way the rendered page does", () => {
  assert.equal(fmt(1234567), "1,234,567");
});

test("plural says one without an s", () => {
  assert.equal(plural(1, "project"), "1 project");
  assert.equal(plural(0, "project"), "0 projects");
  assert.equal(plural(2500, "project"), "2,500 projects");
});
