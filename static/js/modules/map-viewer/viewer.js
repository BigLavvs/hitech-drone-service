import { api } from "../../api.js";
import { normaliseBounds, clamp } from "./geometry.js";
import { parseKmlToGeoJson } from "./kml.js";
import { ensureLeafletRuntime } from "./runtime.js";
import { createSafePopupContent, escapeAttribute, escapeHtml, prettyEnum } from "./safe.js";

export class MapViewer {
  constructor(container, { messageElement } = {}) {
    this.container = container;
    this.messageElement = messageElement;
    this.map = null;
    this.layerEntries = [];
    this.imageEntries = [];
    this.resizeObserver = null;
    this.catalogRequest = 0;

    this.renderShell();
    this.bindEvents();
  }

  renderShell() {
    this.container.innerHTML = `
      <div class="survey-viewer-layout survey-viewer-layout--map">
        <aside class="survey-viewer-sidebar">
          <section class="survey-viewer-panel">
            <div class="survey-viewer-panel-header">
              <h3>Layers</h3>
              <button class="button survey-viewer-fit-button" type="button" data-map-fit-button>Fit to data</button>
            </div>
            <div data-map-layer-controls></div>
          </section>
          <section class="survey-viewer-panel">
            <h3>Private image sources</h3>
            <div data-map-image-list></div>
          </section>
        </aside>
        <div class="survey-viewer-stage">
          <div class="survey-map-canvas" data-map-canvas></div>
          <div class="survey-viewer-state" data-map-state></div>
        </div>
      </div>
    `;

    this.controlsElement = this.container.querySelector("[data-map-layer-controls]");
    this.imagesElement = this.container.querySelector("[data-map-image-list]");
    this.mapElement = this.container.querySelector("[data-map-canvas]");
    this.stateElement = this.container.querySelector("[data-map-state]");
    this.fitButton = this.container.querySelector("[data-map-fit-button]");
  }

  bindEvents() {
    this.controlsElement?.addEventListener("change", (event) => this.handleControlChange(event));
    this.controlsElement?.addEventListener("input", (event) => this.handleControlInput(event));
    this.fitButton?.addEventListener("click", () => this.fitToVisibleData());
  }

  getMap() {
    return this.map;
  }

  getLeaflet() {
    return window.L || null;
  }

  hasRenderableLayer() {
    return this.layerEntries.length > 0;
  }

  async load(surveyId) {
    const requestId = ++this.catalogRequest;
    this.clearMessage();
    this.showState("Loading 2D map", "Fetching ready raster and vector layers for this survey.", "loading");
    this.renderLoadingPanels();

    await ensureLeafletRuntime();
    if (requestId !== this.catalogRequest) {
      return;
    }

    const descriptors = await api.get(`/api/v1/surveys/${surveyId}/map-layers`);
    if (requestId !== this.catalogRequest) {
      return;
    }

    this.destroyLayers();
    this.ensureMap();
    await this.applyDescriptors(descriptors, requestId);
  }

  async applyDescriptors(descriptors, requestId) {
    const preparedEntries = await Promise.all(
      (Array.isArray(descriptors) ? descriptors : []).map((descriptor) =>
        this.prepareEntry(descriptor).catch((error) => ({
          id: descriptor.id,
          descriptor,
          kind: "failed",
          error,
        })),
      ),
    );
    if (requestId !== this.catalogRequest) {
      return;
    }

    this.layerEntries = [];
    this.imageEntries = [];

    for (const entry of preparedEntries) {
      if (entry.kind === "image") {
        this.imageEntries.push(entry);
        continue;
      }
      if (entry.kind === "failed") {
        continue;
      }
      this.layerEntries.push(entry);
      if (entry.visible) {
        entry.layer.addTo(this.map);
      }
    }

    this.renderControls();
    this.renderImageList();

    const failedLayers = preparedEntries.filter((entry) => entry.kind === "failed");
    if (failedLayers.length > 0) {
      this.showMessage("Some map layers could not be loaded and were omitted.", "error");
    }

    if (this.layerEntries.length === 0) {
      const title = this.imageEntries.length > 0 ? "No georeferenced map layers ready" : "No 2D map data ready";
      const description =
        this.imageEntries.length > 0
          ? "Private PNG and JPEG files are listed, but no raster tile set or vector layer is ready to place on the map."
          : "This survey does not currently have any ready 2D map layers.";
      this.showUnavailableState(title, description);
      return;
    }

    this.hideState();
    this.fitToVisibleData();
  }

