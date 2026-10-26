const state = {
  project: null,
  layers: new Map(),
  enabledLayers: new Set(),
  selectedCell: null,
  before: null,
  after: null,
  edges: [],
  afterEdges: [],
  selectedEdgeId: null,
  selectedPolygonId: null,
  constraintGraphLoading: false,
  cellLoadId: 0,
  showConstraints: true,
  viewBox: { x: 0, y: 0, width: 1000, height: 700 },
  dragMode: "pan",
  mergedPolygons: [],
  showMerged: false,
};

const svgNamespace = "http://www.w3.org/2000/svg";
const byId = (id) => document.getElementById(id);

function setStatus(message, workspace = false) {
  byId(workspace ? "workspace-status" : "status").textContent = message;
}

function updateLayersToggleButton() {
  const button = byId("layers-all");
  if (!button) return;
  const allEnabled = state.layers.size > 0 && state.enabledLayers.size === state.layers.size;
  button.textContent = allEnabled ? "All off" : "All on";
  button.disabled = state.layers.size === 0;
}

async function request(url, options = {}) {
  const response = await fetch(url, options);
  const contentType = response.headers.get("content-type") || "";
  const result = contentType.includes("application/json") ? await response.json() : null;
  if (!response.ok) {
    const detail = result && result.detail;
    throw new Error(typeof detail === "string" ? detail : `Request failed (${response.status}).`);
  }
  return result;
}

function makeSvgElement(name, attributes = {}) {
  const element = document.createElementNS(svgNamespace, name);
  for (const [key, value] of Object.entries(attributes)) {
    element.setAttribute(key, String(value));
  }
  return element;
}

function boundsOf(rectangles, polygons = []) {
  const bounds = [
    ...rectangles.map((item) => ({
      xmin: item.xmin,
      xmax: item.xmax,
      ymin: item.ymin,
      ymax: item.ymax,
    })),
    ...polygons
      .filter((polygon) => polygon.points.length)
      .map((polygon) => ({
        xmin: Math.min(...polygon.points.map((point) => point.x)),
        xmax: Math.max(...polygon.points.map((point) => point.x)),
        ymin: Math.min(...polygon.points.map((point) => point.y)),
        ymax: Math.max(...polygon.points.map((point) => point.y)),
      })),
  ];
  if (!bounds.length) {
    return { xmin: -1, xmax: 1, ymin: -1, ymax: 1 };
  }
  return {
    xmin: Math.min(...bounds.map((item) => item.xmin)),
    xmax: Math.max(...bounds.map((item) => item.xmax)),
    ymin: Math.min(...bounds.map((item) => item.ymin)),
    ymax: Math.max(...bounds.map((item) => item.ymax)),
  };
}

function layoutProjection(rectangles, polygons = []) {
  const width = 1000;
  const height = 700;
  const bounds = boundsOf(rectangles, polygons);
  const spanX = Math.max(bounds.xmax - bounds.xmin, 1);
  const spanY = Math.max(bounds.ymax - bounds.ymin, 1);
  const scale = Math.min(width / spanX, height / spanY) * 0.88;
  const offsetX = (width - spanX * scale) / 2;
  const offsetY = (height - spanY * scale) / 2;
  return {
    scale,
    x: (value) => offsetX + (value - bounds.xmin) * scale,
    y: (value) => height - offsetY - (value - bounds.ymin) * scale,
  };
}

function drawMergedPolygons(svg) {
  if (!state.showMerged || !state.mergedPolygons.length) return;
  
  const width = 1000;
  const height = 700;
  svg.setAttribute("viewBox", `${state.viewBox.x} ${state.viewBox.y} ${state.viewBox.width} ${state.viewBox.height}`);
  const bounds = boundsOf(state.before ? state.before.rectangles : []);
  const spanX = Math.max(bounds.xmax - bounds.xmin, 1);
  const spanY = Math.max(bounds.ymax - bounds.ymin, 1);
  const padding = 0.06;
  const scale = Math.min(width / spanX, height / spanY) * (1 - padding * 2);
  const offsetX = (width - spanX * scale) / 2;
  const offsetY = (height - spanY * scale) / 2;
  const x = (value) => offsetX + (value - bounds.xmin) * scale;
  const y = (value) => height - offsetY - (value - bounds.ymin) * scale;

  // Draw merged polygons
  for (const polyData of state.mergedPolygons) {
    const layer = state.layers.get(polyData.layer);
    const color = layer ? layer.color : "#ff00ff";
    
    // Create SVG path from polygon points
    const points = polyData.points;
    if (points.length < 2) continue;
    
    let pathData = `M ${x(points[0][0])} ${y(points[0][1])}`;
    for (let i = 1; i < points.length; i++) {
      pathData += ` L ${x(points[i][0])} ${y(points[i][1])}`;
    }
    pathData += " Z";
    
    const path = makeSvgElement("path", {
      d: pathData,
      fill: color,
      "fill-opacity": "0.6",
      stroke: color,
      "stroke-width": 1.5,
    });
    const title = makeSvgElement("title");
    title.textContent = `Merged polygon on layer ${polyData.layer}`;
    path.append(title);
    svg.append(path);
  }
}

