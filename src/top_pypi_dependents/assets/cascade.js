import { canonical, fetchJson, fmt, plural, pypiLink } from "./format.js";
import { adjacency, bounds, cellIndex, cloudAt, cloudBounds, decodeEdges, reach, spreadCells } from "./graph-model.js";

(() => {
  // Tuning. A hop is one step out along "is depended on by".
  const HOP_MS = 220;
  const GLOW_MS = 1400;
  const MAX_DEPTH = 4;
  const MAX_LIT = 6000;
  const DECAY = 0.72;
  const SIM_EVERY_MS = 2200;
  const LIST_LIMIT = 12;
  // The most a revealed list builds at once: numpy is depended on by thousands
  // of drawn projects, and a button for each stalled the panel. The filter
  // searches all of them.
  const LIST_MAX = 500;
  // A trace's pulses: one every PULSE_PX pixels of a line as drawn, at least
  // one and at most MAX_PULSES to a line, each taking PULSE_MS to travel its
  // spacing. Read by the edge shader and by the beads that ride the pulses.
  const PULSE_PX = 140;
  const MAX_PULSES = 12;
  const PULSE_MS = 1400;
  const pulsePhase = (now) => (now / PULSE_MS) % 1;

  const $ = (id) => document.getElementById(id);
  const surface = $("surface");
  const canvas = $("gl");
  const labels = $("labels");
  const ctx = labels.getContext("2d");
  const minimap = $("minimap");
  const mctx = minimap.getContext("2d");
  const tip = $("tip");
  const statusLine = $("status");
  const detail = $("detail");
  const releases = $("releases");
  const find = $("find");
  const findNote = $("find-note");
  const simulate = $("simulate");
  const extrasToggle = $("extras");
  // The haze where projects crowd, and the names the build gave those crowds.
  const cloudsToggle = $("clouds");
  const cloudsOn = () => cloudsToggle.checked;
  // A trace lights every project a release reaches, however far; direct only
  // stops at the first hop either way, which on a hub is the readable part.
  const directToggle = $("direct");
  const hops = () => (directToggle.checked ? 1 : Number.POSITIVE_INFINITY);
  const reduceMotion = matchMedia("(prefers-reduced-motion: reduce)");
  // Everything that moves -- a trace's pulses, a release's glow, simulated
  // releases -- answers to this one switch. Off leaves the trace drawn still:
  // a hub like requests lights thousands of lines, and pulsing all of them is
  // more than some readers want. It starts off for anyone who asked their
  // system for less motion.
  const animate = $("animate");
  animate.checked = !reduceMotion.matches;
  const moving = () => animate.checked;
  const narrow = matchMedia("(max-width: 44rem)");

  if (narrow.matches) $("key").open = false;

  // No antialiasing on the canvas itself: the still graph is drawn into a
  // multisampled target, which does the smoothing.
  const gl = canvas.getContext("webgl2", { antialias: false, alpha: false });
  if (!gl) {
    statusLine.textContent = "This page needs WebGL 2, which this browser does not offer.";
    return;
  }

  // Colors come from the page's own tokens, so the graph follows the theme.
  let colors;
  // Any CSS color as sRGB channels in 0..1, read back through a one-pixel
  // canvas. Not parsed from the computed style: a color written in oklch
  // computes to `oklch(...)`, whose numbers a digit match would take for RGB.
  const probe = document.createElement("canvas").getContext("2d", { willReadFrequently: true });
  probe.canvas.width = probe.canvas.height = 1;
  function srgb(value) {
    probe.clearRect(0, 0, 1, 1);
    probe.fillStyle = value.trim();
    probe.fillRect(0, 0, 1, 1);
    return [...probe.getImageData(0, 0, 1, 1).data.slice(0, 3)].map((v) => v / 255);
  }
  function readColors() {
    const style = getComputedStyle(document.documentElement);
    const token = (name) => srgb(style.getPropertyValue(name));
    colors = { paper: token("--paper"), ink: token("--ink"), muted: token("--ink-muted"), accent: token("--accent") };
    colors.font = style.getPropertyValue("--sans");
    colors.serif = style.getPropertyValue("--serif");
    colors.nebula = [1, 2, 3, 4, 5, 6].map((n) => token(`--nebula-${n}`));
  }
  readColors();
  const css = (c, a = 1) => `rgba(${c.map((v) => Math.round(v * 255)).join()},${a})`;

  function compile(vs, fs) {
    const program = gl.createProgram();
    for (const [type, src] of [
      [gl.VERTEX_SHADER, vs],
      [gl.FRAGMENT_SHADER, fs],
    ]) {
      const shader = gl.createShader(type);
      gl.shaderSource(shader, src);
      gl.compileShader(shader);
      if (!gl.getShaderParameter(shader, gl.COMPILE_STATUS)) throw new Error(gl.getShaderInfoLog(shader));
      gl.attachShader(program, shader);
    }
    gl.linkProgram(program);
    if (!gl.getProgramParameter(program, gl.LINK_STATUS)) throw new Error(gl.getProgramInfoLog(program));
    return program;
  }

  // Focus is one float per node and per edge: 0 is outside the selection's
  // reach, a positive n is n hops downstream (a dependent), a negative n is n
  // hops upstream (a dependency), and the selection itself is 0.5.
  const VIEW = "uniform vec2 u_scale; uniform vec2 u_offset; uniform float u_focusing;";
  const nodeProgram = compile(
    `#version 300 es
    ${VIEW}
    uniform float u_px; uniform float u_grow; uniform float u_pass;
    in vec2 a_pos; in float a_size; in float a_heat; in float a_focus;
    out float v_heat; out float v_focus; out float v_weight;
    void main() {
      // Depth orders the dots, since they are drawn in rank order and numpy
      // would otherwise lie under everything drawn after it: bigger nearer,
      // and anything on a trace nearer still.
      float depth = 0.5 - min(a_size, 60.0) / 150.0 - (u_focusing > 0.5 && a_focus != 0.0 ? 1.0 : 0.0);
      gl_Position = vec4(a_pos * u_scale + u_offset, depth, 1.0);
      // A point size of zero is undefined in GLES and most drivers draw a pixel,
      // so a hidden project is moved outside clip space instead.
      float heat = u_pass > 0.5 ? a_heat : 0.0;
      if (a_size == 0.0 || (u_pass > 0.5 && heat < 0.01)) gl_Position = vec4(2.0, 2.0, 2.0, 1.0);
      v_heat = heat;
      v_focus = a_focus;
      float grow = a_focus == 0.5 ? 6.0 : 0.0;
      gl_PointSize = (a_size + u_grow + grow + heat * (4.0 + 0.5 * a_size)) * u_px;
      v_weight = clamp((a_size - 1.4) / 8.0, 0.0, 1.0);
    }`,
    `#version 300 es
    // highp, to match the vertex shader: both read u_focusing, and a uniform
    // shared across stages must agree on precision or the program fails.
    precision highp float;
    uniform vec3 u_base; uniform vec3 u_hot; uniform vec3 u_ink; uniform float u_focusing;
    in float v_heat; in float v_focus; in float v_weight; out vec4 color;
    void main() {
      vec2 c = gl_PointCoord * 2.0 - 1.0;
      float d = dot(c, c);
      if (d > 1.0) discard;
      float edge = 1.0 - smoothstep(0.7, 1.0, d);
      // Hubs shade toward ink as well as growing, so they read as landmarks.
      vec3 rgb = mix(u_base, u_ink, v_weight * 0.6);
      // Small projects stay light, so forty thousand overlapping dots read as
      // density rather than a solid disc; the hubs stand out of it.
      float a = mix(0.28, 0.85, v_weight);
      if (u_focusing > 0.5) {
        if (v_focus == 0.0) { a = 0.08; }
        else if (v_focus == 0.5) { rgb = u_ink; a = 1.0; }
        else if (v_focus > 0.0) { rgb = u_hot; a = 0.95; }
        else { rgb = u_ink; a = 0.9; }
      }
      rgb = mix(rgb, u_hot, v_heat);
      a = max(a, v_heat) * edge;
      color = vec4(rgb * a, a);
    }`,
  );

  // Edges carry which end they are (0 the dependent, 1 the dependency), which
  // is what draws direction: a line strengthens toward what is depended on.
  // `a_kind` is 0 for an edge in the runtime graph and 1 for one that only
  // exists once extras count.
  const edgeProgram = compile(
    `#version 300 es
    ${VIEW}
    uniform float u_extras; uniform float u_k;
    in vec2 a_pos; in float a_end; in float a_focus; in float a_kind; in float a_len;
    out float v_end; out float v_focus; out float v_repeat;
    void main() {
      gl_Position = vec4(a_pos * u_scale + u_offset, 0.0, 1.0);
      // An edge only extras draw is moved outside clip space rather than
      // discarded per fragment: with extras off that is more than half the
      // lines, and rasterizing them only to throw every pixel away doubled
      // the work.
      if (a_kind > 0.5 && u_extras < 0.5) gl_Position = vec4(2.0, 2.0, 2.0, 1.0);
      v_end = a_end; v_focus = a_focus;
      // Pulses spaced by pixels of the line as drawn: a fixed one per line
      // crawled as a long smear once zooming stretched a line past the view.
      v_repeat = clamp(floor(a_len * u_k / ${PULSE_PX}.0), 1.0, ${MAX_PULSES}.0);
    }`,
    `#version 300 es
    precision highp float;
    uniform vec3 u_ink; uniform vec3 u_hot; uniform float u_alpha; uniform float u_focusing;
    uniform float u_time; uniform float u_traceAlpha; uniform float u_pass;
    in float v_end; in float v_focus; in float v_repeat; out vec4 color;
    void main() {
      float shade = mix(0.1, 1.0, v_end);
      vec3 rgb = u_ink;
      float a = u_alpha * shade;
      if (u_focusing > 0.5) {
        if (v_focus == 0.0) { a *= 0.05; }
        else {
          float depth = abs(v_focus);
          rgb = v_focus > 0.0 ? u_hot : u_ink;
          a = u_traceAlpha * mix(0.55, 0.9, shade) / (1.0 + 0.35 * (depth - 1.0));
          // A pulse runs from the dependency to the dependent: the way a
          // release travels. Upstream lines are dashed by the same pulse.
          float pulse = fract(u_time + v_end * v_repeat);
          if (u_pass > 0.5) a = smoothstep(0.75, 1.0, pulse) * 0.9;
          else if (v_focus < 0.0) a *= step(0.35, fract(pulse * 3.0));
        }
      }
      color = vec4(rgb * a, a);
    }`,
  );

  // The clouds: one quad over the whole layout, sampling the build's density
  // grid with smooth filtering, so the haze has soft edges at any zoom. Faint
  // ink, never the accent, which belongs to a trace.
  const cloudProgram = compile(
    `#version 300 es
    ${VIEW}
    uniform float u_extent;
    out vec2 v_uv;
    void main() {
      vec2 corner = vec2(gl_VertexID % 2 == 1 ? 1.0 : 0.0, gl_VertexID >= 2 ? 1.0 : 0.0);
      v_uv = corner;
      gl_Position = vec4(corner * u_extent * u_scale + u_offset, 0.0, 1.0);
    }`,
    `#version 300 es
    precision highp float;
    uniform sampler2D u_density; uniform sampler2D u_tints; uniform float u_focusing; uniform float u_fade;
    in vec2 v_uv; out vec4 color;
    void main() {
      float d = texture(u_density, v_uv).r;
      vec4 tint = texture(u_tints, v_uv);
      float a = smoothstep(0.2, 0.8, d) * tint.a * (u_focusing > 0.5 ? 0.15 : 0.6) * u_fade;
      color = vec4(tint.rgb * a, a);
    }`,
  );

  // Copies the cached still graph to the screen: one triangle covering the
  // viewport, sampling the resolved texture pixel for pixel.
  const copyProgram = compile(
    `#version 300 es
    void main() {
      vec2 p = vec2(gl_VertexID == 1 ? 3.0 : -1.0, gl_VertexID == 2 ? 3.0 : -1.0);
      gl_Position = vec4(p, 0.0, 1.0);
    }`,
    `#version 300 es
    precision mediump float;
    uniform sampler2D u_image;
    out vec4 color;
    void main() { color = texelFetch(u_image, ivec2(gl_FragCoord.xy), 0); }`,
  );

  const sparkProgram = compile(
    `#version 300 es
    ${VIEW}
    in vec2 a_pos; in float a_alpha;
    out float v_alpha;
    void main() { gl_Position = vec4(a_pos * u_scale + u_offset, 0.0, 1.0); v_alpha = a_alpha; }`,
    `#version 300 es
    precision mediump float;
    uniform vec3 u_hot;
    in float v_alpha; out vec4 color;
    void main() { color = vec4(u_hot * v_alpha, v_alpha); }`,
  );

  const loc = (program, names) =>
    Object.fromEntries(
      names.map((n) => [n, n.startsWith("u_") ? gl.getUniformLocation(program, n) : gl.getAttribLocation(program, n)]),
    );
  const VIEW_NAMES = ["u_scale", "u_offset", "u_focusing"];
  const nodeLoc = loc(nodeProgram, [
    ...VIEW_NAMES,
    "u_px",
    "u_grow",
    "u_pass",
    "u_base",
    "u_hot",
    "u_ink",
    "a_pos",
    "a_size",
    "a_heat",
    "a_focus",
  ]);
  const edgeLoc = loc(edgeProgram, [
    ...VIEW_NAMES,
    "u_extras",
    "u_pass",
    "u_k",
    "u_ink",
    "u_hot",
    "u_alpha",
    "u_time",
    "u_traceAlpha",
    "a_pos",
    "a_end",
    "a_focus",
    "a_kind",
    "a_len",
  ]);
  const sparkLoc = loc(sparkProgram, [...VIEW_NAMES, "u_hot", "a_pos", "a_alpha"]);
  // A bead at the head of each pulse. A line is one pixel wide whatever is
  // asked of it, and a one-pixel pulse is easy to lose once zooming has
  // stretched the lines across the view; a dot is not.
  const beadProgram = compile(
    `#version 300 es
    ${VIEW}
    uniform float u_size;
    in vec2 a_pos;
    void main() { gl_Position = vec4(a_pos * u_scale + u_offset, 0.0, 1.0); gl_PointSize = u_size; }`,
    `#version 300 es
    precision highp float;
    uniform vec3 u_hot; uniform float u_alpha;
    out vec4 color;
    void main() {
      vec2 c = gl_PointCoord * 2.0 - 1.0;
      float d = dot(c, c);
      if (d > 1.0) discard;
      float a = u_alpha * (1.0 - smoothstep(0.5, 1.0, d));
      color = vec4(u_hot * a, a);
    }`,
  );
  const beadLoc = loc(beadProgram, [...VIEW_NAMES, "u_size", "u_hot", "u_alpha", "a_pos"]);
  const cloudLoc = loc(cloudProgram, [...VIEW_NAMES, "u_extent", "u_density", "u_tints", "u_fade"]);

  // Graph data. Nodes are in runtime rank order; the first `ranked` clear the
  // minimum on runtime dependents, the rest only once extras count. `pairs`
  // is every edge, runtime first, as dependent/dependency index pairs, and
  // `kind[k]` is 1 for an edge that only exists with extras switched on.
  let graph, count, ranked, edgeCount, names, pos, pairs, kind, byName, grid, orderAll, nameWidths;
  let down, downStart, up, upStart;
  let heat, nodeFocus;
  let extras = false;
  const buffers = {};

  const visible = (i) => extras || i < ranked;
  const dependentsOf = (i) => (extras ? graph.dependents_all[i] : graph.dependents[i]);
  // Dot diameter from the full dependent count, not the slice drawn here.
  // Square root, so a dot's area tracks its count: numpy at 95,000 is a
  // landmark of about 35px, a project with a handful barely a speck. A log
  // scale drew both within a few pixels of each other.
  const sizeOf = (i) => 1.4 + 0.11 * Math.sqrt(dependentsOf(i));
  // Zooming in adds the same few pixels to every dot rather than multiplying
  // them, so small projects become clickable without hubs swallowing the view.
  const growth = () => (zoomGrowth() - 1) * 1.6;
  const dotPx = (i) => sizeOf(i) + growth();

  function buildIndexes(graph) {
    names = graph.names;
    count = names.length;
    ranked = graph.ranked;
    byName = new Map(names.map((n, i) => [n, i]));
    pos = new Float32Array(count * 2);
    for (let i = 0; i < count; i++) {
      pos[2 * i] = graph.x[i];
      pos[2 * i + 1] = graph.y[i];
    }
    ({ pairs, kind, edgeCount } = decodeEdges(graph));
    // Decoded into typed arrays above; the boxed JSON copies are let go.
    for (const field of ["x", "y", "edges", "extra_edges"]) delete graph[field];
    heat = new Float32Array(count);
    // Measured once each, on first placement: the label font never changes.
    nameWidths = new Float32Array(count);
    nodeFocus = new Float32Array(count);
    // Label priority with extras on: the count the dots are then sized by,
    // ties broken by name, as the ranking breaks them.
    orderAll = Uint32Array.from({ length: count }, (_, i) => i).sort(
      (a, b) => graph.dependents_all[b] - graph.dependents_all[a] || (names[a] < names[b] ? -1 : 1),
    );

    // A coarse grid over data space, for finding the node under the pointer.
    const cells = 128;
    const buckets = Array.from({ length: cells * cells }, () => []);
    const cellOf = (v) => cellIndex(v, graph.extent, cells);
    for (let i = 0; i < count; i++) buckets[cellOf(pos[2 * i + 1]) * cells + cellOf(pos[2 * i])].push(i);
    grid = { cells, buckets, cellOf };
  }

  const adjacencies = {};
  function setMode(on) {
    extras = on;
    sky = extras ? graph.extras_sky : graph.sky;
    paintSky();
    listClouds();
    // Built once per mode: switching back and forth reuses them.
    const edges = { pairs, kind, edgeCount };
    adjacencies[on] ??= [adjacency(edges, count, on, 1, 0), adjacency(edges, count, on, 0, 1)];
    [[down, downStart], [up, upStart]] = adjacencies[on];
    gl.bindBuffer(gl.ARRAY_BUFFER, buffers.size);
    gl.bufferData(
      gl.ARRAY_BUFFER,
      Float32Array.from({ length: count }, (_, i) => (visible(i) ? sizeOf(i) : 0)),
      gl.STATIC_DRAW,
    );
    if (selected >= 0 && !visible(selected)) selected = -1;
    select(selected, { quiet: true });
    renderMinimapBase();
    statusLine.textContent =
      `${fmt(extras ? count : ranked)} projects and ${fmt(downStart[count])} dependencies between them` +
      (extras ? ", extras included." : ".");
    dirty = true;
  }

  // One edge's vertices, dependent `a` to dependency `b`, written at `slot`:
  // both ends' positions, which end is the dependency, and the line's length
  // for spacing its pulses.
  function writeEdge(a, b, slot, out) {
    out.pos[4 * slot] = pos[2 * a];
    out.pos[4 * slot + 1] = pos[2 * a + 1];
    out.pos[4 * slot + 2] = pos[2 * b];
    out.pos[4 * slot + 3] = pos[2 * b + 1];
    out.ends[2 * slot + 1] = 1;
    out.lens[2 * slot] = out.lens[2 * slot + 1] = Math.hypot(pos[2 * b] - pos[2 * a], pos[2 * b + 1] - pos[2 * a + 1]);
  }
  const edgeArrays = (n) => ({
    pos: new Float32Array(n * 4),
    ends: new Float32Array(n * 2),
    lens: new Float32Array(n * 2),
  });

  function upload() {
    const buffer = (data, usage = gl.STATIC_DRAW) => {
      const b = gl.createBuffer();
      gl.bindBuffer(gl.ARRAY_BUFFER, b);
      gl.bufferData(gl.ARRAY_BUFFER, data, usage);
      return b;
    };
    buffers.pos = buffer(pos);
    buffers.size = buffer(new Float32Array(count));
    buffers.heat = buffer(heat, gl.DYNAMIC_DRAW);
    buffers.nodeFocus = buffer(nodeFocus, gl.DYNAMIC_DRAW);
    // Edges are drawn unindexed: each end needs its own `a_end`, which a vertex
    // shared through an index buffer cannot have.
    const edges = edgeArrays(edgeCount);
    const kinds = new Float32Array(edgeCount * 2);
    for (let k = 0; k < edgeCount; k++) {
      writeEdge(pairs[2 * k], pairs[2 * k + 1], k, edges);
      kinds[2 * k] = kinds[2 * k + 1] = kind[k];
    }
    buffers.edgePos = buffer(edges.pos);
    buffers.ends = buffer(edges.ends);
    buffers.kinds = buffer(kinds);
    buffers.lens = buffer(edges.lens);
    buffers.spark = gl.createBuffer();
    buffers.sky = gl.createTexture();
    buffers.skyTints = gl.createTexture();
    for (const texture of [buffers.sky, buffers.skyTints]) {
      gl.bindTexture(gl.TEXTURE_2D, texture);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
    }
    buffers.sparkAlpha = gl.createBuffer();
    buffers.beads = gl.createBuffer();
  }

  // Each cell of the cloud grid in its cloud's tint, and the rest in plain
  // muted ink; smooth filtering blends the seams. Repainted with the theme.
  let skyCells = null;
  // The sky the current mode draws: extras bring whole neighborhoods with
  // them -- type stubs, boto3's stubs -- so with extras on, the crowds are
  // found again over every drawn project.
  let sky = null;
  function paintSky() {
    const { grid: size, clouds: named } = sky;
    gl.bindTexture(gl.TEXTURE_2D, buffers.sky);
    gl.pixelStorei(gl.UNPACK_ALIGNMENT, 1);
    gl.texImage2D(gl.TEXTURE_2D, 0, gl.R8, size, size, 0, gl.RED, gl.UNSIGNED_BYTE, Uint8Array.from(sky.density));
    skyCells = spreadCells(sky.cells, size, 4);
    const rgba = new Uint8Array(size * size * 4);
    skyCells.forEach((cloud, n) => {
      // Plain haze is fainter than a named cloud's, so the clouds stand out.
      const rgb = cloud ? colors.nebula[named[cloud - 1].tint % colors.nebula.length] : colors.muted;
      rgba.set([...rgb.map((v) => Math.round(v * 255)), cloud ? 255 : 110], n * 4);
    });
    gl.bindTexture(gl.TEXTURE_2D, buffers.skyTints);
    gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA8, size, size, 0, gl.RGBA, gl.UNSIGNED_BYTE, rgba);
  }

  // Camera: `k` is CSS pixels per data unit, (cx, cy) the data point at center.
  const view = { cx: 0, cy: 0, k: 1 };
  let width = 0,
    height = 0,
    dpr = 1;
  let home;
  let dirty = true;

  const homeView = () => ({
    cx: graph.extent / 2,
    cy: graph.extent / 2,
    k: Math.min((width - 48) / graph.extent, (height - 48) / graph.extent),
  });
  function fit() {
    home = homeView();
    Object.assign(view, home);
    dirty = true;
  }

  // The still graph -- every edge and dot -- is drawn into a multisampled
  // target only when something about it changes, resolved into a texture, and
  // that texture copied to the screen each frame. Redrawing 380,000 edges
  // sixty times a second to move a few glowing dots is what made the page slow
  // to first paint. The resolve goes through a texture because resolving a
  // multisampled buffer straight onto the canvas silently draws nothing on
  // some drivers.
  let target = null;
  function makeTarget() {
    if (target) {
      gl.deleteFramebuffer(target.fbo);
      gl.deleteRenderbuffer(target.color);
      gl.deleteRenderbuffer(target.depth);
      gl.deleteFramebuffer(target.resolved);
      gl.deleteTexture(target.texture);
    }
    const samples = Math.min(4, gl.getParameter(gl.MAX_SAMPLES));
    const storage = (format) => {
      const rb = gl.createRenderbuffer();
      gl.bindRenderbuffer(gl.RENDERBUFFER, rb);
      gl.renderbufferStorageMultisample(gl.RENDERBUFFER, samples, format, canvas.width, canvas.height);
      return rb;
    };
    target = { fbo: gl.createFramebuffer(), color: storage(gl.RGBA8), depth: storage(gl.DEPTH_COMPONENT24) };
    gl.bindFramebuffer(gl.FRAMEBUFFER, target.fbo);
    gl.framebufferRenderbuffer(gl.FRAMEBUFFER, gl.COLOR_ATTACHMENT0, gl.RENDERBUFFER, target.color);
    gl.framebufferRenderbuffer(gl.FRAMEBUFFER, gl.DEPTH_ATTACHMENT, gl.RENDERBUFFER, target.depth);
    target.texture = gl.createTexture();
    gl.bindTexture(gl.TEXTURE_2D, target.texture);
    gl.texStorage2D(gl.TEXTURE_2D, 1, gl.RGBA8, canvas.width, canvas.height);
    target.resolved = gl.createFramebuffer();
    gl.bindFramebuffer(gl.FRAMEBUFFER, target.resolved);
    gl.framebufferTexture2D(gl.FRAMEBUFFER, gl.COLOR_ATTACHMENT0, gl.TEXTURE_2D, target.texture, 0);
    const complete = gl.checkFramebufferStatus(gl.FRAMEBUFFER) === gl.FRAMEBUFFER_COMPLETE;
    gl.bindFramebuffer(gl.FRAMEBUFFER, target.fbo);
    if (!complete || gl.checkFramebufferStatus(gl.FRAMEBUFFER) !== gl.FRAMEBUFFER_COMPLETE) {
      fail("This display is too large for the graph's drawing buffer. Reload the page in a smaller window.");
    }
    gl.bindFramebuffer(gl.FRAMEBUFFER, null);
  }

  // Said where the status line was, visibly: the graph will not draw.
  function fail(message) {
    statusLine.textContent = message;
    statusLine.classList.remove("sr-only");
    graph = null;
  }
  canvas.addEventListener("webglcontextlost", (e) => {
    e.preventDefault();
    fail("The graph's drawing surface was lost. Reload the page to draw it again.");
  });

  function resize() {
    const rect = surface.getBoundingClientRect();
    width = rect.width;
    height = rect.height;
    // Sharp on a high-density screen, but capped near sixteen million pixels:
    // the multisampled buffer is four times that, and a 5K display at full
    // density would ask for half a gigabyte.
    dpr = Math.min(window.devicePixelRatio || 1, 2, Math.sqrt(16e6 / Math.max(width * height, 1)));
    for (const c of [canvas, labels]) {
      c.width = Math.round(width * dpr);
      c.height = Math.round(height * dpr);
    }
    makeTarget();
    if (graph) {
      // A reader still at the overview stays at it; one who has zoomed in
      // keeps their place.
      const atHome = home && view.k === home.k && view.cx === home.cx && view.cy === home.cy;
      home = homeView();
      if (atHome) Object.assign(view, home);
      // The navigator's image depends on pixel density, not on the surface's
      // size, so dragging a window edge does not redraw 57,000 dots per step.
      if (miniBase?.width !== MINI * miniScale()) renderMinimapBase();
    }
    dirty = true;
  }

  const toScreen = (x, y) => [(x - view.cx) * view.k + width / 2, (y - view.cy) * view.k + height / 2];
  const toData = (sx, sy) => [(sx - width / 2) / view.k + view.cx, (sy - height / 2) / view.k + view.cy];
  // Dots grow a little as the reader zooms in, but far slower than the space
  // between them, so a dense cluster opens up rather than staying a blot.
  const zoomGrowth = () => Math.min(4, Math.max(1, Math.sqrt(view.k / home.k)));

  function useProgram(program, l) {
    gl.useProgram(program);
    gl.uniform2f(l.u_scale, (2 * view.k) / width, (-2 * view.k) / height);
    gl.uniform2f(l.u_offset, (-2 * view.k * view.cx) / width, (2 * view.k * view.cy) / height);
    gl.uniform1f(l.u_focusing, selected >= 0 ? 1 : 0);
  }

  function attrib(location, buffer, size) {
    gl.bindBuffer(gl.ARRAY_BUFFER, buffer);
    gl.enableVertexAttribArray(location);
    gl.vertexAttribPointer(location, size, gl.FLOAT, false, 0, 0);
  }

  // The selection: every project it reaches either way, and the edges that lie
  // on a shortest path to each of them. Cross edges are left dim, or numpy's
  // trace would be every line on the page.
  let selected = -1;
  let trace = null;
  let traceAlpha = 1;
  let tracedCount = 0,
    tracedEdges,
    tracedFocus;
  function focus(origin) {
    nodeFocus.fill(0);
    trace = null;
    tracedCount = 0;
    if (origin < 0) return;
    // The counts are always the whole reach; only what is drawn stops short.
    const downAll = reach(origin, down, downStart);
    const upAll = reach(origin, up, upStart);
    const near = (depths) =>
      hops() === Number.POSITIVE_INFINITY ? depths : new Map([...depths].filter(([, d]) => d <= hops()));
    const downDepth = near(downAll);
    const upDepth = near(upAll);
    for (const [i, d] of downDepth) nodeFocus[i] = d;
    for (const [i, d] of upDepth) if (d) nodeFocus[i] = -d;
    nodeFocus[origin] = 0.5;
    // The traced edges, as dependent, dependency, focus, found by walking the
    // adjacency from the projects reached rather than scanning every edge. An
    // edge on a cycle could step both ways; it is drawn as a step down.
    const traced = [];
    for (const [b, d] of downDepth) {
      for (let k = downStart[b]; k < downStart[b + 1]; k++) {
        if (downDepth.get(down[k]) === d + 1) traced.push(down[k], b, d + 1);
      }
    }
    for (const [a, d] of upDepth) {
      for (let k = upStart[a]; k < upStart[a + 1]; k++) {
        const b = up[k];
        if (upDepth.get(b) === d + 1 && downDepth.get(a) !== downDepth.get(b) + 1) traced.push(a, b, -(d + 1));
      }
    }
    // Drawn over the dimmed still graph, and alone in the animated pass:
    // drawing all 380,000 edges every frame to discard most of them undid the
    // point of caching the still graph.
    tracedCount = traced.length / 3;
    tracedEdges = edgeArrays(tracedCount);
    tracedFocus = new Float32Array(tracedCount * 2);
    for (let t = 0; t < tracedCount; t++) {
      writeEdge(traced[3 * t], traced[3 * t + 1], t, tracedEdges);
      tracedFocus[2 * t] = tracedFocus[2 * t + 1] = traced[3 * t + 2];
    }
    // A few hundred traced lines can each be drawn strong; numpy's ten
    // thousand would be a solid green disc, so they thin as they multiply.
    traceAlpha = Math.min(1, Math.sqrt(400 / Math.max(tracedCount, 1)));
    trace = {
      down: downAll.size - 1,
      up: upAll.size - 1,
      // The origin is in both walks, and so is anything on a cycle with it;
      // each is listed once.
      nodes: Uint32Array.from(new Set([...upDepth.keys(), ...downDepth.keys()])).sort(),
    };
  }

  function uploadFocus() {
    if (!buffers.tracedPos) {
      for (const name of ["tracedPos", "tracedEnds", "tracedFocus", "tracedLens"]) buffers[name] = gl.createBuffer();
    }
    if (tracedCount) {
      for (const [name, data] of [
        ["tracedPos", tracedEdges.pos],
        ["tracedEnds", tracedEdges.ends],
        ["tracedFocus", tracedFocus],
        ["tracedLens", tracedEdges.lens],
      ]) {
        gl.bindBuffer(gl.ARRAY_BUFFER, buffers[name]);
        gl.bufferData(gl.ARRAY_BUFFER, data, gl.DYNAMIC_DRAW);
      }
    }
    gl.bindBuffer(gl.ARRAY_BUFFER, buffers.nodeFocus);
    gl.bufferSubData(gl.ARRAY_BUFFER, 0, nodeFocus);
  }

  // Cascade state for releases: when each lit node lights, and how bright.
  const litAt = new Map();
  let sparks = [];

  function cascade(origin, now) {
    const seen = new Set([origin]);
    let frontier = [origin];
    litAt.set(origin, { t: now, amp: 1 });
    let lit = 1;
    for (let depth = 1; depth <= Math.min(MAX_DEPTH, hops()) && frontier.length && lit < MAX_LIT; depth++) {
      const next = [];
      const t = now + depth * HOP_MS;
      const amp = DECAY ** depth;
      for (const from of frontier) {
        for (let k = downStart[from]; k < downStart[from + 1] && lit < MAX_LIT; k++) {
          const to = down[k];
          if (seen.has(to)) continue;
          seen.add(to);
          next.push(to);
          lit++;
          const prior = litAt.get(to);
          if (!prior || prior.t + GLOW_MS < t) litAt.set(to, { t, amp });
          sparks.push({ from, to, t: t - HOP_MS, amp });
        }
      }
      frontier = next;
    }
    return seen.size - 1;
  }

  // Uploaded only while something glows, and once more to clear the last of
  // it: a selection animates every frame, and most of those light nothing.
  let heatLive = false;
  function updateHeat(now) {
    if (!litAt.size && !heatLive) return;
    heatLive = litAt.size > 0;
    for (const [i, { t, amp }] of litAt) {
      const age = now - t;
      if (age < 0) {
        heat[i] = 0;
        continue;
      }
      if (age > GLOW_MS) {
        heat[i] = 0;
        litAt.delete(i);
        continue;
      }
      heat[i] = amp * (1 - age / GLOW_MS) ** 2;
    }
    gl.bindBuffer(gl.ARRAY_BUFFER, buffers.heat);
    gl.bufferSubData(gl.ARRAY_BUFFER, 0, heat);
  }

  // The edges a release pulse is traveling along this frame, drawn as a line
  // that brightens toward the dependent as it arrives.
  function drawSparks(now) {
    // Pruned in one pass: splicing one at a time went quadratic once a few
    // big cascades had queued thousands.
    sparks = sparks.filter((s) => now - s.t <= HOP_MS + GLOW_MS * 0.5);
    const live = sparks.filter((s) => now >= s.t);
    if (!live.length) return;
    const xy = new Float32Array(live.length * 4);
    const alpha = new Float32Array(live.length * 2);
    live.forEach((s, n) => {
      const age = now - s.t;
      const fade = age < HOP_MS ? age / HOP_MS : 1 - (age - HOP_MS) / (GLOW_MS * 0.5);
      xy.set([pos[2 * s.from], pos[2 * s.from + 1], pos[2 * s.to], pos[2 * s.to + 1]], n * 4);
      alpha[n * 2] = 0.15 * s.amp * fade;
      alpha[n * 2 + 1] = 0.9 * s.amp * fade;
    });
    useProgram(sparkProgram, sparkLoc);
    gl.uniform3fv(sparkLoc.u_hot, colors.accent);
    gl.bindBuffer(gl.ARRAY_BUFFER, buffers.spark);
    gl.bufferData(gl.ARRAY_BUFFER, xy, gl.STREAM_DRAW);
    attrib(sparkLoc.a_pos, buffers.spark, 2);
    gl.bindBuffer(gl.ARRAY_BUFFER, buffers.sparkAlpha);
    gl.bufferData(gl.ARRAY_BUFFER, alpha, gl.STREAM_DRAW);
    attrib(sparkLoc.a_alpha, buffers.sparkAlpha, 1);
    gl.drawArrays(gl.LINES, 0, live.length * 2);
  }

  // Label helpers. Every name on the graph is drawn over a paper halo, so it
  // reads across lines and haze; `taken` holds the boxes already placed, as
  // [left, middle, width], and a label that would crowd one is not drawn.
  const onScreen = (sx, sy) => sx >= 0 && sy >= 0 && sx <= width && sy <= height;
  const clears = (taken, x, y, w, pad, rows) =>
    !taken.some((r) => x < r[0] + r[2] + pad && x + w + pad > r[0] && Math.abs(y - r[1]) < rows);
  function halo(text, x, y, lineWidth, ink, paper) {
    ctx.lineWidth = lineWidth;
    ctx.strokeStyle = paper;
    ctx.strokeText(text, x, y);
    ctx.fillStyle = ink;
    ctx.fillText(text, x, y);
  }
  // A centered serif name, as clouds and the belt are labeled; returns the box
  // to record in `taken`, or nothing when `room` says it would crowd one.
  function serifName(text, sx, sy, px, lineWidth, ink, paper, room) {
    ctx.save();
    ctx.textAlign = "center";
    ctx.font = `italic 400 ${px}px ${colors.serif}`;
    const w = ctx.measureText(text).width;
    const box = [sx - w / 2, sy, w];
    const fits = !room || room(...box);
    if (fits) halo(text, sx, sy, lineWidth, ink, paper);
    ctx.restore();
    return fits ? box : null;
  }

  function drawLabels() {
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, width, height);
    ctx.font = `500 12px ${colors.font}`;
    ctx.textBaseline = "middle";
    ctx.lineJoin = "round";
    const taken = [];
    const place = (i, strong) => {
      const [sx, sy] = toScreen(pos[2 * i], pos[2 * i + 1]);
      if (!onScreen(sx, sy)) return false;
      nameWidths[i] ||= ctx.measureText(names[i]).width;
      const w = nameWidths[i];
      // Flipped to the left of the dot near the right edge, so a name is not
      // cut off by the frame it is meant to sit inside.
      const gap = dotPx(i) / 2 + 4;
      const x = sx + gap + w > width - 4 ? sx - gap - w : sx + gap;
      if (!strong && !clears(taken, x, sy, w, 6, 15)) return false;
      taken.push([x, sy, w]);
      halo(names[i], x, sy, 3, css(strong ? colors.ink : colors.muted), css(colors.paper));
      return true;
    };
    if (selected >= 0) place(selected, true);
    // The cloud names are the map while nothing is traced.
    const mapping = cloudsOn() && !trace;
    if (mapping) {
      pinCloud(taken);
      nameBelt(taken);
      nameClouds(taken);
    }
    // Names in rank order, fewer when zoomed out: a dozen recognizable names
    // read as a map, fifty crowded ones read as noise. While tracing, only
    // projects on the trace are named.
    const order = trace ? trace.nodes : extras ? orderAll : null;
    const limit = order ? Math.min(order.length, 6000) : Math.min(ranked, 4000);
    let budget = Math.round((12 * zoomGrowth() ** 2 * width * height) / 1e6) + 8;
    // At the overview the cloud names are the map; a handful of the best-known
    // projects is enough beside them, and the rest return as they fade.
    if (mapping && sky.clouds.length) budget = Math.round(budget * (1 - 0.7 * cloudFade()));
    let shown = 0;
    for (let n = 0; n < limit && shown < budget; n++) {
      const i = order ? order[n] : n;
      if (i !== selected && visible(i) && place(i, false)) shown++;
    }
  }

  // Cloud names, at the overview: italic serif, quieter than any project, and
  // gone by the time the reader has zoomed in far enough to read the projects
  // themselves. Placed first, so project names make room for them.
  const cloudFade = () => Math.min(1, Math.max(0, 2 - zoomGrowth()));
  function nameClouds(taken) {
    const fade = cloudFade();
    if (!fade) return;
    const largest = sky.clouds[0]?.projects;
    // Clouds arrive largest first, so where two names would collide the
    // smaller cloud gives way.
    const room = (x, y, w) => clears(taken, x, y, w, 8, 20);
    for (const cloud of sky.clouds) {
      const [sx, sy] = toScreen(cloud.x, cloud.y);
      if (!onScreen(sx, sy)) continue;
      const px = Math.round(13 + 5 * Math.sqrt(cloud.projects / largest));
      const box = serifName(cloud.name, sx, sy, px, 4, css(colors.muted, fade), css(colors.paper, 0.8 * fade), room);
      if (box) taken.push(box);
    }
  }

  // The belt is a ring, so its name sits on the stretch of it nearest the
  // middle of the view -- the top at the overview, and wherever the reader
  // meets it once zoomed in. It never fades: the rim stays the rim.
  function nameBelt(taken) {
    const { belt } = sky;
    if (!belt) return;
    const c = graph.extent / 2;
    const dx = view.cx - c,
      dy = view.cy - c;
    const d = Math.hypot(dx, dy);
    const [ux, uy] = d > belt.radius * 0.2 ? [dx / d, dy / d] : [0, -1];
    const [sx, sy] = toScreen(c + ux * belt.radius, c + uy * belt.radius);
    if (!onScreen(sx, sy)) return;
    taken.push(serifName(belt.name, sx, sy, 14, 4, css(colors.muted), css(colors.paper, 0.8)));
  }

  // Zoomed in past the overview names, the cloud the view is over keeps its
  // name, large and faint across the top of the view, where project names
  // make room for it: at its centroid it was buried under them.
  function pinCloud(taken) {
    const strength = 1 - cloudFade();
    if (!strength) return;
    const { grid: size, clouds } = sky;
    const n = cloudAt(skyCells, size, graph.extent, view.cx, view.cy);
    if (!n) return;
    const cloud = clouds[n - 1];
    const alpha = Math.min(1, strength * 2);
    taken.push(
      serifName(cloud.name, width / 2, 38, 30, 6, css(colors.muted, 0.6 * alpha), css(colors.paper, 0.7 * alpha)),
    );
  }

  // Each cloud in the key, with what lives there and its best-known projects.
  // Choosing one frames its densest cells.
  function listClouds() {
    const { grid: size, clouds, cells, belt } = sky;
    $("cloud-key").hidden = !clouds.length;
    if (!clouds.length) return;
    const items = clouds.map((cloud, n) => {
      const button = el("button", { type: "button", className: "jump", textContent: cloud.name });
      button.addEventListener("click", () => {
        cloudsToggle.checked = true;
        frameBox(...cloudBounds(cells, size, graph.extent, n + 1), { margin: 0.8, most: 16 });
        statusLine.textContent = `${cloud.name}: ${cloud.about}`;
      });
      return el(
        "li",
        {},
        button,
        el("span", { className: "meta", textContent: `${cloud.about} Best known: ${cloud.top.join(", ")}.` }),
      );
    });
    if (belt) {
      items.push(
        el(
          "li",
          {},
          el("strong", { textContent: belt.name }),
          el("span", { className: "meta", textContent: belt.about }),
        ),
      );
    }
    $("cloud-list").replaceChildren(...items);
  }

  // The navigator: every visible dot, drawn once into a cached image whenever
  // the mode, the theme or the size changes, then the viewport outlined on top
  // of it whenever the view moves.
  const MINI = 136;
  let miniBase = null;
  const miniScale = () => Math.min(window.devicePixelRatio || 1, 2);
  function renderMinimapBase() {
    if (!graph) return;
    const scale = miniScale();
    minimap.width = minimap.height = MINI * scale;
    miniBase = document.createElement("canvas");
    miniBase.width = miniBase.height = MINI * scale;
    const c = miniBase.getContext("2d");
    const f = (MINI * scale) / graph.extent;
    c.fillStyle = css(colors.muted, 0.35);
    for (let i = 0; i < count; i++) {
      if (!visible(i)) continue;
      const r = Math.max(0.6, sizeOf(i) * 0.1) * scale;
      c.fillRect(pos[2 * i] * f - r / 2, pos[2 * i + 1] * f - r / 2, r, r);
    }
    dirty = true;
  }
  function drawMinimap() {
    if (!miniBase || narrow.matches) return;
    const s = minimap.width;
    const f = s / graph.extent;
    mctx.clearRect(0, 0, s, s);
    mctx.drawImage(miniBase, 0, 0);
    if (selected >= 0) {
      mctx.fillStyle = css(colors.accent);
      mctx.beginPath();
      mctx.arc(pos[2 * selected] * f, pos[2 * selected + 1] * f, 3 * (s / MINI), 0, Math.PI * 2);
      mctx.fill();
    }
    const [x0, y0] = toData(0, 0);
    const [x1, y1] = toData(width, height);
    mctx.strokeStyle = css(colors.ink);
    mctx.lineWidth = s / MINI;
    mctx.strokeRect(x0 * f, y0 * f, (x1 - x0) * f, (y1 - y0) * f);
  }

  function animating() {
    return litAt.size > 0 || sparks.length > 0 || (selected >= 0 && moving());
  }

  function drawEdges(pass, now) {
    useProgram(edgeProgram, edgeLoc);
    gl.uniform1f(edgeLoc.u_pass, pass);
    gl.uniform1f(edgeLoc.u_extras, extras ? 1 : 0);
    gl.uniform3fv(edgeLoc.u_ink, colors.ink);
    gl.uniform3fv(edgeLoc.u_hot, colors.accent);
    // Faint enough at the overview that the core reads as density, not as a
    // solid knot of ink; lines strengthen as zooming thins them out.
    gl.uniform1f(edgeLoc.u_alpha, Math.min(0.14, 0.012 * zoomGrowth() ** 2));
    gl.uniform1f(edgeLoc.u_traceAlpha, traceAlpha);
    gl.uniform1f(edgeLoc.u_time, pass ? pulsePhase(now) : 0.5);
    gl.uniform1f(edgeLoc.u_k, view.k);
    // A constant where every vertex of a draw shares the value: the whole
    // graph is untraced, and every traced edge is drawn in the current mode.
    const constant = (location, value) => {
      gl.disableVertexAttribArray(location);
      gl.vertexAttrib1f(location, value);
    };
    if (!pass) {
      attrib(edgeLoc.a_pos, buffers.edgePos, 2);
      attrib(edgeLoc.a_end, buffers.ends, 1);
      attrib(edgeLoc.a_kind, buffers.kinds, 1);
      constant(edgeLoc.a_focus, 0);
      attrib(edgeLoc.a_len, buffers.lens, 1);
      gl.drawArrays(gl.LINES, 0, edgeCount * 2);
    }
    if (!tracedCount) return;
    attrib(edgeLoc.a_pos, buffers.tracedPos, 2);
    attrib(edgeLoc.a_end, buffers.tracedEnds, 1);
    constant(edgeLoc.a_kind, 0);
    attrib(edgeLoc.a_focus, buffers.tracedFocus, 1);
    attrib(edgeLoc.a_len, buffers.tracedLens, 1);
    gl.drawArrays(gl.LINES, 0, tracedCount * 2);
  }

  // The beads ride the downstream lines where the edge shader puts its pulse
  // heads, capped in all so a hub's trace stays cheap to animate.
  const MAX_BEADS = 6000;
  const beadXY = new Float32Array(MAX_BEADS * 2);
  function drawBeads(now) {
    const phase = pulsePhase(now);
    const { pos: xy, lens } = tracedEdges;
    let n = 0;
    for (let t = 0; t < tracedCount && n < MAX_BEADS; t++) {
      if (tracedFocus[2 * t] <= 0) continue;
      const repeat = Math.min(MAX_PULSES, Math.max(1, Math.floor((lens[2 * t] * view.k) / PULSE_PX)));
      const ax = xy[4 * t];
      const ay = xy[4 * t + 1];
      const bx = xy[4 * t + 2];
      const by = xy[4 * t + 3];
      for (let j = 0; j < repeat && n < MAX_BEADS; j++) {
        // 1 at the dependency, 0 at the dependent: a release travels outward.
        const e = (j + 1 - phase) / repeat;
        beadXY[2 * n] = ax + (bx - ax) * e;
        beadXY[2 * n + 1] = ay + (by - ay) * e;
        n++;
      }
    }
    if (!n) return;
    useProgram(beadProgram, beadLoc);
    gl.uniform1f(beadLoc.u_size, (3 + zoomGrowth()) * dpr);
    gl.uniform3fv(beadLoc.u_hot, colors.accent);
    gl.uniform1f(beadLoc.u_alpha, Math.max(0.4, traceAlpha));
    gl.bindBuffer(gl.ARRAY_BUFFER, buffers.beads);
    gl.bufferData(gl.ARRAY_BUFFER, beadXY.subarray(0, 2 * n), gl.STREAM_DRAW);
    attrib(beadLoc.a_pos, buffers.beads, 2);
    gl.drawArrays(gl.POINTS, 0, n);
  }

  function drawClouds() {
    useProgram(cloudProgram, cloudLoc);
    gl.uniform1f(cloudLoc.u_extent, graph.extent);
    // Thinned as the reader zooms in: inside a cloud its haze is the whole
    // view, and at full strength it washes out the projects.
    gl.uniform1f(cloudLoc.u_fade, 0.3 + 0.7 * cloudFade());
    gl.uniform1i(cloudLoc.u_density, 0);
    gl.uniform1i(cloudLoc.u_tints, 1);
    gl.activeTexture(gl.TEXTURE1);
    gl.bindTexture(gl.TEXTURE_2D, buffers.skyTints);
    gl.activeTexture(gl.TEXTURE0);
    gl.bindTexture(gl.TEXTURE_2D, buffers.sky);
    gl.drawArrays(gl.TRIANGLE_STRIP, 0, 4);
  }

  function drawNodes(pass) {
    useProgram(nodeProgram, nodeLoc);
    gl.uniform1f(nodeLoc.u_pass, pass);
    gl.uniform1f(nodeLoc.u_px, dpr);
    gl.uniform1f(nodeLoc.u_grow, growth());
    gl.uniform3fv(nodeLoc.u_base, colors.muted);
    gl.uniform3fv(nodeLoc.u_hot, colors.accent);
    gl.uniform3fv(nodeLoc.u_ink, colors.ink);
    attrib(nodeLoc.a_pos, buffers.pos, 2);
    attrib(nodeLoc.a_size, buffers.size, 1);
    attrib(nodeLoc.a_heat, buffers.heat, 1);
    attrib(nodeLoc.a_focus, buffers.nodeFocus, 1);
    gl.drawArrays(gl.POINTS, 0, count);
  }

  function frame(now) {
    requestAnimationFrame(frame);
    if (!graph || (!dirty && !animating())) return;
    gl.enable(gl.BLEND);
    gl.blendFunc(gl.ONE, gl.ONE_MINUS_SRC_ALPHA);
    gl.viewport(0, 0, canvas.width, canvas.height);

    if (dirty) {
      dirty = false;
      gl.bindFramebuffer(gl.FRAMEBUFFER, target.fbo);
      gl.clearColor(...colors.paper, 1);
      gl.clear(gl.COLOR_BUFFER_BIT | gl.DEPTH_BUFFER_BIT);
      drawEdges(0, now);
      // Over the edges and under the dots: the haze veils the lines the way a
      // nebula does, and never hides a project.
      if (cloudsOn()) drawClouds();
      gl.enable(gl.DEPTH_TEST);
      drawNodes(0);
      gl.disable(gl.DEPTH_TEST);
      gl.bindFramebuffer(gl.READ_FRAMEBUFFER, target.fbo);
      gl.bindFramebuffer(gl.DRAW_FRAMEBUFFER, target.resolved);
      const { width: w, height: h } = canvas;
      gl.blitFramebuffer(0, 0, w, h, 0, 0, w, h, gl.COLOR_BUFFER_BIT, gl.NEAREST);
      gl.bindFramebuffer(gl.FRAMEBUFFER, null);
      drawLabels();
      drawMinimap();
    }

    gl.disable(gl.BLEND);
    gl.useProgram(copyProgram);
    gl.activeTexture(gl.TEXTURE0);
    gl.bindTexture(gl.TEXTURE_2D, target.texture);
    gl.drawArrays(gl.TRIANGLES, 0, 3);
    gl.enable(gl.BLEND);

    // Only what moves is drawn every frame.
    if (selected >= 0 && moving()) {
      drawEdges(1, now);
      drawBeads(now);
    }
    drawSparks(now);
    updateHeat(now);
    if (litAt.size) drawNodes(1);
  }

  function nodeAt(sx, sy) {
    const [x, y] = toData(sx, sy);
    const span = 24 / view.k;
    const { cells, buckets, cellOf } = grid;
    let best = -1,
      bestDist = Infinity;
    for (let gy = cellOf(y - span); gy <= cellOf(y + span); gy++) {
      for (let gx = cellOf(x - span); gx <= cellOf(x + span); gx++) {
        for (const i of buckets[gy * cells + gx]) {
          if (!visible(i)) continue;
          // While tracing, only projects on the trace answer the pointer.
          if (selected >= 0 && nodeFocus[i] === 0) continue;
          const dx = (pos[2 * i] - x) * view.k,
            dy = (pos[2 * i + 1] - y) * view.k;
          const dist = Math.hypot(dx, dy) - dotPx(i) / 2;
          if (dist < bestDist) {
            bestDist = dist;
            best = i;
          }
        }
      }
    }
    return bestDist <= 6 ? best : -1;
  }

  const rankText = (i) => (i < ranked ? `rank ${fmt(i + 1)}` : "ranked only with extras");
  const countsText = (i) => {
    const runtime = graph.dependents[i],
      every = graph.dependents_all[i];
    return every === runtime
      ? plural(runtime, "dependent")
      : `${plural(runtime, "dependent")}, ${fmt(every)} incl. extras`;
  };

  function el(tag, props = {}, ...children) {
    const node = Object.assign(document.createElement(tag), props);
    node.append(...children);
    return node;
  }
  // A heading and the first few names, ranked; the rest are one click away,
  // with a filter once there are more than can be scanned by eye.
  function jumpList(heading, items) {
    if (!items.length) return [];
    const list = el("ol");
    const capped = el("p", { className: "meta", hidden: true });
    const fill = (subset) => {
      capped.hidden = subset.length <= LIST_MAX || subset.length <= LIST_LIMIT;
      capped.textContent = `The first ${fmt(LIST_MAX)} of ${fmt(subset.length)} are listed; filter to find the rest.`;
      list.replaceChildren(
        ...subset.slice(0, LIST_MAX).map((i) => {
          const button = el("button", { type: "button", className: "jump", textContent: names[i] });
          // The panel is rebuilt for the new selection, taking this button with
          // it; focus moves to the new project's name rather than to the page.
          button.addEventListener("click", () => {
            select(i, { fly: true });
            $("detail-name")?.focus();
          });
          return el("li", {}, button, el("span", { className: "meta", textContent: fmt(dependentsOf(i)) }));
        }),
      );
    };
    fill(items.slice(0, LIST_LIMIT));
    const parts = [el("h2", { textContent: `${heading} (${fmt(items.length)})` }), list, capped];
    if (items.length > LIST_LIMIT) {
      const label = items.length > LIST_MAX ? `Show the first ${fmt(LIST_MAX)}` : `Show all ${fmt(items.length)}`;
      const more = el("button", { type: "button", className: "jump", textContent: label });
      const moreItem = el("p", { className: "meta" }, more);
      more.addEventListener("click", () => {
        fill(items);
        moreItem.remove();
        // The button removes itself, so focus moves to the first name it
        // revealed, or to the filter when there is one.
        list.children[LIST_LIMIT]?.querySelector("button")?.focus();
        if (items.length > 50) {
          const filter = el("input", {
            type: "search",
            placeholder: `Filter ${heading.toLowerCase()}`,
            autocomplete: "off",
          });
          filter.setAttribute("aria-label", `Filter ${heading.toLowerCase()}`);
          filter.addEventListener("input", () => {
            const wanted = canonical(filter.value);
            fill(wanted ? items.filter((i) => names[i].includes(wanted)) : items);
          });
          list.before(filter);
          filter.focus();
        }
      });
      parts.push(moreItem);
    }
    return parts;
  }

  function showDetail(i) {
    if (i < 0) {
      detail.hidden = true;
      detail.replaceChildren();
      return;
    }
    const link = pypiLink(names[i]);
    link.id = "detail-name";
    const close = el("button", { type: "button", className: "icon", textContent: "×" });
    close.setAttribute("aria-label", "Clear the selection");
    // Closing removes the panel and this button; focus returns to the graph.
    close.addEventListener("click", () => {
      select(-1);
      surface.focus();
    });
    const rank = rankText(i);
    detail.replaceChildren(
      el("div", { className: "detail-head" }, link, close),
      el("p", { className: "meta", textContent: `${rank[0].toUpperCase()}${rank.slice(1)} · ${countsText(i)}` }),
      el("p", {
        className: "meta",
        textContent: `A release reaches ${plural(trace.down, "drawn project")}. It depends on ${fmt(trace.up)} in all.`,
      }),
      ...jumpList("Depends on", [...up.subarray(upStart[i], upStart[i + 1])]),
      ...jumpList("Depended on by", [...down.subarray(downStart[i], downStart[i + 1])]),
    );
    detail.hidden = false;
  }

  // Fits a data-space box in the view, at least `least` units across so a
  // single point still gets some sky around it, and above the selection
  // sheet that covers the bottom of the graph on a narrow screen.
  function frameBox(x0, y0, x1, y1, { margin = 0.7, least = 0, most = 400 } = {}) {
    const sheet = narrow.matches && !detail.hidden ? detail.offsetHeight : 0;
    const fitK = margin * Math.min(width / Math.max(x1 - x0, least), (height - sheet) / Math.max(y1 - y0, least));
    view.k = Math.min(home.k * most, Math.max(home.k, fitK));
    view.cx = (x0 + x1) / 2;
    view.cy = (y0 + y1) / 2 + sheet / 2 / view.k;
    dirty = true;
  }

  function select(i, { fly = false, quiet = false } = {}) {
    // A list made with extras on can still name a project they alone draw.
    if (i >= 0 && !visible(i)) return;
    selected = i;
    focus(i);
    uploadFocus();
    showDetail(i);
    dirty = true;
    // The panel appears without taking focus, so the selection is said through
    // the page's one polite status region.
    if (!quiet) {
      statusLine.textContent =
        i < 0
          ? "Selection cleared."
          : `${names[i]}, ${rankText(i)}, ${countsText(i)}. A release reaches ${plural(trace.down, "drawn project")}.`;
    }
    if (i < 0) return;
    if (!quiet && moving()) cascade(i, performance.now());
    if (fly) {
      // Frame the whole trace. A family drawn together can sit closer than a
      // dot is wide, so a fixed zoom left a trace hidden under its own origin.
      // A trace of one -- a rim project, with no drawn neighbors -- has no
      // extent to fit.
      const box = bounds(Array.from(trace.nodes, (n) => [pos[2 * n], pos[2 * n + 1]]));
      frameBox(...box, { least: graph.extent * 0.04 });
    }
  }

  // Pointer: drag to pan, wheel or pinch to zoom, hover for a name, click to
  // select. A press that moved more than a few pixels is a drag, not a click.
  const pointers = new Map();
  let movedPointer = false,
    pinchFrom = null,
    pressedAt = null;

  function zoomAround(sx, sy, factor) {
    const [x, y] = toData(sx, sy);
    view.k = Math.min(home.k * 400, Math.max(home.k * 0.5, view.k * factor));
    view.cx = x - (sx - width / 2) / view.k;
    view.cy = y - (sy - height / 2) / view.k;
    dirty = true;
  }

  const local = (e) => {
    const r = surface.getBoundingClientRect();
    return [e.clientX - r.left, e.clientY - r.top];
  };
  const spread = () => {
    const [a, b] = [...pointers.values()];
    return Math.hypot(a[0] - b[0], a[1] - b[1]);
  };

  // Only a primary press or a lone finger selects: a right-click opens the
  // menu, and a two-finger tap is a gesture, not a choice.
  let multiTouch = false;
  surface.addEventListener("pointerdown", (e) => {
    if (e.pointerType === "mouse" && e.button !== 0) return;
    if (pointers.size >= 1) multiTouch = true;
    surface.setPointerCapture(e.pointerId);
    pointers.set(e.pointerId, local(e));
    pressedAt = local(e);
    movedPointer = false;
    if (pointers.size === 2) pinchFrom = spread();
  });
  surface.addEventListener("pointermove", (e) => {
    if (!graph) return;
    const [sx, sy] = local(e);
    const prior = pointers.get(e.pointerId);
    if (prior) {
      tip.hidden = true;
      if (!movedPointer && Math.hypot(sx - pressedAt[0], sy - pressedAt[1]) < 4) return;
      movedPointer = true;
      pointers.set(e.pointerId, [sx, sy]);
      if (pointers.size === 2) {
        const [a, b] = [...pointers.values()];
        const now = spread();
        zoomAround((a[0] + b[0]) / 2, (a[1] + b[1]) / 2, now / pinchFrom);
        pinchFrom = now;
      } else {
        surface.classList.add("dragging");
        view.cx -= (sx - prior[0]) / view.k;
        view.cy -= (sy - prior[1]) / view.k;
        dirty = true;
      }
      return;
    }
    const i = nodeAt(sx, sy);
    surface.style.cursor = i >= 0 ? "pointer" : "";
    if (i < 0) {
      tip.hidden = true;
      return;
    }
    let relation = "";
    if (selected >= 0 && i !== selected) {
      const d = nodeFocus[i];
      relation =
        d > 0
          ? ` · ${d === 1 ? "depends on" : `${d} hops from`} ${names[selected]}`
          : ` · ${names[selected]} ${d === -1 ? "depends on it" : `reaches it in ${-d} hops`}`;
    }
    tip.replaceChildren(
      el("strong", { textContent: names[i] }),
      el("span", { textContent: ` · ${rankText(i)} · ${countsText(i)}${relation}` }),
    );
    tip.hidden = false;
    const left = Math.min(sx + 14, width - tip.offsetWidth - 8);
    tip.style.left = `${Math.max(8, left)}px`;
    tip.style.top = `${sy + 14 + tip.offsetHeight > height ? sy - tip.offsetHeight - 10 : sy + 14}px`;
  });
  const release = (e) => {
    if (!pointers.has(e.pointerId)) return;
    const wasClick = pointers.size === 1 && !movedPointer && !multiTouch;
    pointers.delete(e.pointerId);
    if (!pointers.size) multiTouch = false;
    if (pointers.size < 2) pinchFrom = null;
    surface.classList.remove("dragging");
    if (!wasClick || e.type !== "pointerup" || !graph) return;
    // A click on empty sky with nothing selected has nothing to clear or say.
    const i = nodeAt(...local(e));
    if (i >= 0 || selected >= 0) select(i);
  };
  surface.addEventListener("pointerup", release);
  surface.addEventListener("pointercancel", release);
  surface.addEventListener("pointerleave", () => {
    tip.hidden = true;
  });
  surface.addEventListener(
    "wheel",
    (e) => {
      if (!graph) return;
      e.preventDefault();
      zoomAround(...local(e), Math.exp(-e.deltaY * 0.0015));
    },
    { passive: false },
  );

  // Keyboard: the graph is focusable, and pans and zooms from the keys a map
  // uses.
  surface.addEventListener("keydown", (e) => {
    if (!graph) return;
    const step = 80 / view.k;
    const moves = { ArrowLeft: [-step, 0], ArrowRight: [step, 0], ArrowUp: [0, -step], ArrowDown: [0, step] };
    if (moves[e.key]) {
      view.cx += moves[e.key][0];
      view.cy += moves[e.key][1];
      dirty = true;
    } else if (e.key === "+" || e.key === "=") zoomAround(width / 2, height / 2, 1.4);
    else if (e.key === "-") zoomAround(width / 2, height / 2, 1 / 1.4);
    else if (e.key === "0") fit();
    else return;
    e.preventDefault();
  });

  $("zoom-in").addEventListener("click", () => graph && zoomAround(width / 2, height / 2, 1.6));
  $("zoom-out").addEventListener("click", () => graph && zoomAround(width / 2, height / 2, 1 / 1.6));
  $("zoom-fit").addEventListener("click", () => graph && fit());

  // Click or drag on the navigator to move there.
  function steer(e) {
    const r = minimap.getBoundingClientRect();
    view.cx = ((e.clientX - r.left) / r.width) * graph.extent;
    view.cy = ((e.clientY - r.top) / r.height) * graph.extent;
    dirty = true;
  }
  minimap.addEventListener("pointerdown", (e) => {
    if (!graph) return;
    minimap.setPointerCapture(e.pointerId);
    steer(e);
  });
  minimap.addEventListener("pointermove", (e) => {
    if (graph && minimap.hasPointerCapture(e.pointerId)) steer(e);
  });

  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && selected >= 0 && graph) select(-1);
  });

  // Emptying the field -- its clear button, or deleting what was typed -- is
  // starting over: the selection goes and the whole graph comes back.
  find.addEventListener("input", () => {
    if (find.value || !graph) return;
    findNote.hidden = true;
    if (selected >= 0) select(-1);
    fit();
  });
  find.addEventListener("keydown", (e) => {
    if (e.key !== "Enter" || !graph) return;
    const wanted = canonical(find.value);
    if (!wanted) return;
    let i = byName.get(wanted);
    // Rank order, so a prefix lands on the best-known match.
    if (i === undefined) i = names.findIndex((n, j) => visible(j) && n.startsWith(wanted));
    const note = (text) => {
      findNote.textContent = text;
      findNote.hidden = false;
    };
    if (i < 0) {
      note(
        `No drawn project matches “${find.value.trim()}”. The graph holds projects with at least ${fmt(graph.min_dependents)} dependents.`,
      );
      return;
    }
    if (!visible(i)) {
      note(`${names[i]} has ${countsText(i)}, so it is drawn only with “Include extras” on.`);
      return;
    }
    findNote.hidden = true;
    // On a phone the keyboard would otherwise stay up over the sheet and the
    // trace just framed for it.
    if (narrow.matches) find.blur();
    select(i, { fly: true });
  });

  extrasToggle.addEventListener("change", () => graph && setMode(extrasToggle.checked));
  directToggle.addEventListener("change", () => {
    if (graph && selected >= 0) select(selected, { quiet: true });
    dirty = true;
  });

  // Simulated releases, standing in for PyPI's RSS feed. A uniform pick is the
  // honest stand-in: most releases are leaves, and a hub is a rare, big event.
  let timer = null;
  function simulateOne() {
    const i = Math.floor(Math.random() * (extras ? count : ranked));
    const reached = cascade(i, performance.now());
    const button = el("button", { type: "button", className: "jump", textContent: names[i] });
    button.addEventListener("click", () => select(i, { fly: true }));
    releases.prepend(
      el(
        "li",
        {},
        button,
        el("span", { className: "meta", textContent: reached ? ` reached ${fmt(reached)}` : " no drawn dependents" }),
      ),
    );
    while (releases.children.length > 3) releases.lastChild.remove();
  }
  function setSimulating(on) {
    clearInterval(timer);
    timer = on ? setInterval(simulateOne, SIM_EVERY_MS) : null;
    if (!simulate.checked) releases.replaceChildren();
  }
  simulate.addEventListener("change", () => setSimulating(simulate.checked));
  // Simulated releases are nothing but motion, so they stop with it.
  function syncMotion() {
    simulate.disabled = !moving();
    if (!moving()) {
      simulate.checked = false;
      setSimulating(false);
      litAt.clear();
      sparks = [];
      heat?.fill(0);
    }
    dirty = true;
  }
  animate.addEventListener("change", syncMotion);
  cloudsToggle.addEventListener("change", () => {
    dirty = true;
  });
  // A background tab stops drawing but would keep queueing releases, and pay
  // for all of them on return.
  document.addEventListener("visibilitychange", () => {
    setSimulating(simulate.checked && !document.hidden);
  });

  matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => {
    readColors();
    if (graph) paintSky();
    renderMinimapBase();
  });
  new ResizeObserver(resize).observe(surface);

  // Hands the main thread back between load stages, so parsing and indexing
  // the graph is several short tasks rather than one long one that holds off
  // input and paint. scheduler.yield where it exists, a timeout where not.
  const yieldToMain = () =>
    "scheduler" in window && "yield" in scheduler
      ? scheduler.yield()
      : new Promise((resolve) => setTimeout(resolve, 0));

  fetchJson(surface.dataset.graph)
    .then(async (data) => {
      await yieldToMain();
      buildIndexes(data);
      await yieldToMain();
      // Assigned only once indexed, and nothing yields from here to the end of
      // setMode: a frame or handler that sees a graph must also see the camera
      // and adjacency it reads.
      graph = data;
      upload();
      resize();
      // A drawing buffer this display cannot have has already said so.
      if (!graph) return;
      fit();
      setMode(extrasToggle.checked);
      statusLine.classList.add("sr-only");
      // Motion by default only for readers who have not asked for less of it.
      // Simulated releases stand in for a live feed that does not exist yet,
      // so they stay out of sight unless asked for with ?simulate.
      if (new URLSearchParams(location.search).has("simulate")) {
        $("simulate-toggle").hidden = false;
        simulate.checked = moving();
        simulate.disabled = !moving();
        setTimeout(() => setSimulating(simulate.checked && !document.hidden), 1500);
      }
    })
    .catch((err) => {
      statusLine.textContent = `The graph could not be loaded (${err.message}).`;
    });

  requestAnimationFrame(frame);
})();
