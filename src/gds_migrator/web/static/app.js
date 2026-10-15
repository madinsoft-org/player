const state = {
  project: null,
  layers: new Map(),
  enabledLayers: new Set(),
  selectedCell: null,
  before: null,
  after: null,
  edges: [],
  selectedEdgeId: null,
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
  const allEnabled = state.enabledLayers.size === state.layers.size;
  button.textContent = allEnabled ? "All off" : "All on";
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

function boundsOf(rectangles) {
  if (!rectangles.length) {
    return { xmin: -1, xmax: 1, ymin: -1, ymax: 1 };
  }
  return {
    xmin: Math.min(...rectangles.map((item) => item.xmin)),
    xmax: Math.max(...rectangles.map((item) => item.xmax)),
    ymin: Math.min(...rectangles.map((item) => item.ymin)),
    ymax: Math.max(...rectangles.map((item) => item.ymax)),
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

function drawLayout(svg, rectangles, edges = []) {
  svg.replaceChildren();
  const width = 1000;
  const height = 700;
  svg.setAttribute("viewBox", `${state.viewBox.x} ${state.viewBox.y} ${state.viewBox.width} ${state.viewBox.height}`);
  const bounds = boundsOf(rectangles);
  const spanX = Math.max(bounds.xmax - bounds.xmin, 1);
  const spanY = Math.max(bounds.ymax - bounds.ymin, 1);
  const padding = 0.06;
  const scale = Math.min(width / spanX, height / spanY) * (1 - padding * 2);
  const offsetX = (width - spanX * scale) / 2;
  const offsetY = (height - spanY * scale) / 2;
  const x = (value) => offsetX + (value - bounds.xmin) * scale;
  const y = (value) => height - offsetY - (value - bounds.ymin) * scale;

  const visible = rectangles.filter((rectangle) => state.enabledLayers.has(rectangle.layer));
  for (const rectangle of visible) {
    const layer = state.layers.get(rectangle.layer);
    const left = x(rectangle.xmin);
    const top = y(rectangle.ymax);
    const shape = makeSvgElement("rect", {
      x: left,
      y: top,
      width: Math.max(0.8, (rectangle.xmax - rectangle.xmin) * scale),
      height: Math.max(0.8, (rectangle.ymax - rectangle.ymin) * scale),
      fill: layer ? layer.color : "#778899",
      "fill-opacity": "0.45",
      stroke: layer ? layer.color : "#778899",
      "stroke-width": 0.8,
    });
    const title = makeSvgElement("title");
    title.textContent = `${rectangle.id} · layer ${rectangle.layer}`;
    shape.append(title);
    svg.append(shape);
  }

  const edgeGroup = makeSvgElement("g");
  for (const edge of (state.showConstraints ? edges : []).filter((item) =>
    (item.source_layer < 0 || state.enabledLayers.has(item.source_layer))
    && (item.target_layer < 0 || state.enabledLayers.has(item.target_layer)))) {
    const line = makeSvgElement("line", {
      x1: x(edge.start.x),
      y1: y(edge.start.y),
      x2: x(edge.end.x),
      y2: y(edge.end.y),
      stroke: edge.axis === "x" ? "#ff9e58" : "#56c3bc",
      "stroke-width": 1,
      "stroke-dasharray": "3 3",
      "vector-effect": "non-scaling-stroke",
      class: `constraint-line${edge.excluded ? " excluded" : ""}${edge.id === state.selectedEdgeId ? " selected" : ""}`,
      "data-id": edge.id,
    });
    const title = makeSvgElement("title");
    title.textContent = `${edge.axis.toUpperCase()} ${edge.kind.replaceAll("_", " ")}: ${edge.value}`;
    line.append(title);
    line.addEventListener("click", () => selectEdge(edge.id));
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

function renderConstraints() {
  const list = byId("constraint-list");
  list.replaceChildren();
  byId("constraint-count").textContent = String(state.edges.length);
  const visibilityButton = byId("toggle-constraints-button");
  visibilityButton.textContent = state.showConstraints ? "Hide graph" : "Show graph";
  visibilityButton.setAttribute("aria-pressed", String(state.showConstraints));
  for (const edge of state.edges) {
    const row = document.createElement("button");
    row.type = "button";
    row.className = `constraint-row${edge.excluded ? " excluded" : ""}${edge.id === state.selectedEdgeId ? " selected" : ""}`;
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
    list.append(row);
  }

  const edge = state.edges.find((item) => item.id === state.selectedEdgeId);
  const details = byId("edge-details");
  const toggleButton = byId("toggle-edge-button");
  if (!edge) {
    details.textContent = "Select a constraint to see its details.";
    toggleButton.disabled = true;
    toggleButton.textContent = "Exclude constraint";
  } else {
    const valueLabel = edge.kind === "fixed_footprint" ? "fixed size" : `${edge.value} nm minimum`;
    details.textContent = `${edge.axis.toUpperCase()} · ${edge.kind.replaceAll("_", " ")} · ${valueLabel} · ${edge.source_rect} → ${edge.target_rect} · layers ${edge.source_layer}/${edge.target_layer}${edge.excluded ? " · excluded" : ""}`;
    toggleButton.disabled = !edge.removable;
    toggleButton.textContent = edge.removable
      ? (edge.excluded ? "Restore constraint" : "Exclude constraint")
      : "Required footprint";
  }
}

function renderViews() {
  if (!state.before) return;
  drawLayout(byId("before-view"), state.before.rectangles, state.edges);
  if (state.after) drawLayout(byId("after-view"), state.after.rectangles);
  else byId("after-view").replaceChildren();
}

function selectEdge(id) {
  state.selectedEdgeId = id;
  renderConstraints();
  renderViews();
}

async function loadSelectedCell() {
  if (!state.project || !state.selectedCell) return;
  state.before = await request(`/api/sessions/${state.project.id}/layout?cell=${encodeURIComponent(state.selectedCell)}&version=before`);
  state.mergedPolygons = [];
  state.showMerged = false;
  const constraints = await request(`/api/sessions/${state.project.id}/constraints?cell=${encodeURIComponent(state.selectedCell)}`);
  state.edges = constraints.edges;
  state.selectedEdgeId = state.edges.some((edge) => edge.id === state.selectedEdgeId) ? state.selectedEdgeId : null;
  updateZoomSelection(null, null, null, null);
  state.after = null;
  byId("before-caption").textContent = state.selectedCell;
  byId("after-caption").textContent = "Run migration to compare";
  byId("download-link").hidden = true;
  renderConstraints();
  renderViews();
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
  state.layers = new Map(project.layers.map((layer) => [layer.layer, layer]));
  // Only enable layers that are present in the GDS file by default
  if (project.gds_layers && project.gds_layers.length > 0) {
    state.enabledLayers = new Set(project.gds_layers);
  } else {
    state.enabledLayers = new Set(project.layers.map((layer) => layer.layer));
  }
  updateLayersToggleButton();
  state.selectedCell = project.root_cells[0] || project.cells[project.cells.length - 1];
  state.after = null;
  state.mergedPolygons = [];
  state.showMerged = false;
  
  // Populate merge layer selector
  const mergeLayerSelect = byId("merge-layer-select");
  if (mergeLayerSelect) {
    mergeLayerSelect.replaceChildren();
    for (const layer of project.layers) {
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
  byId("technology-strip").textContent = `${project.cells.length} cells · ${project.layers.length} layers · ${project.source_name || "GDSII layout"}`;
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

byId("migrate-button").addEventListener("click", async () => {
  const button = byId("migrate-button");
  button.disabled = true;
  setStatus("Migrating hierarchy…", true);
  try {
    const result = await request(`/api/sessions/${state.project.id}/migrate`, { method: "POST" });
    await loadMigratedCell();
    setStatus(`${result.cells_migrated} cells migrated. ${result.excluded_constraints} constraints excluded.`, true);
  } catch (error) {
    setStatus(error.message, true);
  } finally {
    button.disabled = false;
  }
});

byId("layers-all").addEventListener("click", () => {
  const allLayers = new Set(state.layers.keys());
  if (state.enabledLayers.size === state.layers.size) {
    state.enabledLayers = new Set();
  } else {
    state.enabledLayers = allLayers;
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

byId("layers-all").addEventListener("click", () => {
  const allLayers = new Set(state.layers.keys());
  if (state.enabledLayers.size === state.layers.size) {
    state.enabledLayers = new Set();
  } else {
    state.enabledLayers = allLayers;
  }
  updateLayersToggleButton();
  renderLayers();
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

  svg.addEventListener("pointerdown", (event) => {
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
    dragOrigin = null;
  });
  svg.addEventListener("pointercancel", () => {
    dragOrigin = null;
    zoomStart = null;
    updateZoomSelection(null, null, null, null, null);
  });
}

byId("reset-view-button").addEventListener("click", resetView);

byId("pan-zoom-toggle").addEventListener("click", toggleDragMode);

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