const CUT_LAYER_PATTERN = /contact|via|cut/i;

function isCutLayer(layerNumber) {
  const layer = state.layers.get(layerNumber);
  return Boolean(layer && CUT_LAYER_PATTERN.test(layer.name || ""));
}

// Cut layers (contacts, vias) are drawn last so they stay on top and remain selectable.
function visiblePolygons(polygons) {
  const visible = polygons.filter((item) => state.enabledLayers.has(item.layer) && item.points.length >= 3);
  return [
    ...visible.filter((item) => !isCutLayer(item.layer)),
    ...visible.filter((item) => isCutLayer(item.layer)),
  ];
}

const CONSTRAINT_TYPE_COLORS = {
  width: "#ff9e58",
  spacing: "#56c3bc",
  overlap: "#c792ea",
  extension: "#f2d14b",
};
const DEFAULT_CONSTRAINT_COLOR = "#cfd8dc";

function constraintColor(type) {
  return CONSTRAINT_TYPE_COLORS[type] || DEFAULT_CONSTRAINT_COLOR;
}

function drawLayout(svg, rectangles, polygons = [], edges = []) {
  svg.replaceChildren();
  const projection = layoutProjection(rectangles, polygons);
  svg.setAttribute("viewBox", `${state.viewBox.x} ${state.viewBox.y} ${state.viewBox.width} ${state.viewBox.height}`);
  const { x, y } = projection;

  const visible = rectangles.filter((rectangle) => state.enabledLayers.has(rectangle.layer));
  for (const rectangle of visible) {
    const layer = state.layers.get(rectangle.layer);
    const left = x(rectangle.xmin);
    const top = y(rectangle.ymax);
    const shape = makeSvgElement("rect", {
      x: left,
      y: top,
      width: Math.max(0.8, (rectangle.xmax - rectangle.xmin) * projection.scale),
      height: Math.max(0.8, (rectangle.ymax - rectangle.ymin) * projection.scale),
      fill: layer
        ? (layer.datatype_colors[String(rectangle.datatype)] || layer.color)
        : "#778899",
      "fill-opacity": "0.45",
      stroke: layer
        ? (layer.datatype_colors[String(rectangle.datatype)] || layer.color)
        : "#778899",
      "stroke-width": 0.8,
    });
    const title = makeSvgElement("title");
    title.textContent = `${rectangle.id} · layer ${rectangle.layer}`;
    shape.append(title);
    svg.append(shape);
  }

  for (const polygon of visiblePolygons(polygons)) {
    if (polygon.points.length < 3) continue;
    const layer = state.layers.get(polygon.layer);
    const color = layer
      ? (layer.datatype_colors[String(polygon.datatype)] || layer.color)
      : "#778899";
    const pathData = polygon.points
      .map((point, index) => `${index === 0 ? "M" : "L"} ${x(point.x)} ${y(point.y)}`)
      .join(" ") + " Z";
    const shape = makeSvgElement("path", {
      d: pathData,
      fill: color,
      "fill-opacity": "0.45",
      stroke: color,
      "stroke-width": 0.8,
      class: `layout-polygon${polygon.id === state.selectedPolygonId ? " selected" : ""}`,
    });
    shape.setAttribute("data-polygon-id", polygon.id);
    const title = makeSvgElement("title");
    title.textContent = `${polygon.id} · layer ${polygon.layer}`;
    shape.append(title);
    svg.append(shape);
  }

  const edgeGroup = makeSvgElement("g");
  const selectedEdge = edges.find((item) => item.id === state.selectedEdgeId);
  for (const segment of selectedEdge ? [selectedEdge.start_segment, selectedEdge.end_segment] : []) {
    if (!segment) continue;
    edgeGroup.append(makeSvgElement("line", {
      x1: x(segment[0].x),
      y1: y(segment[0].y),
      x2: x(segment[1].x),
      y2: y(segment[1].y),
      stroke: constraintColor(selectedEdge.type),
      "stroke-width": 4,
      "vector-effect": "non-scaling-stroke",
      "pointer-events": "none",
      class: "constraint-segment-highlight",
    }));
  }
  for (const edge of (state.showConstraints ? edges : []).filter((item) =>
    (item.source_layer < 0 || state.enabledLayers.has(item.source_layer))
    && (item.target_layer < 0 || state.enabledLayers.has(item.target_layer)))) {
    const line = makeSvgElement("line", {
      x1: x(edge.start.x),
      y1: y(edge.start.y),
      x2: x(edge.end.x),
      y2: y(edge.end.y),
      stroke: constraintColor(edge.type),
      "stroke-width": 1,
      "stroke-dasharray": "3 3",
      "vector-effect": "non-scaling-stroke",
      class: `constraint-line${edge.excluded ? " excluded" : ""}${edge.id === state.selectedEdgeId ? " selected" : ""}`,
      "data-id": edge.id,
    });
    const title = makeSvgElement("title");
    title.textContent = `${edge.axis.toUpperCase()} ${edge.kind.replaceAll("_", " ")}: ${edge.value} nm · ${edge.source_layer_name || `Layer ${edge.source_layer}`} → ${edge.target_layer_name || `Layer ${edge.target_layer}`}`;
    line.append(title);
    const hitArea = makeSvgElement("line", {
      x1: x(edge.start.x),
      y1: y(edge.start.y),
      x2: x(edge.end.x),
      y2: y(edge.end.y),
      stroke: "transparent",
      "stroke-width": 10,
      "vector-effect": "non-scaling-stroke",
      "pointer-events": "stroke",
      class: "constraint-hit-area",
      "data-id": edge.id,
    });
    edgeGroup.append(hitArea);
    edgeGroup.append(line);
  }
  svg.append(edgeGroup);
}


