import assert from "node:assert/strict";
import { test } from "node:test";
import {
  adjacency,
  cloudAt,
  cloudBounds,
  decodeEdges,
  reach,
  spreadCells,
} from "../../src/top_pypi_dependents/assets/graph-model.js";

// Four projects, ranked 0..3; node 3 is ranked only with extras. Runtime
// edges: 1 -> 0, 2 -> 0, 2 -> 1, 3 -> 2. One extra: 1 -> 2.
const graph = {
  ranked: 3,
  edges: { degree: [0, 1, 2, 1], gaps: [0, 0, 1, 2] },
  extra_edges: { degree: [0, 1, 0, 0], gaps: [2] },
};

test("edges decode into dependent/dependency pairs, runtime first", () => {
  const { pairs, kind, edgeCount } = decodeEdges(graph);
  assert.equal(edgeCount, 5);
  assert.deepEqual([...pairs], [1, 0, 2, 0, 2, 1, 3, 2, 1, 2]);
  // An extra, and a runtime edge from a project ranked only with extras.
  assert.deepEqual([...kind], [0, 0, 0, 1, 1]);
});

test("adjacency lists each node's neighbors in rank order", () => {
  const edges = decodeEdges(graph);
  const [list, start] = adjacency(edges, 4, false, 1, 0);
  const dependentsOf = (i) => [...list.subarray(start[i], start[i + 1])];
  assert.deepEqual(dependentsOf(0), [1, 2]);
  assert.deepEqual(dependentsOf(2), []);
  const [all, allStart] = adjacency(edges, 4, true, 1, 0);
  assert.deepEqual([...all.subarray(allStart[2], allStart[3])], [1, 3]);
});

test("reach counts every hop out from the origin", () => {
  const [list, start] = adjacency(decodeEdges(graph), 4, true, 1, 0);
  const depths = reach(0, list, start);
  assert.deepEqual(Object.fromEntries(depths), { 0: 0, 1: 1, 2: 1, 3: 2 });
});

test("a cloud's tint spreads into empty cells, never over another cloud", () => {
  // 3x3: cloud 1 in the top-left corner, cloud 2 in the bottom-right.
  const cells = [1, 0, 0, 0, 0, 0, 0, 0, 2];
  assert.deepEqual([...spreadCells(cells, 3, 1)], [1, 1, 0, 1, 0, 2, 0, 2, 2]);
  assert.deepEqual([...spreadCells(cells, 3, 0)], cells);
});

test("cloudAt finds the cell under a point and clamps to the grid", () => {
  const cells = [1, 0, 0, 2];
  assert.equal(cloudAt(cells, 2, 100, 10, 10), 1);
  assert.equal(cloudAt(cells, 2, 100, 99, 99), 2);
  assert.equal(cloudAt(cells, 2, 100, 500, -5), 0);
});

test("cloudBounds boxes a cloud's cells in data space", () => {
  const cells = [0, 1, 0, 1];
  assert.deepEqual(cloudBounds(cells, 2, 100, 1), [50, 0, 100, 100]);
});
