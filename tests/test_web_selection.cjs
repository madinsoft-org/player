const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const { test } = require("node:test");

class Element {
  constructor() {
    this.children = [];
    this.attributes = {};
    this.style = {};
  }

  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this.children = children; }
  setAttribute(name, value) { this.attributes[name] = String(value); }
  addEventListener() {}
  querySelector() { return null; }
  get classList() {
    const attributes = this.attributes;
    const names = () => new Set((attributes.class || "").split(" ").filter(Boolean));
    return {
      add: (name) => { attributes.class = [...names().add(name)].join(" "); },
      remove: (name) => { const set = names(); set.delete(name); attributes.class = [...set].join(" "); },
      contains: (name) => names().has(name),
    };
  }
}

function workbench() {
  const elements = new Map();
  const listeners = {};
  const document = {
    getElementById(id) {
      if (!elements.has(id)) elements.set(id, new Element());
      return elements.get(id);
    },
    createElement: () => new Element(),
    createElementNS: () => new Element(),
    createTextNode: (text) => ({ textContent: text }),
    addEventListener(type, listener) { listeners[type] = listener; },
  };
  const context = vm.createContext({ document, Element });
  const source = fs.readFileSync(
    path.join(__dirname, "..", "src", "gds_migrator", "web", "static", "app.js"),
    "utf8",
  );
  vm.runInContext(source, context);
  vm.runInContext(`
    state.selectedCell = "TOP";
    state.enabledLayers = new Set([1]);
    state.before = {
      rectangles: [],
      polygons: ["p0", "p1", "p10", "orphan"].map((id, index) => ({
        id: "TOP/" + id,
        layer: 1,
        datatype: 0,
        points: [
          { x: index * 10, y: 0 },
          { x: index * 10 + 5, y: 0 },
          { x: index * 10 + 5, y: 5 },
        ],
      })),
    };
    state.edges = [
      { id: "width-0", source_rect: "p0", target_rect: "p0", type: "width" },
      { id: "spacing", source_rect: "p0", target_rect: "p1", type: "spacing" },
      { id: "width-1", source_rect: "TOP/p1", target_rect: "TOP/p1", type: "width" },
      { id: "width-10", source_rect: "p10", target_rect: "p10", type: "width" },
    ].map((edge) => ({
      ...edge, axis: "x", kind: "minimum_" + edge.type, value: 5,
      source_layer: 1, target_layer: 1, removable: true, excluded: false,
      start: { x: 0, y: 0 }, end: { x: 5, y: 0 },
    }));
  `, context);
  return {
    run: (code) => vm.runInContext(code, context),
    element: (id) => document.getElementById(id),
    press: (key, options = {}) => {
      let prevented = false;
      listeners.keydown({
        key, shiftKey: false, ctrlKey: false, metaKey: false, altKey: false, target: null,
        ...options,
        preventDefault: () => { prevented = true; },
      });
      return prevented;
    },
  };
}

function descendants(element) {
  return (element.children || []).flatMap((child) => [child, ...descendants(child)]);
}

function constraintIds(element, className) {
  return descendants(element)
    .filter((child) => child.attributes?.class?.split(" ").includes(className)
      || child.className?.split(" ").includes(className))
    .map((child) => child.attributes["data-id"]);
}

function assertConstraints(app, expected) {
  assert.deepEqual(
    constraintIds(app.element("before-view"), "constraint-line").sort(),
    [...expected].sort(),
  );
  assert.deepEqual(
    constraintIds(app.element("constraint-list"), "constraint-row").sort(),
    [...expected].sort(),
  );
  assert.equal(app.element("constraint-count").textContent, String(expected.length));
}

test("without polygon selection the complete constraint graph remains visible", () => {
  const app = workbench();
  app.run("renderConstraints(); renderViews();");
  assertConstraints(app, ["width-0", "spacing", "width-1", "width-10"]);
});

test("polygon selection filters graph and list by either constraint endpoint", () => {
  const app = workbench();
  app.run('selectPolygon("TOP/p0");');
  assertConstraints(app, ["width-0", "spacing"]);
  app.run('selectPolygon("TOP/p1");');
  assertConstraints(app, ["spacing", "width-1"]);
});