function renderLayers() {
  const list = byId("layer-list");
  list.replaceChildren();
  for (const layer of state.layers.values()) {
    const label = document.createElement("label");
    label.className = "layer-option";
    const checkbox = document.createElement("input");
    checkbox.type = "checkbox";
    checkbox.checked = state.enabledLayers.has(layer.layer);
    checkbox.addEventListener("change", () => {
      if (checkbox.checked) state.enabledLayers.add(layer.layer);
      else state.enabledLayers.delete(layer.layer);
      updateLayersToggleButton();
      renderViews();
    });
    const swatch = document.createElement("span");
    swatch.className = "layer-swatch";
    swatch.style.backgroundColor = layer.color;
    const text = document.createElement("span");
    text.textContent = `${layer.name} (${layer.layer})`;
    label.append(checkbox, swatch, text);
    list.append(label);
  }
}

function polygonConstraints(edges = state.edges, layout = state.before) {
  const selectedPolygon = (layout?.polygons || []).find(
    (polygon) => polygon.id === state.selectedPolygonId,
  );
  const polygonShapeId = selectedPolygon?.id.startsWith(`${state.selectedCell}/`)
    ? selectedPolygon.id.slice(state.selectedCell.length + 1)
    : selectedPolygon?.id;
  return selectedPolygon
    ? edges.filter(
        (edge) => edge.source_rect === polygonShapeId
          || edge.target_rect === polygonShapeId
          || edge.source_rect === selectedPolygon.id
          || edge.target_rect === selectedPolygon.id,
      )
    : edges;
}

