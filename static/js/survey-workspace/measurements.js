import {
  ApiError,
  APPROVAL_ACTION_LABELS,
  ASSET_SOURCE_LABELS,
  NON_TERMINAL_JOB_STATUSES,
  PRIMARY_UPLOAD_EXTENSIONS,
  RELATED_ASSET_RULES,
  TERMINAL_JOB_STATUSES,
  api,
  appendRelatedAssetsToFormData,
  buildRelatedAssetSummary,
  getFileExtension,
  isUnauthorized,
  mergeRelatedAssetSelections,
  prettyRole,
  removeRelatedAssetSelection,
  selectReferencedGltfBundleAssets,
  startPolling,
} from "./dependencies.js";

export const measurementMethods = {
  syncMeasurementInteractionState() {
    const map = this.mapViewer?.getMap();
    const leaflet = this.mapViewer?.getLeaflet();
    const mapReady = Boolean(map && leaflet && this.mapViewer.hasRenderableLayer());
    const canCreate = Boolean(this.auth?.user);

    this.measurementForm.hidden = !canCreate;
    if (!canCreate) {
      return;
    }

    this.measurementSaveSubmit.disabled = !mapReady;
    this.measurementResetButton.disabled = !mapReady && this.drawingCoordinates.length === 0;
    this.measurementDrawingHelp.textContent = mapReady
      ? `${this.prettyEnum(this.measurementTypeInput.value)} measurement: click the rendered map to place points, then save.`
      : "Saved measurements remain visible here. Map placement requires at least one rendered map layer.";

    if (!mapReady) {
      this.detachMapClickHandler();
      this.renderMeasurementPreview();
      return;
    }

    if (!this.mapClickHandler) {
      this.mapClickHandler = (event) => {
        this.drawingCoordinates.push([event.latlng.lng, event.latlng.lat]);
        this.renderMeasurementPreview();
      };
      map.on("click", this.mapClickHandler);
    }

    this.ensureMeasurementGroups();
    this.renderMeasurementPreview();
  },

  ensureMeasurementGroups() {
    const leaflet = this.mapViewer?.getLeaflet();
    const map = this.mapViewer?.getMap();
    if (!leaflet || !map) {
      return;
    }
    if (!this.measurementPreviewGroup) {
      this.measurementPreviewGroup = leaflet.layerGroup().addTo(map);
    }
    if (!this.measurementSavedGroup) {
      this.measurementSavedGroup = leaflet.layerGroup().addTo(map);
    }
  },

  detachMapClickHandler() {
    const map = this.mapViewer?.getMap();
    if (map && this.mapClickHandler) {
      map.off("click", this.mapClickHandler);
    }
    this.mapClickHandler = null;
  },

  renderMeasurementPreview() {
    const leaflet = this.mapViewer?.getLeaflet();
    if (!leaflet || !this.measurementPreviewGroup) {
      return;
    }

    this.measurementPreviewGroup.clearLayers();
    if (this.drawingCoordinates.length === 0) {
      return;
    }

    const latLngs = this.drawingCoordinates.map(([longitude, latitude]) => [latitude, longitude]);
    latLngs.forEach((latLng) => {
      leaflet.circleMarker(latLng, {
        radius: 5,
        color: "#8c5d1c",
        weight: 2,
        fillColor: "#f4e6cb",
        fillOpacity: 1,
      }).addTo(this.measurementPreviewGroup);
    });

    if (latLngs.length >= 2) {
      const isArea = this.measurementTypeInput.value === "AREA";
      const shape = isArea && latLngs.length >= 3
        ? leaflet.polygon(latLngs, { color: "#8c5d1c", weight: 2, fillOpacity: 0.2 })
        : leaflet.polyline(latLngs, { color: "#8c5d1c", weight: 3 });
      shape.addTo(this.measurementPreviewGroup);
    }
  },

  renderSavedMeasurementOverlays() {
    const leaflet = this.mapViewer?.getLeaflet();
    if (!leaflet || !this.measurementSavedGroup) {
      return;
    }

    this.measurementSavedGroup.clearLayers();
    for (const measurement of this.measurements) {
      const latLngs = (measurement.coordinates || []).map(([longitude, latitude]) => [latitude, longitude]);
      if (latLngs.length === 0) {
        continue;
      }

      const color = measurement.type === "AREA" ? "#176b57" : "#0f4a8a";
      latLngs.forEach((latLng) => {
        leaflet.circleMarker(latLng, {
          radius: 4,
          color,
          weight: 2,
          fillColor: "#ffffff",
          fillOpacity: 1,
        }).addTo(this.measurementSavedGroup);
      });

      const shape = measurement.type === "AREA"
        ? leaflet.polygon(latLngs, { color, weight: 2, fillOpacity: 0.12 })
        : leaflet.polyline(latLngs, { color, weight: 3 });
      shape.bindPopup(
        `<strong>${this.escapeHtml(measurement.name)}</strong><br>${this.escapeHtml(this.formatMeasurementValue(measurement.calculated_value, measurement.unit, false))}`,
      );
      shape.addTo(this.measurementSavedGroup);
    }
  },

  resetMeasurementDrawing() {
    this.drawingCoordinates = [];
    this.renderMeasurementPreview();
    if (this.measurementForm) {
      this.measurementNameInput.value = "";
    }
    if (this.measurementResetButton) {
      this.measurementResetButton.disabled = !this.mapViewer?.hasRenderableLayer();
    }
  },

  async handleMeasurementSave(event) {
    event.preventDefault();
    this.clearMessage(this.measurementMessage);

    if (!this.mapViewer?.hasRenderableLayer()) {
      this.showMessage(
        this.measurementMessage,
        "Map placement requires a rendered map layer for this survey.",
        "error",
      );
      return;
    }

    const payload = {
      type: this.measurementTypeInput.value,
      name: this.measurementNameInput.value.trim(),
      coordinates: this.drawingCoordinates,
    };

    this.measurementSaveSubmit.disabled = true;
    try {
      await api.post(`/api/v1/surveys/${this.surveyId}/measurements`, payload);
      this.showMessage(this.measurementMessage, "Measurement saved successfully.");
      this.resetMeasurementDrawing();
      await this.refreshMeasurements();
    } catch (error) {
      if (isUnauthorized(error)) {
        await this.loadWorkspace();
        return;
      }
      this.showMessage(this.measurementMessage, this.describeError(error), "error");
    } finally {
      this.measurementSaveSubmit.disabled = !this.mapViewer?.hasRenderableLayer();
    }
  },

  async handleMeasurementDelete(event) {
    const button = event.target.closest("[data-delete-measurement-id]");
    if (!button) {
      return;
    }

    button.disabled = true;
    this.clearMessage(this.measurementMessage);
    try {
      await api.delete(
        `/api/v1/surveys/${this.surveyId}/measurements/${button.dataset.deleteMeasurementId}`,
      );
      this.showMessage(this.measurementMessage, "Measurement deleted.");
      await this.refreshMeasurements();
    } catch (error) {
      if (isUnauthorized(error)) {
        await this.loadWorkspace();
        return;
      }
      this.showMessage(this.measurementMessage, this.describeError(error), "error");
      button.disabled = false;
    }
  }
};