  async prepareEntry(descriptor) {
    const format = String(descriptor.format || "").toUpperCase();

    if (descriptor.tile_url_template) {
      const zoomRange = descriptor.zoom_range || {};
      const layer = window.L.tileLayer(descriptor.tile_url_template, {
        minZoom: Number.isFinite(Number(zoomRange.min)) ? Number(zoomRange.min) : 0,
        maxZoom: Number.isFinite(Number(zoomRange.max)) ? Number(zoomRange.max) : 22,
        maxNativeZoom: Number.isFinite(Number(zoomRange.max)) ? Number(zoomRange.max) : undefined,
        opacity: 1,
      });

      return {
        id: descriptor.id,
        kind: "raster",
        descriptor,
        layer,
        bounds: normaliseBounds(descriptor.bounds),
        visible: true,
        opacity: 1,
      };
    }

    if (format === "PNG" || format === "JPEG") {
      return {
        id: descriptor.id,
        kind: "image",
        descriptor,
      };
    }

    if (!descriptor.source_url) {
      throw new Error("Source URL missing.");
    }

    const response = await fetch(descriptor.source_url, {
      credentials: "omit",
      headers: { Accept: format === "GEOJSON" ? "application/json" : "application/vnd.google-earth.kml+xml,text/xml" },
    });
    if (!response.ok) {
      throw new Error("Source request failed.");
    }

    let geojson = null;
    if (format === "GEOJSON") {
      geojson = await response.json();
    } else if (format === "KML") {
      geojson = parseKmlToGeoJson(await response.text());
    } else {
      throw new Error("Unsupported vector format.");
    }

    const layer = window.L.geoJSON(geojson, {
      style: () => ({
        color: "#176b57",
        weight: 2,
        opacity: 0.9,
        fillColor: "#176b57",
        fillOpacity: 0.15,
      }),
      pointToLayer: (_feature, latlng) =>
        window.L.circleMarker(latlng, {
          radius: 6,
          color: "#176b57",
          weight: 2,
          fillColor: "#ffffff",
          fillOpacity: 1,
        }),
      onEachFeature: (feature, featureLayer) => {
        const properties = feature?.properties || {};
        const name = typeof properties.name === "string" && properties.name.trim() ? properties.name.trim() : null;
        const description =
          typeof properties.description === "string" && properties.description.trim()
            ? properties.description.trim()
            : null;
        if (name || description) {
          featureLayer.bindPopup(createSafePopupContent({ name, description }));
        }
      },
    });

    return {
      id: descriptor.id,
      kind: "vector",
      descriptor,
      layer,
      bounds: layer.getBounds().isValid() ? layer.getBounds() : null,
      visible: true,
      opacity: 1,
    };
  }

  ensureMap() {
    if (this.map) {
      return;
    }

    this.map = window.L.map(this.mapElement, {
      zoomControl: true,
      attributionControl: false,
    });
    this.resizeObserver = new ResizeObserver(() => {
      if (!this.mapElement.closest("[hidden]")) {
        this.map.invalidateSize(false);
      }
    });
    this.resizeObserver.observe(this.mapElement);
  }

  renderLoadingPanels() {
    this.controlsElement.innerHTML = '<p class="survey-viewer-empty-copy">Loading layer catalog.</p>';
    this.imagesElement.innerHTML = '<p class="survey-viewer-empty-copy">Loading private image sources.</p>';
    this.fitButton.disabled = true;
  }

  renderControls() {
    this.fitButton.disabled = this.layerEntries.length === 0;

    if (this.layerEntries.length === 0) {
      this.controlsElement.innerHTML =
        '<p class="survey-viewer-empty-copy">No georeferenced 2D layers are ready for display.</p>';
      return;
    }

    this.controlsElement.innerHTML = this.layerEntries
      .map((entry) => {
        const formatLabel = escapeHtml(prettyEnum(entry.descriptor.format));
        const filename = escapeHtml(entry.descriptor.original_filename);
        const opacityValue = Math.round(entry.opacity * 100);
        return `
          <label class="survey-layer-control">
            <div class="survey-layer-control-row">
              <input type="checkbox" data-layer-visibility="${entry.id}" ${entry.visible ? "checked" : ""}>
              <span>
                <strong>${filename}</strong>
                <span class="survey-layer-meta">${formatLabel}</span>
              </span>
            </div>
            ${
              entry.kind === "raster"
                ? `
                  <div class="survey-layer-control-row">
                    <span class="survey-layer-meta">Opacity</span>
                    <input
                      class="survey-layer-opacity"
                      type="range"
                      min="0"
                      max="100"
                      step="5"
                      value="${opacityValue}"
                      data-layer-opacity="${entry.id}"
                      aria-label="Opacity for ${filename}"
                    >
                    <span class="survey-layer-meta">${opacityValue}%</span>
                  </div>
                `
                : ""
            }
          </label>
        `;
      })
      .join("");
  }