function renderConstraints() {
  const list = byId("constraint-list");
  list.replaceChildren();
  const filteredEdges = polygonConstraints();
  byId("constraint-count").textContent = String(filteredEdges.length);
  const visibilityButton = byId("toggle-constraints-button");
  visibilityButton.textContent = state.showConstraints ? "Hide graph" : "Show graph";
  visibilityButton.setAttribute("aria-pressed", String(state.showConstraints));
  if (state.constraintGraphLoading) {
    const loadingMessage = document.createElement("p");
    loadingMessage.className = "constraint-group-empty";
    loadingMessage.textContent = "Building constraint graph…";
    list.append(loadingMessage);
    return;
  }
  for (const type of ["width", "spacing", "overlap", "extension"]) {
    const groupEdges = filteredEdges.filter((edge) => edge.type === type);
    const group = document.createElement("details");
    group.className = "constraint-group";
    group.open = true;
    const heading = document.createElement("summary");
    heading.className = "constraint-group-heading";
    heading.textContent = `${type} (${groupEdges.length})`;
    group.append(heading);

    if (groupEdges.length === 0) {
      const emptyMessage = document.createElement("p");
      emptyMessage.className = "constraint-group-empty";
      emptyMessage.textContent = "No constraints";
      group.append(emptyMessage);
    }

    const edgesByRule = new Map();
    for (const edge of groupEdges) {
      const ruleId = edge.kind || "Unspecified";
      if (!edgesByRule.has(ruleId)) edgesByRule.set(ruleId, []);
      edgesByRule.get(ruleId).push(edge);
    }
    const ruleIds = [...edgesByRule.keys()].sort((left, right) =>
      left.localeCompare(right, undefined, { numeric: true, sensitivity: "base" }));
    for (const ruleId of ruleIds) {
      const ruleEdges = edgesByRule.get(ruleId);
      const ruleGroup = document.createElement("details");
      ruleGroup.className = "constraint-rule-group";
      ruleGroup.open = ruleEdges.some((item) => item.id === state.selectedEdgeId);
      const ruleHeading = document.createElement("summary");
      ruleHeading.className = "constraint-rule-heading";
      ruleHeading.textContent = `Rule ${ruleId} (${ruleEdges.length})`;
      ruleGroup.append(ruleHeading);

      for (const edge of ruleEdges) {
        const row = document.createElement("button");
        row.type = "button";
        row.className = `constraint-row${edge.excluded ? " excluded" : ""}${edge.id === state.selectedEdgeId ? " selected" : ""}`;
        row.setAttribute("data-id", edge.id);
        const label = document.createElement("span");
        const axis = document.createElement("span");
        axis.className = "constraint-axis";
        axis.textContent = edge.axis.toUpperCase();
        label.append(axis, document.createTextNode(edge.kind.replaceAll("_", " ")));
        const value = document.createElement("span");
        value.className = "value";
        value.textContent = `${edge.value} nm`;
        row.append(label, value);
        row.addEventListener("click", () => selectEdge(edge.id));
        ruleGroup.append(row);
      }
      group.append(ruleGroup);
    }
    list.append(group);
  }

  const edge = filteredEdges.find((item) => item.id === state.selectedEdgeId);
  const details = byId("edge-details");
  const toggleButton = byId("toggle-edge-button");
  if (!edge) {
    details.textContent = "Select a constraint to see its details.";
    toggleButton.disabled = true;
    toggleButton.textContent = "Exclude constraint";
  } else {
    const valueLabel = edge.kind === "fixed_footprint" ? "fixed size" : `${edge.value} nm minimum`;
    const sourceLayerName = edge.source_layer_name || `Layer ${edge.source_layer}`;
    const targetLayerName = edge.target_layer_name || `Layer ${edge.target_layer}`;
    details.textContent = `Constraint: ${edge.kind.replaceAll("_", " ")} · Type: ${edge.type} · ${edge.axis.toUpperCase()} · ${valueLabel} · ${edge.source_rect} → ${edge.target_rect} · Layers: ${sourceLayerName} → ${targetLayerName}${edge.excluded ? " · excluded" : ""}`;
    toggleButton.disabled = !edge.removable;
    toggleButton.textContent = edge.removable
      ? (edge.excluded ? "Restore constraint" : "Exclude constraint")
      : "Required footprint";
  }
}

function renderViews() {
  if (!state.before) return;
  drawLayout(byId("before-view"), state.before.rectangles, state.before.polygons || [], polygonConstraints(state.edges, state.before));
  if (state.after) drawLayout(byId("after-view"), state.after.rectangles, state.after.polygons || [], polygonConstraints(state.afterEdges || [], state.after));
  else byId("after-view").replaceChildren();
}

