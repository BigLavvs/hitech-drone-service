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

export const dataMethods = {
  async refreshAll() {
    if (this.sessionBlocked) {
      return;
    }

    const [surveyResult, filesResult, approvalResult, viewersResult, measurementsResult, auditResult] =
      await Promise.allSettled([
        api.get(`/api/v1/surveys/${this.surveyId}`),
        api.get(`/api/v1/surveys/${this.surveyId}/files`),
        this.fetchApproval(),
        this.refreshViewers(),
        api.get(`/api/v1/surveys/${this.surveyId}/measurements`),
        this.fetchAuditLogs(),
      ]);

    if (surveyResult.status === "fulfilled") {
      this.survey = surveyResult.value;
      this.renderOverview();
    } else if (isUnauthorized(surveyResult.reason)) {
      await this.loadWorkspace();
      return;
    } else {
      this.showMessage(this.overviewMessage, this.describeError(surveyResult.reason), "error");
    }

    if (filesResult.status === "fulfilled") {
      this.files = filesResult.value;
      this.renderFiles();
      this.syncPolling();
    } else if (isUnauthorized(filesResult.reason)) {
      await this.loadWorkspace();
      return;
    } else {
      this.filesContent.innerHTML = this.renderStatePanel(
        "Files unavailable",
        this.describeError(filesResult.reason),
        "error",
      );
    }

    if (approvalResult.status === "fulfilled") {
      this.approval = approvalResult.value;
      this.renderApproval();
    } else if (isUnauthorized(approvalResult.reason)) {
      await this.loadWorkspace();
      return;
    } else {
      this.showMessage(this.approvalMessage, this.describeError(approvalResult.reason), "error");
      this.approvalHistoryContent.innerHTML = this.renderStatePanel(
        "Approval unavailable",
        this.describeError(approvalResult.reason),
        "error",
      );
    }

    if (measurementsResult.status === "fulfilled") {
      this.measurements = measurementsResult.value;
      this.renderMeasurements();
    } else if (isUnauthorized(measurementsResult.reason)) {
      await this.loadWorkspace();
      return;
    } else {
      this.measurementsContent.innerHTML = this.renderStatePanel(
        "Measurements unavailable",
        this.describeError(measurementsResult.reason),
        "error",
      );
    }

    if (auditResult.status === "fulfilled") {
      this.auditLogPayload = auditResult.value;
      this.renderAudit();
    } else if (isUnauthorized(auditResult.reason)) {
      await this.loadWorkspace();
      return;
    } else {
      this.auditContent.innerHTML = this.renderStatePanel(
        "Audit unavailable",
        this.describeError(auditResult.reason),
        "error",
      );
    }

    if (viewersResult.status === "rejected" && isUnauthorized(viewersResult.reason)) {
      await this.loadWorkspace();
    }
  },

  async refreshFiles() {
    try {
      this.files = await api.get(`/api/v1/surveys/${this.surveyId}/files`);
      this.renderFiles();
      this.syncPolling();
    } catch (error) {
      if (isUnauthorized(error)) {
        await this.loadWorkspace();
        return;
      }

      this.filesContent.innerHTML = this.renderStatePanel(
        "Files unavailable",
        this.describeError(error),
        "error",
      );
      this.showMessage(this.filesMessage, this.describeError(error), "error");
    }
  },

  async refreshApproval() {
    try {
      this.approval = await this.fetchApproval();
      this.renderApproval();
    } catch (error) {
      if (isUnauthorized(error)) {
        await this.loadWorkspace();
        return;
      }

      this.approval = null;
      this.renderApproval();
    }
  },

  async refreshViewers() {
    await Promise.all([this.refreshMapViewer(), this.refreshModelViewer()]);
  },

  async refreshMapViewer() {
    if (!this.mapViewer) {
      return;
    }

    try {
      await this.mapViewer.load(this.surveyId);
      this.syncMeasurementInteractionState();
      this.renderSavedMeasurementOverlays();
    } catch (error) {
      if (isUnauthorized(error)) {
        throw error;
      }
      this.mapViewer.showUnavailableState("Map unavailable", this.describeError(error), "error");
      this.syncMeasurementInteractionState();
    }
  },

  async refreshModelViewer() {
    if (!this.modelViewer) {
      return;
    }

    try {
      await this.modelViewer.load(this.surveyId);
    } catch (error) {
      if (isUnauthorized(error)) {
        throw error;
      }
      this.modelViewer.showUnavailableState("Model unavailable", this.describeError(error), "error");
    }
  },

  async refreshMeasurements() {
    try {
      this.measurements = await api.get(`/api/v1/surveys/${this.surveyId}/measurements`);
      this.renderMeasurements();
    } catch (error) {
      if (isUnauthorized(error)) {
        await this.loadWorkspace();
        return;
      }

      this.measurementsContent.innerHTML = this.renderStatePanel(
        "Measurements unavailable",
        this.describeError(error),
        "error",
      );
      this.showMessage(this.measurementMessage, this.describeError(error), "error");
    }
  },

  async refreshAudit() {
    try {
      this.auditLogPayload = await this.fetchAuditLogs();
      this.renderAudit();
    } catch (error) {
      if (isUnauthorized(error)) {
        await this.loadWorkspace();
        return;
      }

      this.auditContent.innerHTML = this.renderStatePanel(
        "Audit unavailable",
        this.describeError(error),
        "error",
      );
      this.showMessage(this.auditMessage, this.describeError(error), "error");
    }
  },

  async fetchApproval() {
    try {
      return await api.get(`/api/v1/surveys/${this.surveyId}/approvals`);
    } catch (error) {
      if (error instanceof ApiError && error.status === 404) {
        return null;
      }
      throw error;
    }
  },

  async fetchAuditLogs() {
    const params = new URLSearchParams({ survey_id: this.surveyId, limit: "20", offset: "0" });
    for (const [key, value] of Object.entries(this.auditFilters)) {
      if (value) {
        params.set(key, value);
      }
    }
    return await api.get(`/api/v1/audit-logs?${params.toString()}`);
  }
};