test("a polygon without constraints shows no unrelated graph edges", () => {
  const app = workbench();
  app.run('selectPolygon("TOP/orphan");');
  assertConstraints(app, []);
});

test("changing polygon clears stale constraint details and preserves exclusions", () => {
  const app = workbench();
  app.run(`
    state.selectedEdgeId = "width-0";
    state.edges[2].excluded = true;
    selectPolygon("TOP/p1");
  `);
  assertConstraints(app, ["spacing", "width-1"]);
  assert.equal(app.element("toggle-edge-button").disabled, true);
  assert.equal(app.element("edge-details").textContent, "Select a constraint to see its details.");
  const excluded = descendants(app.element("before-view"))
    .find((element) => element.attributes?.["data-id"] === "width-1"
      && element.attributes.class.includes("constraint-line"));
  assert.ok(excluded.attributes.class.includes("excluded"));
});

test("graph visibility and layer visibility still apply to filtered edges", () => {
  const app = workbench();
  app.run('selectPolygon("TOP/p0"); state.showConstraints = false; renderViews();');
  assert.deepEqual(constraintIds(app.element("before-view"), "constraint-line"), []);
  app.run("state.showConstraints = true; state.enabledLayers.clear(); renderViews();");
  assert.deepEqual(constraintIds(app.element("before-view"), "constraint-line"), []);
  app.run("state.enabledLayers.add(1); renderViews();");
  assertConstraints(app, ["width-0", "spacing"]);
});

function polygonOrder(app) {
  return descendants(app.element("before-view"))
    .filter((element) => element.attributes?.["data-polygon-id"])
    .map((element) => element.attributes["data-polygon-id"]);
}

test("contact and via polygons are drawn above other polygons", () => {
  const app = workbench();
  app.run(`
    state.layers = new Map([
      [1, { layer: 1, name: "metal1", color: "#111111", datatype_colors: {} }],
      [2, { layer: 2, name: "contact", color: "#222222", datatype_colors: {} }],
      [3, { layer: 3, name: "Via12", color: "#333333", datatype_colors: {} }],
    ]);
    state.enabledLayers = new Set([1, 2, 3]);
    state.before.polygons[0].layer = 2;
    state.before.polygons[2].layer = 3;
    renderViews();
  `);
  assert.deepEqual(polygonOrder(app), ["TOP/p1", "TOP/orphan", "TOP/p0", "TOP/p10"]);
});

test("selected constraint details include its type", () => {
  const app = workbench();
  app.run('selectEdge("spacing");');
  assert.match(app.element("edge-details").textContent, /Type: spacing/);
});

test("selecting a constraint expands its rule group only", () => {
  const app = workbench();
  app.run('selectEdge("spacing");');
  const groups = descendants(app.element("constraint-list"))
    .filter((element) => element.className === "constraint-rule-group");
  const opened = groups.filter((group) => group.open);
  assert.equal(opened.length, 1);
  assert.ok(descendants(opened[0]).some((child) => child.attributes?.["data-id"] === "spacing"));
});

test("Tab cycles through visible polygons and Shift+Tab goes back", () => {
  const app = workbench();
  assert.equal(app.press("Tab"), false);
  app.run('selectPolygon("TOP/p10");');
  assert.equal(app.press("Tab"), true);
  assert.equal(app.run("state.selectedPolygonId"), "TOP/orphan");
  app.press("Tab");
  assert.equal(app.run("state.selectedPolygonId"), "TOP/p0");
  app.press("Tab", { shiftKey: true });
  assert.equal(app.run("state.selectedPolygonId"), "TOP/orphan");
});

test("F, Z and P shortcuts reset view and switch drag mode", () => {
  const app = workbench();
  app.run('state.viewBox = { x: 5, y: 5, width: 10, height: 10 };');
  assert.equal(app.press("z"), true);
  assert.equal(app.run("state.dragMode"), "zoom");
  app.press("P");
  assert.equal(app.run("state.dragMode"), "pan");
  app.press("f");
  assert.deepEqual(
    JSON.parse(app.run("JSON.stringify(state.viewBox)")),
    { x: 0, y: 0, width: 1000, height: 700 },
  );
  assert.equal(app.press("z", { ctrlKey: true }), false);
  assert.equal(app.run("state.dragMode"), "pan");
});