function zoomToEdge(edge) {
  if (!edge || !edge.start || !edge.end) return;
  const layout = (state.afterEdges || []).includes(edge) ? (state.after || state.before) : (state.before || state.after);
  if (!layout) return;
  const { x, y } = layoutProjection(layout.rectangles, layout.polygons || []);
  const startX = x(edge.start.x);
  const endX = x(edge.end.x);
  const startY = y(edge.start.y);
  const endY = y(edge.end.y);
  const padding = Math.max(Math.abs(endX - startX), Math.abs(endY - startY)) * 0.2 + 12;
  const centerX = (startX + endX) / 2;
  const centerY = (startY + endY) / 2;
  const view = byId("before-view");
  const aspectRatio = view.clientHeight > 0 ? view.clientWidth / view.clientHeight : 1000 / 700;
  let width = Math.max(Math.abs(endX - startX) + padding * 2, 24);
  let height = Math.max(Math.abs(endY - startY) + padding * 2, 24);
  if (width / height < aspectRatio) width = height * aspectRatio;
  else height = width / aspectRatio;
  state.viewBox = {
    x: centerX - width / 2,
    y: centerY - height / 2,
    width,
    height,
  };
}

function scrollConstraintIntoView(id) {
  const list = byId("constraint-list");
  if (!list) return;

  const selectedRow = list.querySelector(`.constraint-row[data-id="${id}"]`);
  if (selectedRow) {
    selectedRow.scrollIntoView({ behavior: "smooth", block: "center" });
  }
}

function selectPolygon(id) {
  state.selectedPolygonId = id;
  state.selectedEdgeId = null;
  renderConstraints();
  renderViews();
}

function selectEdge(id) {
  state.selectedEdgeId = id;
  const edge = state.edges.find((item) => item.id === id) || (state.afterEdges || []).find((item) => item.id === id);
  if (edge) zoomToEdge(edge);
  renderConstraints();
  renderViews();
  if (edge) scrollConstraintIntoView(id);
}

function clearSelection() {
  if (state.selectedEdgeId === null && state.selectedPolygonId === null) return;
  state.selectedEdgeId = null;
  state.selectedPolygonId = null;
  renderConstraints();
  renderViews();
}

async function loadSelectedCell() {
  if (!state.project || !state.selectedCell) return;
  const cell = state.selectedCell;
  const projectId = state.project.id;
  const loadId = ++state.cellLoadId;
  state.selectedPolygonId = null;
  const layout = await request(
    `/api/sessions/${projectId}/layout?cell=${encodeURIComponent(cell)}&version=before`,
  );
  if (loadId !== state.cellLoadId) return;
  state.before = layout;
  state.mergedPolygons = [];
  state.showMerged = false;
  state.edges = [];
  state.selectedEdgeId = null;
  updateZoomSelection(null, null, null, null);
  state.after = null;
  byId("before-caption").textContent = state.selectedCell;
  byId("after-caption").textContent = "Run migration to compare";
  byId("download-link").hidden = true;
  state.constraintGraphLoading = true;
  renderConstraints();
  renderViews();
  await new Promise((resolve) => requestAnimationFrame(() => setTimeout(resolve, 0)));
  if (loadId !== state.cellLoadId) return;
  try {
    const constraints = await request(
      `/api/sessions/${projectId}/constraints?cell=${encodeURIComponent(cell)}`,
    );
    if (loadId === state.cellLoadId) state.edges = constraints.edges;
  } finally {
    if (loadId === state.cellLoadId) {
      state.constraintGraphLoading = false;
      renderConstraints();
      renderViews();
    }
  }
}

async function loadMigratedCell() {
  if (!state.project) return;
  state.after = await request(`/api/sessions/${state.project.id}/layout?cell=${encodeURIComponent(state.selectedCell)}&version=after`);
  byId("after-caption").textContent = `${state.selectedCell} · migrated`;
  byId("download-link").href = `/api/sessions/${state.project.id}/download`;
  byId("download-link").hidden = false;
  renderViews();
}

