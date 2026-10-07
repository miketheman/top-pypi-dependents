// The cascade page's graph logic, apart from drawing it: decoding the edge
// lists, walking them, and reading the sky. Nothing here touches the page.

// Each edge list arrives as per-node out-degrees and the gaps between that
// node's sorted targets; expanded into dependent/dependency pairs, runtime
// edges first. An edge is `kind` 1 when only extras draw it: an extra itself,
// or a runtime edge touching a project ranked only with extras.
export function decodeEdges({ edges, extra_edges, ranked }) {
  const runtime = edges.gaps.length;
  const edgeCount = runtime + extra_edges.gaps.length;
  const pairs = new Uint32Array(edgeCount * 2);
  let k = 0;
  for (const { degree, gaps } of [edges, extra_edges]) {
    let g = 0;
    for (let i = 0; i < degree.length; i++) {
      let target = 0;
      for (let d = 0; d < degree[i]; d++) {
        target += gaps[g++];
        pairs[2 * k] = i;
        pairs[2 * k + 1] = target;
        k++;
      }
    }
  }
  const kind = new Uint8Array(edgeCount);
  for (let e = 0; e < edgeCount; e++) {
    kind[e] = e >= runtime || pairs[2 * e] >= ranked || pairs[2 * e + 1] >= ranked ? 1 : 0;
  }
  return { pairs, kind, edgeCount };
}

// Adjacency over the edges a mode shows, in CSR form: `list[start[i]]` up to
// `list[start[i + 1]]` are node i's neighbors, in rank order so a capped
// cascade keeps the best-known ones. `from` 1 and `to` 0 follow an edge from
// the depended-on project to its dependent.
export function adjacency({ pairs, kind, edgeCount }, count, extras, from, to) {
  const start = new Uint32Array(count + 1);
  for (let k = 0; k < edgeCount; k++) if (extras || !kind[k]) start[pairs[2 * k + from] + 1]++;
  for (let i = 0; i < count; i++) start[i + 1] += start[i];
  const list = new Uint32Array(start[count]);
  const fill = start.slice(0, count);
  for (let k = 0; k < edgeCount; k++) {
    if (extras || !kind[k]) list[fill[pairs[2 * k + from]]++] = pairs[2 * k + to];
  }
  for (let i = 0; i < count; i++) list.subarray(start[i], start[i + 1]).sort();
  return [list, start];
}

// Breadth-first over one direction, recording each node's hop count.
export function reach(origin, list, start) {
  const depth = new Map([[origin, 0]]);
  let frontier = [origin];
  for (let d = 1; frontier.length; d++) {
    const next = [];
    for (const from of frontier) {
      for (let k = start[from]; k < start[from + 1]; k++) {
        const to = list[k];
        if (!depth.has(to)) {
          depth.set(to, d);
          next.push(to);
        }
      }
    }
    frontier = next;
  }
  return depth;
}

// A cloud's cells are only its densest core; its tint spreads `passes` cells
// further, into the haze around it, so the color reads as the cloud's. Cells
// hold a cloud's number counted from one; zero is no cloud.
export function spreadCells(cells, size, passes) {
  let spread = Int16Array.from(cells);
  for (let pass = 0; pass < passes; pass++) {
    const next = spread.slice();
    for (let y = 0; y < size; y++) {
      for (let x = 0; x < size; x++) {
        if (spread[y * size + x]) continue;
        for (const [dx, dy] of [
          [1, 0],
          [-1, 0],
          [0, 1],
          [0, -1],
        ]) {
          const nx = x + dx;
          const ny = y + dy;
          if (nx >= 0 && ny >= 0 && nx < size && ny < size && spread[ny * size + nx]) {
            next[y * size + x] = spread[ny * size + nx];
            break;
          }
        }
      }
    }
    spread = next;
  }
  return spread;
}

// The cell, of `size` across `extent`, holding a data-space coordinate,
// clamped to the grid.
// Whether a label box [left, middle, width] at (x, y) crowds none of the boxes
// already `taken`: `pad` apart sideways, `rows` apart up and down. A taken
// box may carry a fourth value, its own row clearance, wider than the caller's.
export const clears = (taken, x, y, w, pad, rows) =>
  !taken.some((r) => x < r[0] + r[2] + pad && x + w + pad > r[0] && Math.abs(y - r[1]) < Math.max(rows, r[3] ?? 0));

export const cellIndex = (v, extent, size) => Math.min(size - 1, Math.max(0, Math.floor((v / extent) * size)));

// The cloud number of the cell holding a point in data space; zero is no cloud.
export function cloudAt(cells, size, extent, x, y) {
  return cells[cellIndex(y, extent, size) * size + cellIndex(x, extent, size)];
}

// The box around some points, as `[x0, y0, x1, y1]`.
export function bounds(points) {
  let [x0, y0, x1, y1] = [Infinity, Infinity, -Infinity, -Infinity];
  for (const [x, y] of points) {
    x0 = Math.min(x0, x);
    x1 = Math.max(x1, x);
    y0 = Math.min(y0, y);
    y1 = Math.max(y1, y);
  }
  return [x0, y0, x1, y1];
}

// The data-space box around a cloud's cells.
export function cloudBounds(cells, size, extent, cloud) {
  const step = extent / size;
  const corners = [];
  cells.forEach((c, k) => {
    if (c !== cloud) return;
    const x = (k % size) * step;
    const y = Math.floor(k / size) * step;
    corners.push([x, y], [x + step, y + step]);
  });
  return bounds(corners);
}