  renderImageList() {
    if (this.imageEntries.length === 0) {
      this.imagesElement.innerHTML =
        '<p class="survey-viewer-empty-copy">No unplaced PNG or JPEG image sources are available.</p>';
      return;
    }

    this.imagesElement.innerHTML = this.imageEntries
      .map(
        (entry) => `
          <div class="survey-image-source">
            <div>
              <strong>${escapeHtml(entry.descriptor.original_filename)}</strong>
              <p class="survey-layer-meta">${escapeHtml(prettyEnum(entry.descriptor.format))} available as a private source. Geographic bounds were not supplied, so it is not placed on the map.</p>
            </div>
            <a class="button" href="${escapeAttribute(entry.descriptor.source_url)}" target="_blank" rel="noreferrer noopener">Open image</a>
          </div>
        `,
      )
      .join("");
  }

  handleControlChange(event) {
    const checkbox = event.target.closest("[data-layer-visibility]");
    if (!checkbox) {
      return;
    }

    const entry = this.layerEntries.find((item) => String(item.id) === checkbox.dataset.layerVisibility);
    if (!entry) {
      return;
    }

    entry.visible = checkbox.checked;
    if (entry.visible) {
      entry.layer.addTo(this.map);
      this.fitToVisibleData();
    } else {
      this.map.removeLayer(entry.layer);
    }
  }

  handleControlInput(event) {
    const input = event.target.closest("[data-layer-opacity]");
    if (!input) {
      return;
    }

    const entry = this.layerEntries.find((item) => String(item.id) === input.dataset.layerOpacity);
    if (!entry) {
      return;
    }

    const opacity = clamp(Number(input.value) / 100, 0, 1);
    entry.opacity = opacity;
    if (typeof entry.layer.setOpacity === "function") {
      entry.layer.setOpacity(opacity);
    }

    const row = input.closest(".survey-layer-control");
    row?.querySelector(".survey-layer-control-row:last-child .survey-layer-meta:last-child")?.replaceChildren(
      document.createTextNode(`${Math.round(opacity * 100)}%`),
    );
  }

  fitToVisibleData() {
    if (!this.map) {
      return;
    }

    const visibleBounds = this.layerEntries
      .filter((entry) => entry.visible && entry.bounds)
      .map((entry) => entry.bounds);

    if (visibleBounds.length === 0) {
      return;
    }

    const firstBounds = visibleBounds[0];
    const combined = window.L.latLngBounds(firstBounds.getSouthWest(), firstBounds.getNorthEast());
    visibleBounds.slice(1).forEach((bounds) => combined.extend(bounds));
    this.map.fitBounds(combined, { padding: [24, 24] });
    this.map.invalidateSize(false);
  }

  showUnavailableState(title, description, tone = "empty") {
    this.renderControls();
    this.renderImageList();
    this.showState(title, description, tone);
  }

  showState(title, description, tone = "empty") {
    const toneClass =
      tone === "loading" ? " survey-viewer-state--loading" : tone === "error" ? " survey-viewer-state--error" : "";
    const spinner =
      tone === "loading" ? '<div class="loading-indicator" aria-hidden="true"></div>' : "";

    this.stateElement.innerHTML = `
      <section class="state-panel${toneClass}">
        ${spinner}
        <div>
          <h2>${escapeHtml(title)}</h2>
          <p>${escapeHtml(description)}</p>
        </div>
      </section>
    `;
    this.stateElement.hidden = false;
  }

  hideState() {
    this.stateElement.hidden = true;
    this.stateElement.innerHTML = "";
  }

  showMessage(message, tone = "info") {
    if (!this.messageElement) {
      return;
    }
    this.messageElement.textContent = message;
    this.messageElement.hidden = false;
    this.messageElement.classList.toggle("is-error", tone === "error");
  }

  clearMessage() {
    if (!this.messageElement) {
      return;
    }
    this.messageElement.textContent = "";
    this.messageElement.hidden = true;
    this.messageElement.classList.remove("is-error");
  }

  destroyLayers() {
    if (this.map) {
      this.layerEntries.forEach((entry) => {
        if (this.map.hasLayer(entry.layer)) {
          this.map.removeLayer(entry.layer);
        }
      });
    }
    this.layerEntries = [];
    this.imageEntries = [];
  }

  destroy() {
    this.destroyLayers();
    this.resizeObserver?.disconnect();
    this.resizeObserver = null;
    if (this.map) {
      this.map.remove();
      this.map = null;
    }
  }
}