function populateProject(project) {
  state.project = project;
  const gdsLayers = new Set(
    Array.isArray(project.gds_layers)
      ? project.gds_layers
      : project.layers.map((layer) => layer.layer),
  );
  state.layers = new Map(
    project.layers
      .filter((layer) => gdsLayers.has(layer.layer))
      .map((layer) => [layer.layer, layer]),
  );
  state.enabledLayers = new Set(state.layers.keys());
  updateLayersToggleButton();
  state.selectedCell = project.root_cells[0] || project.cells[project.cells.length - 1];
  state.after = null;
  state.mergedPolygons = [];
  state.showMerged = false;
  
  // Populate merge layer selector
  const mergeLayerSelect = byId("merge-layer-select");
  if (mergeLayerSelect) {
    mergeLayerSelect.replaceChildren();
    for (const layer of state.layers.values()) {
      const option = document.createElement("option");
      option.value = layer.layer;
      option.textContent = `${layer.name} (${layer.layer})`;
      mergeLayerSelect.append(option);
    }
  }
  byId("project-summary").textContent = `${project.technology.name} · grid ${project.technology.grid} nm`;
  byId("project-summary").hidden = false;

  const selector = byId("cell-select");
  selector.replaceChildren();
  for (const cell of project.cells) selector.add(new Option(cell, cell));
  selector.value = state.selectedCell;
  byId("technology-strip").textContent = `${project.cells.length} cells · ${state.layers.size} layers · ${project.source_name || "GDSII layout"}`;
  byId("upload-panel").hidden = true;
  byId("workspace").hidden = false;
  renderLayers();
}

byId("upload-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  setStatus("Loading layout and technology rules…");
  const form = new FormData(event.currentTarget);
  try {
    const project = await request("/api/sessions", { method: "POST", body: form });
    populateProject(project);
    await loadSelectedCell();
    setStatus("");
  } catch (error) {
    setStatus(error.message);
    window.alert(`Unable to load the technology file:\n\n${error.message}`);
  }
});

byId("cell-select").addEventListener("change", async (event) => {
  state.selectedCell = event.target.value;
  try {
    await loadSelectedCell();
    setStatus("");
  } catch (error) {
    setStatus(error.message, true);
  }
});

byId("merge-button").addEventListener("click", async () => {
  const layerSelect = byId("merge-layer-select");
  const selectedLayer = layerSelect ? parseInt(layerSelect.value) : null;
  
  if (!state.project || !state.selectedCell || selectedLayer === null) {
    setStatus("Please select a cell and a layer to merge.");
    return;
  }
  
  try {
    const response = await request(
      `/api/sessions/${state.project.id}/merge?cell=${encodeURIComponent(state.selectedCell)}&layer=${selectedLayer}`
    );
    
    if (response.polygons && response.polygons.length > 0) {
      state.mergedPolygons = response.polygons;
      state.showMerged = true;
      setStatus(`Merged ${response.count} polygon(s) on layer ${selectedLayer}.`, true);
    } else {
      state.mergedPolygons = [];
      state.showMerged = false;
      setStatus(response.message || "No polygons found on this layer.", true);
    }
    renderViews();
  } catch (error) {
    setStatus(error.message, true);
  }
});

byId("toggle-edge-button").addEventListener("click", async () => {
  const edge = state.edges.find((item) => item.id === state.selectedEdgeId);
  if (!edge || !edge.removable) return;
  try {
    await request(`/api/sessions/${state.project.id}/constraints/${encodeURIComponent(edge.id)}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ cell: state.selectedCell, excluded: !edge.excluded }),
    });
    await loadSelectedCell();
    setStatus("Constraint updated. Run the migration again to see its effect.", true);
  } catch (error) {
    setStatus(error.message, true);
  }
});

async function runMigration(method) {
  const buttons = [byId("migrate-button"), byId("sweep-button")];
  for (const button of buttons) button.disabled = true;
  setStatus(method === "sweep" ? "Sweeping constraint graph…" : "Migrating hierarchy…", true);
  try {
    const result = await request(
      `/api/sessions/${state.project.id}/migrate?method=${method}`,
      { method: "POST" },
    );
    await loadMigratedCell();
    setStatus(`${result.cells_migrated} cells migrated (${result.method}). ${result.excluded_constraints} constraints excluded.`, true);
  } catch (error) {
    setStatus(error.message, true);
  } finally {
    for (const button of buttons) button.disabled = false;
  }
}

byId("migrate-button").addEventListener("click", () => runMigration("solve"));
byId("sweep-button").addEventListener("click", () => runMigration("sweep"));

byId("layers-all").addEventListener("click", () => {
  if (state.enabledLayers.size === state.layers.size) {
    state.enabledLayers = new Set();
  } else {
    state.enabledLayers = new Set(state.layers.keys());
  }
  updateLayersToggleButton();
  renderLayers();
  renderViews();
});

byId("toggle-constraints-button").addEventListener("click", () => {
  state.showConstraints = !state.showConstraints;
  renderConstraints();
  renderViews();
});

function resetView() {
  state.viewBox = { x: 0, y: 0, width: 1000, height: 700 };
  renderViews();
}

function setDragMode(mode) {
  state.dragMode = mode;
  const button = byId("pan-zoom-toggle");
  const isPanMode = state.dragMode === "pan";
  button.textContent = isPanMode ? "Pan mode" : "Zoom mode";
  button.setAttribute("aria-pressed", String(isPanMode));

  if (state.dragMode === "zoom") {
    byId("before-view").classList.add("zoom-mode");
    byId("after-view").classList.add("zoom-mode");
  } else {
    byId("before-view").classList.remove("zoom-mode");
    byId("after-view").classList.remove("zoom-mode");
  }

  updateZoomSelection(null, null, null, null, null);
}

function toggleDragMode() {
  setDragMode(state.dragMode === "pan" ? "zoom" : "pan");
}

for (const svg of [byId("before-view"), byId("after-view")]) {
  svg.addEventListener("wheel", (event) => {
    event.preventDefault();
    const anchor = svgPoint(svg, event.clientX, event.clientY);
    if (!anchor) return;

    const factor = event.deltaY < 0 ? 0.82 : 1.22;
    const nextWidth = state.viewBox.width * factor;
    const scale = nextWidth / state.viewBox.width;
    const nextHeight = state.viewBox.height * scale;
    const relativeX = (anchor.x - state.viewBox.x) / state.viewBox.width;
    const relativeY = (anchor.y - state.viewBox.y) / state.viewBox.height;
    state.viewBox = {
      x: anchor.x - relativeX * nextWidth,
      y: anchor.y - relativeY * nextHeight,
      width: nextWidth,
      height: nextHeight,
    };
    renderViews();
  }, { passive: false });

  let dragOrigin = null;
  let zoomStart = null;
  let pointerStart = null;

  svg.addEventListener("pointerdown", (event) => {
    const target = event.target instanceof Element ? event.target : null;
    const edgeTarget = target
      ? target.closest(".constraint-line, .constraint-hit-area")
      : null;
    const polygonTarget = svg.id === "before-view" && target
      ? target.closest(".layout-polygon")
      : null;
    pointerStart = {
      x: event.clientX,
      y: event.clientY,
      primary: event.button === 0,
      edgeId: event.button === 0 ? edgeTarget?.getAttribute("data-id") : null,
      polygonId: event.button === 0 ? polygonTarget?.getAttribute("data-polygon-id") : null,
    };
    if (state.dragMode === "zoom") {
      zoomStart = { x: event.clientX, y: event.clientY };
      updateZoomSelection(svg, event.clientX, event.clientY, event.clientX, event.clientY);
    } else {
      dragOrigin = { x: event.clientX, y: event.clientY };
    }
    svg.setPointerCapture(event.pointerId);
  });
  svg.addEventListener("pointermove", (event) => {
    if (state.dragMode === "zoom" && zoomStart) {
      updateZoomSelection(svg, zoomStart.x, zoomStart.y, event.clientX, event.clientY);
    } else if (dragOrigin) {
      const current = svgPoint(svg, event.clientX, event.clientY);
      const previous = svgPoint(svg, dragOrigin.x, dragOrigin.y);
      if (!current || !previous) return;
      state.viewBox.x -= current.x - previous.x;
      state.viewBox.y -= current.y - previous.y;
      dragOrigin = { x: event.clientX, y: event.clientY };
      renderViews();
    }
  });
  svg.addEventListener("pointerup", (event) => {
    if (state.dragMode === "zoom" && zoomStart) {
      if (Math.abs(event.clientX - zoomStart.x) > 5 && Math.abs(event.clientY - zoomStart.y) > 5) {
        const start = svgPoint(svg, zoomStart.x, zoomStart.y);
        const end = svgPoint(svg, event.clientX, event.clientY);
        if (start && end) {
          state.viewBox = {
            x: Math.min(start.x, end.x),
            y: Math.min(start.y, end.y),
            width: Math.abs(end.x - start.x),
            height: Math.abs(end.y - start.y),
          };
          renderViews();
        }
      }
      updateZoomSelection(null, null, null, null, null);
      zoomStart = null;
    }
    if (
      pointerStart?.primary
      && Math.hypot(event.clientX - pointerStart.x, event.clientY - pointerStart.y) <= 5
    ) {
      if (pointerStart.polygonId) selectPolygon(pointerStart.polygonId);
      else if (pointerStart.edgeId) selectEdge(pointerStart.edgeId);
      else clearSelection();
    }
    pointerStart = null;
    dragOrigin = null;
  });
  svg.addEventListener("pointercancel", () => {
    dragOrigin = null;
    zoomStart = null;
    pointerStart = null;
    updateZoomSelection(null, null, null, null, null);
  });
}

byId("reset-view-button").addEventListener("click", resetView);

byId("pan-zoom-toggle").addEventListener("click", toggleDragMode);

function selectAdjacentPolygon(step) {
  const ordered = visiblePolygons(state.before?.polygons || []);
  if (!ordered.length) return false;
  const index = ordered.findIndex((polygon) => polygon.id === state.selectedPolygonId);
  if (index < 0) return false;
  selectPolygon(ordered[(index + step + ordered.length) % ordered.length].id);
  return true;
}

const SHORTCUTS = {
  f: resetView,
  z: () => setDragMode("zoom"),
  p: () => setDragMode("pan"),
};

document.addEventListener("keydown", (event) => {
  if (byId("workspace").hidden || event.ctrlKey || event.metaKey || event.altKey) return;
  const target = event.target;
  if (target instanceof Element && target.closest("input, select, textarea, [contenteditable='true']")) return;
  if (event.key === "Tab") {
    if (state.selectedPolygonId && selectAdjacentPolygon(event.shiftKey ? -1 : 1)) event.preventDefault();
    return;
  }
  const action = SHORTCUTS[event.key.toLowerCase()];
  if (action) {
    event.preventDefault();
    action();
  }
});

function svgPoint(svg, clientX, clientY) {
  const screenMatrix = svg.getScreenCTM();
  if (!screenMatrix) return null;
  const point = svg.createSVGPoint();
  point.x = clientX;
  point.y = clientY;
  return point.matrixTransform(screenMatrix.inverse());
}

function updateZoomSelection(svg, startX, startY, endX, endY) {
  const selections = [
    [byId("before-view"), byId("before-selection")],
    [byId("after-view"), byId("after-selection")],
  ];

  if (state.dragMode !== "zoom" || startX === null || !svg) {
    for (const [, overlay] of selections) {
      overlay.style.display = "none";
    }
    return;
  }

  const activeSelection = selections.find(([view]) => view === svg);
  for (const [view, overlay] of selections) {
    if (view !== svg) overlay.style.display = "none";
  }
  if (!activeSelection) return;

  const sourceBounds = svg.getBoundingClientRect();
  const x1 = (Math.min(startX, endX) - sourceBounds.left) / sourceBounds.width;
  const y1 = (Math.min(startY, endY) - sourceBounds.top) / sourceBounds.height;
  const x2 = (Math.max(startX, endX) - sourceBounds.left) / sourceBounds.width;
  const y2 = (Math.max(startY, endY) - sourceBounds.top) / sourceBounds.height;

  if ((x2 - x1) * sourceBounds.width <= 5 || (y2 - y1) * sourceBounds.height <= 5) {
    activeSelection[1].style.display = "none";
    return;
  }

  const [view, overlay] = activeSelection;
  const viewBounds = view.getBoundingClientRect();
  const panelBounds = view.parentElement.getBoundingClientRect();
  overlay.style.left = `${viewBounds.left - panelBounds.left + x1 * viewBounds.width}px`;
  overlay.style.top = `${viewBounds.top - panelBounds.top + y1 * viewBounds.height}px`;
  overlay.style.width = `${(x2 - x1) * viewBounds.width}px`;
  overlay.style.height = `${(y2 - y1) * viewBounds.height}px`;
  overlay.style.display = "block";
}


byId("new-project-button").addEventListener("click", async () => {
  if (state.project) {
    try {
      await request(`/api/sessions/${state.project.id}`, { method: "DELETE" });
    } catch (error) {
      setStatus(error.message);
    }
  }
  state.project = null;
  state.before = null;
  state.after = null;
  state.edges = [];
  state.selectedEdgeId = null;
  setDragMode("pan");
  resetView();
  byId("workspace").hidden = true;
  byId("upload-panel").hidden = false;
  byId("project-summary").hidden = true;
  byId("upload-form").reset();
  setStatus("");
});
