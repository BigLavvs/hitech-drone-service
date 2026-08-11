import { api, ApiError } from "./api.js";
import { applyShellUser, isUnauthorized, loadAuthState, prettyRole } from "./modules/auth-shell.js";
import {
  appendRelatedAssetsToFormData,
  buildRelatedAssetSummary,
  getFileExtension,
  mergeRelatedAssetSelections,
  removeRelatedAssetSelection,
  selectReferencedGltfBundleAssets,
} from "./modules/gltf-bundle.js";
import { createMapViewer } from "./modules/map-viewer.js";
import { createModelViewer } from "./modules/model-viewer.js";
import { startPolling } from "./modules/polling.js";

const TERMINAL_JOB_STATUSES = new Set(["completed", "failed"]);
const NON_TERMINAL_JOB_STATUSES = new Set(["queued", "running"]);
const PRIMARY_UPLOAD_ACCEPT =
  ".tif,.tiff,.png,.jpg,.jpeg,.kml,.geojson,.json,.geo.json,.obj,.glb,.gltf,.las,.laz,.ply,.stl";
const PRIMARY_UPLOAD_EXTENSIONS = new Set(
  PRIMARY_UPLOAD_ACCEPT.split(",").map((value) => value.toLowerCase()),
);
const RELATED_ASSET_RULES = {
  ".obj": {
    label: "OBJ related assets for an .obj primary file",
    help: "Choose all related files. Accepted companion formats: .mtl, .png, .jpg, .jpeg.",
    accept: ".mtl,.png,.jpg,.jpeg",
    extensions: new Set([".mtl", ".png", ".jpg", ".jpeg"]),
    name: "OBJ",
  },
  ".gltf": {
    label: "GLTF related assets for a .gltf primary file",
    help: "Fallback picker: choose every referenced .bin, .png, .jpg, or .jpeg file directly if folder selection is unavailable.",
    accept: ".bin,.png,.jpg,.jpeg",
    extensions: new Set([".bin", ".png", ".jpg", ".jpeg"]),
    name: "GLTF",
  },
};
const ASSET_SOURCE_LABELS = {
  folder: "from the selected GLTF bundle folder",
  picker: "with the fallback file picker",
};
const APPROVAL_ACTION_LABELS = {
  submitted: "Submitted",
  approved: "Approved",
  rejected: "Rejected",
  archived: "Archived",
};

export function initialiseSurveyWorkspace() {
  const root = document.querySelector("[data-survey-workspace]");
  if (!root) return;
  const controller = new SurveyWorkspaceController(root);
  controller.initialise();
}

class SurveyWorkspaceController {
  constructor(root) {
    this.root = root;
    this.surveyId = root.dataset.surveyId;
    this.sessionMessage = root.querySelector("[data-workspace-session-message]");
    this.overviewMessage = root.querySelector("[data-overview-message]");
    this.filesMessage = root.querySelector("[data-files-message]");
    this.approvalMessage = root.querySelector("[data-approval-message]");
    this.mapMessage = root.querySelector("[data-map-message]");
    this.modelMessage = root.querySelector("[data-model-message]");
    this.measurementMessage = root.querySelector("[data-measurement-message]");
    this.auditMessage = root.querySelector("[data-audit-message]");
    this.filesContent = root.querySelector("[data-files-content]");
    this.mapViewerRoot = root.querySelector("[data-map-viewer]");
    this.modelViewerRoot = root.querySelector("[data-model-viewer]");
    this.uploadForm = root.querySelector("[data-upload-form]");
    this.primaryFileInput = root.querySelector("#survey-primary-file");
    this.primaryFileHelp = root.querySelector("[data-upload-primary-help]");
    this.primaryFileSelection = root.querySelector("[data-upload-primary-selection]");
    this.assetFileInput = root.querySelector("#survey-asset-files");
    this.assetField = root.querySelector("[data-upload-assets-field]");
    this.assetFieldLabel = root.querySelector("[data-upload-assets-label]");
    this.assetPickerHelp = root.querySelector("[data-upload-assets-picker-help]");
    this.assetFieldHelp = root.querySelector("[data-upload-assets-help]");
    this.assetFieldSelection = root.querySelector("[data-upload-assets-selection]");
    this.assetList = root.querySelector("[data-upload-assets-list]");
    this.gltfFolderField = root.querySelector("[data-upload-gltf-folder-field]");
    this.gltfFolderInput = root.querySelector("#survey-gltf-folder");
    this.gltfFolderHelp = root.querySelector("[data-upload-gltf-folder-help]");
    this.gltfFolderSelection = root.querySelector("[data-upload-gltf-folder-selection]");
    this.uploadSubmit = root.querySelector("[data-upload-submit]");
    this.approvalGuidance = root.querySelector("[data-approval-guidance]");
    this.approvalActions = root.querySelector("[data-approval-actions]");
    this.rejectionForm = root.querySelector("[data-rejection-form]");
    this.rejectionReasonInput = root.querySelector("#survey-rejection-reason");
    this.rejectionError = root.querySelector("[data-rejection-error]");
    this.rejectSubmit = root.querySelector("[data-reject-submit]");
    this.approvalHistoryContent = root.querySelector("[data-approval-history-content]");
    this.measurementForm = root.querySelector("[data-measurement-form]");
    this.measurementTypeInput = root.querySelector("#measurement-type");
    this.measurementNameInput = root.querySelector("#measurement-name");
    this.measurementSaveSubmit = root.querySelector("[data-measurement-save-submit]");
    this.measurementResetButton = root.querySelector("[data-measurement-reset-button]");
    this.measurementDrawingHelp = root.querySelector("[data-measurement-drawing-help]");
    this.measurementsContent = root.querySelector("[data-measurements-content]");
    this.auditFilterForm = root.querySelector("[data-audit-filter-form]");
    this.auditResetButton = root.querySelector("[data-audit-reset-button]");
    this.auditContent = root.querySelector("[data-audit-content]");
    this.overviewFields = new Map(
      [...root.querySelectorAll("[data-overview-field]")].map((element) => [
        element.dataset.overviewField,
        element,
      ]),
    );
    this.approvalFields = new Map(
      [...root.querySelectorAll("[data-approval-field]")].map((element) => [
        element.dataset.approvalField,
        element,
      ]),
    );

    this.auth = null;
    this.survey = null;
    this.files = [];
    this.approval = null;
    this.measurements = [];
    this.auditLogPayload = null;
    this.auditFilters = {};
    this.pollStops = new Map();
    this.sessionBlocked = false;
    this.rejectFormVisible = false;
    this.drawingCoordinates = [];
    this.selectedAssets = [];
    this.uploadPrimaryExtension = "";
    this.mapClickHandler = null;
    this.measurementPreviewGroup = null;
    this.measurementSavedGroup = null;
    this.mapViewer = this.mapViewerRoot
      ? createMapViewer(this.mapViewerRoot, { messageElement: this.mapMessage })
      : null;
    this.modelViewer = this.modelViewerRoot
      ? createModelViewer(this.modelViewerRoot, { messageElement: this.modelMessage })
      : null;
  }

  initialise() {
    this.bindEvents();
    this.loadWorkspace();
  }

  bindEvents() {
    this.uploadForm?.addEventListener("submit", (event) => this.handleUpload(event));
    this.uploadForm?.addEventListener("click", (event) => this.handleUploadSelectionClick(event));
    this.primaryFileInput?.addEventListener("change", () => this.handlePrimaryFileChange());
    this.gltfFolderInput?.addEventListener("change", () => this.handleGltfFolderChange());
    this.assetFileInput?.addEventListener("change", () => this.handleAssetFileChange());
    this.filesContent?.addEventListener("click", (event) => this.handleFilesClick(event));
    this.approvalActions?.addEventListener("click", (event) => this.handleApprovalActionClick(event));
    this.rejectionForm?.addEventListener("submit", (event) => this.handleReject(event));
    this.measurementForm?.addEventListener("submit", (event) => this.handleMeasurementSave(event));
    this.measurementTypeInput?.addEventListener("change", () => this.syncMeasurementInteractionState());
    this.measurementResetButton?.addEventListener("click", () => this.resetMeasurementDrawing());
    this.measurementsContent?.addEventListener("click", (event) => this.handleMeasurementDelete(event));
    this.auditFilterForm?.addEventListener("submit", (event) => this.handleAuditFilterSubmit(event));
    this.auditResetButton?.addEventListener("click", () => this.handleAuditReset());
  }

  async loadWorkspace() {
    this.clearMessage(this.sessionMessage);
    this.sessionBlocked = false;

    const [authResult, surveyResult] = await Promise.allSettled([
      loadAuthState({ force: true }),
      api.get(`/api/v1/surveys/${this.surveyId}`),
    ]);

    if (this.handleAuthFailure(authResult, surveyResult)) {
      return;
    }

    if (surveyResult.status !== "fulfilled") {
      this.renderFatalSurveyError(surveyResult.reason);
      return;
    }

    this.auth = authResult.value;
    applyShellUser(this.auth);
    this.survey = surveyResult.value;
    this.renderOverview();

    await Promise.all([
      this.refreshFiles(),
      this.refreshApproval(),
      this.refreshViewers(),
      this.refreshMeasurements(),
      this.refreshAudit(),
    ]);
  }

  handleAuthFailure(authResult, surveyResult) {
    const authUnauthorized = authResult.status === "rejected" && isUnauthorized(authResult.reason);
    const surveyUnauthorized = surveyResult.status === "rejected" && isUnauthorized(surveyResult.reason);
    if (!authUnauthorized && !surveyUnauthorized) {
      return false;
    }

    this.sessionBlocked = true;
    applyShellUser(null);
    this.showMessage(
      this.sessionMessage,
      "Your Hitech sign-in session is missing or expired. Sign in again to continue.",
      "error",
    );
    this.showMessage(
      this.overviewMessage,
      "Survey data is unavailable until you sign in again.",
      "error",
    );
    this.filesContent.innerHTML = this.renderStatePanel(
      "Session required",
      "Sign in again to load survey files and processing status.",
      "error",
    );
    this.approvalHistoryContent.innerHTML = this.renderStatePanel(
      "Session required",
      "Sign in again to load approval details and actions.",
      "error",
    );
    this.measurementsContent.innerHTML = this.renderStatePanel(
      "Session required",
      "Sign in again to load and save measurements.",
      "error",
    );
    this.auditContent.innerHTML = this.renderStatePanel(
      "Session required",
      "Sign in again to load the audit timeline.",
      "error",
    );
    this.mapViewer?.showUnavailableState(
      "Session required",
      "Sign in again to load private map layers for this survey.",
      "error",
    );
    this.modelViewer?.showUnavailableState(
      "Session required",
      "Sign in again to load private 3D model data for this survey.",
      "error",
    );
    this.uploadForm.hidden = true;
    this.syncUploadAssetField();
    this.measurementForm.hidden = true;
    this.rejectionForm.hidden = true;
    this.auditFilterForm.hidden = true;
    this.rejectFormVisible = false;
    this.approvalActions.innerHTML = "";
    this.resetMeasurementDrawing();
    this.stopPolling();
    return true;
  }

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
  }

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
  }

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
  }

  async refreshViewers() {
    await Promise.all([this.refreshMapViewer(), this.refreshModelViewer()]);
  }

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
  }

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
  }

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
  }

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
  }

  async fetchApproval() {
    try {
      return await api.get(`/api/v1/surveys/${this.surveyId}/approvals`);
    } catch (error) {
      if (error instanceof ApiError && error.status === 404) {
        return null;
      }
      throw error;
    }
  }

  async fetchAuditLogs() {
    const params = new URLSearchParams({ survey_id: this.surveyId, limit: "20", offset: "0" });
    for (const [key, value] of Object.entries(this.auditFilters)) {
      if (value) {
        params.set(key, value);
      }
    }
    return await api.get(`/api/v1/audit-logs?${params.toString()}`);
  }

  renderOverview() {
    this.clearMessage(this.overviewMessage);
    if (!this.survey) {
      return;
    }

    const values = {
      project: this.referenceLabel("Project", this.survey.project_id),
      site: this.referenceLabel("Site", this.survey.site_id),
      survey_date: this.formatDate(this.survey.survey_date),
      drone_model: this.presentValue(this.survey.drone_model),
      pilot: this.presentValue(this.survey.pilot),
      coordinate_reference_system: this.presentValue(this.survey.coordinate_reference_system),
      status: this.renderBadgeLabel(this.survey.status, "survey"),
      processing_status: this.renderBadgeLabel(this.survey.processing_status, "processing"),
      notes: this.presentValue(this.survey.notes),
      created_by: this.userLabel(this.survey.created_by),
      approved_by: this.userLabel(this.survey.approved_by),
    };

    for (const [key, element] of this.overviewFields.entries()) {
      element.innerHTML = values[key] ?? '<span class="survey-muted-value">Unavailable</span>';
    }
  }

  renderFiles() {
    this.clearMessage(this.filesMessage);
    this.renderUploadForm();

    if (!Array.isArray(this.files) || this.files.length === 0) {
      this.filesContent.innerHTML = this.renderStatePanel(
        "No files uploaded",
        "No files have been uploaded to this survey yet.",
      );
      return;
    }

    const rows = this.files
      .map((file) => {
        const job = file.processing_job;
        const canRetry =
          job &&
          job.status === "failed" &&
          this.auth?.user &&
          ["ADMINISTRATOR", "SURVEY_ENGINEER"].includes(this.auth.user.role);
        const canDownload = this.survey?.status === "APPROVED";

        return `
          <tr>
            <td>
              <div class="survey-file-name">${this.escapeHtml(file.original_filename)}</div>
              <div class="survey-file-meta">${this.escapeHtml(this.prettyEnum(file.format))} - ${this.escapeHtml(this.prettyEnum(file.file_type))} - ${this.formatBytes(file.size_bytes)}</div>
            </td>
            <td>${this.renderBadge(file.status, "file")}</td>
            <td>
              ${
                job
                  ? `
                    ${this.renderBadge(job.status, "processing")}
                    <div class="survey-job-detail">Progress ${this.formatPercent(job.progress_percent)} - Retries ${this.escapeHtml(String(job.retry_count))}</div>
                  `
                  : '<span class="survey-muted-value">No processing job</span>'
              }
            </td>
            <td class="survey-table-actions">
              ${
                canRetry
                  ? `<button class="button survey-action-button" type="button" data-retry-job-id="${job.id}">Retry processing</button>`
                  : ""
              }
              ${
                canDownload
                  ? `<a class="survey-download-link" href="/api/v1/surveys/${this.surveyId}/files/${file.id}/download">Download</a>`
                  : '<span class="survey-muted-value">Available after approval</span>'
              }
            </td>
          </tr>
        `;
      })
      .join("");

    this.filesContent.innerHTML = `
      <div class="table-wrap">
        <table class="data-table">
          <thead>
            <tr>
              <th scope="col">File</th>
              <th scope="col">File status</th>
              <th scope="col">Processing</th>
              <th scope="col">Actions</th>
            </tr>
          </thead>
          <tbody>${rows}</tbody>
        </table>
      </div>
    `;
  }

  renderUploadForm() {
    const role = this.auth?.user?.role;
    const canUpload = ["ADMINISTRATOR", "PROJECT_MANAGER", "SURVEY_ENGINEER"].includes(role);
    this.uploadForm.hidden = !canUpload;
    if (this.primaryFileInput) {
      this.primaryFileInput.setAttribute("accept", PRIMARY_UPLOAD_ACCEPT);
    }
    this.syncUploadAssetField();
  }

  renderApproval() {
    this.clearMessage(this.approvalMessage);
    this.renderApprovalFields();
    this.renderApprovalGuidance();
    this.renderApprovalActions();
    this.renderApprovalHistory();
  }

  renderApprovalFields() {
    const fields = {
      current_status: this.survey ? this.renderBadgeLabel(this.survey.status, "survey") : '<span class="survey-muted-value">Unavailable</span>',
      submitted_at: this.approval ? this.formatDateTime(this.approval.submitted_at) : '<span class="survey-muted-value">Not submitted</span>',
      submitted_by: this.approval ? this.userLabel(this.approval.submitted_by) : '<span class="survey-muted-value">Not submitted</span>',
      approved_at: this.approval ? this.formatDateTime(this.approval.approved_at) : '<span class="survey-muted-value">Not approved</span>',
      approved_by: this.approval ? this.userLabel(this.approval.approved_by) : '<span class="survey-muted-value">Not approved</span>',
      rejection_reason: this.approval ? this.presentValue(this.approval.rejection_reason) : '<span class="survey-muted-value">No approval record</span>',
    };

    for (const [key, element] of this.approvalFields.entries()) {
      element.innerHTML = fields[key] ?? '<span class="survey-muted-value">Unavailable</span>';
    }
  }

  renderApprovalActions() {
    const role = this.auth?.user?.role;
    const surveyStatus = this.survey?.status;
    const actions = [];
    const selfReviewBlocked =
      ["PROJECT_MANAGER", "ADMINISTRATOR"].includes(role) &&
      surveyStatus === "PENDING_APPROVAL" &&
      this.survey?.created_by === this.auth?.user?.id;

    if (role === "SURVEY_ENGINEER" && surveyStatus === "READY") {
      actions.push(
        '<button class="button button--primary survey-action-button" type="button" data-approval-action="submit">Submit for approval</button>',
      );
    }
    if (
      ["PROJECT_MANAGER", "ADMINISTRATOR"].includes(role) &&
      surveyStatus === "PENDING_APPROVAL" &&
      !selfReviewBlocked
    ) {
      actions.push(
        '<button class="button button--primary survey-action-button" type="button" data-approval-action="approve">Approve survey</button>',
      );
      actions.push(
        '<button class="button survey-action-button" type="button" data-approval-action="show-reject">Reject survey</button>',
      );
    }
    if (["PROJECT_MANAGER", "ADMINISTRATOR"].includes(role) && ["APPROVED", "REJECTED"].includes(surveyStatus)) {
      actions.push(
        '<button class="button survey-action-button" type="button" data-approval-action="archive">Archive survey</button>',
      );
    }

    this.approvalActions.innerHTML = actions.join("");
    const canReject =
      ["PROJECT_MANAGER", "ADMINISTRATOR"].includes(role) &&
      surveyStatus === "PENDING_APPROVAL" &&
      !selfReviewBlocked;
    this.rejectionForm.hidden = !canReject || !this.rejectFormVisible;
    if (this.rejectionForm.hidden) {
      this.rejectionReasonInput.value = "";
      this.hideRejectionError();
    }
  }

  renderApprovalGuidance() {
    if (!this.approvalGuidance) {
      return;
    }

    const status = this.survey?.status;
    const role = this.auth?.user?.role;
    const isSelfReview =
      ["PROJECT_MANAGER", "ADMINISTRATOR"].includes(role) &&
      status === "PENDING_APPROVAL" &&
      this.survey?.created_by === this.auth?.user?.id;

    const guidance = {
      DRAFT: "DRAFT: upload supported files first.",
      UPLOADING: "UPLOADING: wait for processing.",
      PROCESSING: "PROCESSING: wait for processing.",
      READY: "READY: the assigned Survey Engineer can submit for approval.",
      PENDING_APPROVAL: isSelfReview
        ? "PENDING_APPROVAL: self-review is not permitted for the survey creator."
        : "PENDING_APPROVAL: the owning Project Manager or an Administrator can approve or reject, unless they created the survey.",
      APPROVED: "APPROVED: an eligible reviewer can archive this survey.",
      REJECTED: "REJECTED: an eligible reviewer can archive this survey.",
    };

    this.approvalGuidance.textContent =
      guidance[status] || "Survey status changes are driven by upload, processing, submission, review, and archive actions.";
  }

  renderApprovalHistory() {
    if (!this.approval) {
      this.approvalHistoryContent.innerHTML = this.renderStatePanel(
        "Approval not started",
        "This survey does not have an approval record yet.",
      );
      return;
    }

    if (!Array.isArray(this.approval.history) || this.approval.history.length === 0) {
      this.approvalHistoryContent.innerHTML = this.renderStatePanel(
        "No approval history",
        "Approval history will appear here after workflow actions occur.",
      );
      return;
    }

    const rows = this.approval.history
      .map(
        (entry) => `
          <tr>
            <td>${this.escapeHtml(APPROVAL_ACTION_LABELS[entry.action] || entry.action)}</td>
            <td>${this.userLabel(entry.actor_id)}</td>
            <td>${this.formatDateTime(entry.timestamp)}</td>
          </tr>
        `,
      )
      .join("");

    this.approvalHistoryContent.innerHTML = `
      <h3 class="survey-history-heading">Approval history</h3>
      <div class="table-wrap">
        <table class="data-table">
          <thead>
            <tr>
              <th scope="col">Action</th>
              <th scope="col">Actor</th>
              <th scope="col">Timestamp</th>
            </tr>
          </thead>
          <tbody>${rows}</tbody>
        </table>
      </div>
    `;
  }

  renderMeasurements() {
    this.clearMessage(this.measurementMessage);
    this.syncMeasurementInteractionState();
    this.renderSavedMeasurementOverlays();

    if (!Array.isArray(this.measurements) || this.measurements.length === 0) {
      this.measurementsContent.innerHTML = this.renderStatePanel(
        "No saved measurements",
        "No measurements have been saved for this survey yet.",
      );
      return;
    }

    const canDelete = ["ADMINISTRATOR", "PROJECT_MANAGER"].includes(this.auth?.user?.role);
    const rows = this.measurements
      .map(
        (measurement) => `
          <tr>
            <td>
              <strong>${this.escapeHtml(measurement.name)}</strong>
              <div class="survey-file-meta">${this.escapeHtml(this.prettyEnum(measurement.type))}</div>
            </td>
            <td>${this.formatMeasurementValue(measurement.calculated_value, measurement.unit)}</td>
            <td>${this.userLabel(measurement.created_by)}</td>
            <td>${this.formatDateTime(measurement.created_at)}</td>
            <td class="survey-table-actions">
              ${
                canDelete
                  ? `<button class="button survey-action-button" type="button" data-delete-measurement-id="${measurement.id}">Delete</button>`
                  : '<span class="survey-muted-value">Read only</span>'
              }
            </td>
          </tr>
        `,
      )
      .join("");

    this.measurementsContent.innerHTML = `
      <div class="table-wrap">
        <table class="data-table">
          <thead>
            <tr>
              <th scope="col">Measurement</th>
              <th scope="col">Calculated value</th>
              <th scope="col">Created by</th>
              <th scope="col">Created</th>
              <th scope="col">Actions</th>
            </tr>
          </thead>
          <tbody>${rows}</tbody>
        </table>
      </div>
    `;
  }

  renderAudit() {
    this.clearMessage(this.auditMessage);
    this.auditFilterForm.hidden = false;
    const logs = Array.isArray(this.auditLogPayload?.results) ? this.auditLogPayload.results : [];

    if (logs.length === 0) {
      this.auditContent.innerHTML = this.renderStatePanel(
        "No audit events",
        "No audit events matched the current survey and filter selection.",
      );
      return;
    }

    const rows = logs
      .map(
        (entry) => `
          <tr>
            <td>${this.escapeHtml(this.prettyEnum(entry.action))}</td>
            <td>${this.userLabel(entry.user_id)}</td>
            <td>${this.formatDateTime(entry.timestamp)}</td>
            <td>${this.escapeHtml(`${entry.entity_type} #${entry.entity_id}`)}</td>
            <td>${this.renderAuditDetails(entry.details)}</td>
          </tr>
        `,
      )
      .join("");

    this.auditContent.innerHTML = `
      <div class="table-wrap">
        <table class="data-table">
          <thead>
            <tr>
              <th scope="col">Action</th>
              <th scope="col">Actor</th>
              <th scope="col">Timestamp</th>
              <th scope="col">Entity</th>
              <th scope="col">Details</th>
            </tr>
          </thead>
          <tbody>${rows}</tbody>
        </table>
      </div>
    `;
  }

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
  }

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
  }

  detachMapClickHandler() {
    const map = this.mapViewer?.getMap();
    if (map && this.mapClickHandler) {
      map.off("click", this.mapClickHandler);
    }
    this.mapClickHandler = null;
  }

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
  }

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
  }

  resetMeasurementDrawing() {
    this.drawingCoordinates = [];
    this.renderMeasurementPreview();
    if (this.measurementForm) {
      this.measurementNameInput.value = "";
    }
    if (this.measurementResetButton) {
      this.measurementResetButton.disabled = !this.mapViewer?.hasRenderableLayer();
    }
  }

  handlePrimaryFileChange() {
    const file = this.primaryFileInput?.files?.[0];
    if (file && !PRIMARY_UPLOAD_EXTENSIONS.has(this.fileExtension(file.name))) {
      this.primaryFileInput.value = "";
      this.showMessage(this.filesMessage, "Unsupported primary file type selected.", "error");
    } else {
      if (file) {
        this.clearPrimaryDependentSelections();
      }
      this.clearMessage(this.filesMessage);
    }
    this.syncUploadAssetField();
  }

  handleAssetFileChange() {
    if (!this.assetFileInput?.files?.length) {
      return;
    }

    const assetRule = this.relatedAssetRule();
    if (!assetRule) {
      this.assetFileInput.value = "";
      this.clearSelectedAssets();
      this.showMessage(this.filesMessage, "Related assets are allowed only when the primary file is .obj or .gltf.", "error");
      this.syncUploadAssetField();
      return;
    }

    const invalidAsset = [...this.assetFileInput.files].find(
      (file) => !assetRule.extensions.has(this.fileExtension(file.name)),
    );
    if (invalidAsset) {
      this.assetFileInput.value = "";
      this.clearSelectedAssets();
      this.showMessage(this.filesMessage, `Unsupported ${assetRule.name} asset type selected.`, "error");
      this.renderAssetSelection();
      return;
    }

    this.mergeSelectedAssets(
      [...this.assetFileInput.files].map((file) => ({
        file,
        displayName: file.name,
        source: "picker",
      })),
    );
    this.assetFileInput.value = "";
    this.renderAssetSelection();
    this.clearMessage(this.filesMessage);
  }

  async handleGltfFolderChange() {
    if (!this.gltfFolderInput?.files?.length) {
      this.renderGltfFolderSelection();
      return;
    }

    const primaryFile = this.primaryFileInput?.files?.[0];
    if (!primaryFile || this.fileExtension(primaryFile.name) !== ".gltf") {
      this.gltfFolderInput.value = "";
      this.clearSelectedAssets();
      this.showMessage(this.filesMessage, "GLTF bundle folder selection is available only for a .gltf primary file.", "error");
      this.syncUploadAssetField();
      return;
    }

    const folderFiles = [...this.gltfFolderInput.files];

    try {
      const selection = selectReferencedGltfBundleAssets({
        manifestText: await primaryFile.text(),
        folderFiles,
      });
      this.removeSelectedAssetsBySource("folder");
      this.mergeSelectedAssets(
        selection.selectedAssets.map((asset) => ({
          file: asset.file,
          displayName: asset.relativePath,
          source: "folder",
        })),
      );

      if (selection.unsupportedReferences.length > 0 || selection.missingReferences.length > 0) {
        const issues = [];
        if (selection.unsupportedReferences.length > 0) {
          issues.push(
            `Unsupported GLTF folder references: ${selection.unsupportedReferences.join(", ")}`,
          );
        }
        if (selection.missingReferences.length > 0) {
          issues.push(`Missing referenced files in the chosen folder: ${selection.missingReferences.join(", ")}`);
        }
        this.showMessage(
          this.filesMessage,
          `${issues.join(". ")}. Use the fallback related-assets picker if needed. Server validation remains authoritative.`,
          "error",
        );
      } else {
        this.clearMessage(this.filesMessage);
      }
    } catch (error) {
      this.gltfFolderInput.value = "";
      this.removeSelectedAssetsBySource("folder");
      this.showMessage(
        this.filesMessage,
        `${error instanceof Error ? error.message : "The GLTF bundle folder could not be matched."} Use the fallback related-assets picker if needed.`,
        "error",
      );
    }

    this.renderGltfFolderSelection();
    this.renderAssetSelection();
  }

  syncUploadAssetField() {
    if (!this.assetField || !this.assetFileInput || !this.primaryFileInput) {
      return;
    }

    const assetRule = this.relatedAssetRule();
    const currentExtension = this.fileExtension(this.primaryFileInput?.files?.[0]?.name);
    const primaryExtensionChanged =
      Boolean(this.uploadPrimaryExtension) && this.uploadPrimaryExtension !== currentExtension;

    this.assetField.hidden = !assetRule;
    this.assetFileInput.disabled = !assetRule;
    if (this.assetFieldLabel) {
      this.assetFieldLabel.textContent = assetRule?.label || "Related assets";
    }
    if (this.assetFieldHelp) {
      this.assetFieldHelp.textContent = assetRule?.help || "";
    }
    if (assetRule) {
      this.assetFileInput.setAttribute("accept", assetRule.accept);
    } else {
      this.assetFileInput.removeAttribute("accept");
    }
    if (!assetRule || primaryExtensionChanged) {
      this.assetFileInput.value = "";
    }
    if (this.assetPickerHelp) {
      this.assetPickerHelp.textContent =
        assetRule && this.selectedAssets.length > 0 ? "Add related assets" : "Choose related assets";
    }
    if (this.gltfFolderField && this.gltfFolderInput) {
      const showGltfFolder = currentExtension === ".gltf";
      this.gltfFolderField.hidden = !showGltfFolder;
      this.gltfFolderInput.disabled = !showGltfFolder;
      if (!showGltfFolder || primaryExtensionChanged) {
        this.gltfFolderInput.value = "";
      }
    }
    if (!assetRule || primaryExtensionChanged) {
      this.clearSelectedAssets();
    }
    this.uploadPrimaryExtension = assetRule ? currentExtension : "";
    this.renderPrimarySelection();
    this.renderGltfFolderSelection();
    this.renderAssetSelection();
  }

  renderPrimarySelection() {
    if (!this.primaryFileSelection || !this.primaryFileHelp) {
      return;
    }

    const primaryFile = this.primaryFileInput?.files?.[0];
    if (!primaryFile) {
      this.primaryFileHelp.textContent =
        "Choose one primary survey dataset file. Selecting another file replaces the current primary file.";
      this.primaryFileSelection.innerHTML = "";
      this.primaryFileSelection.hidden = true;
      return;
    }

    this.primaryFileHelp.textContent = "Choose or replace the primary survey dataset file.";
    this.primaryFileSelection.innerHTML = this.renderSelectionChip({
      label: primaryFile.name,
      removeAction: "remove-primary-file",
      removeLabel: `Remove primary file ${primaryFile.name}`,
    });
    this.primaryFileSelection.hidden = false;
  }

  renderAssetSelection() {
    if (!this.assetFieldSelection || !this.assetList) {
      return;
    }

    if (this.selectedAssets.length === 0) {
      this.assetFieldSelection.textContent = "";
      this.assetFieldSelection.hidden = true;
      this.assetList.innerHTML = "";
      this.assetList.hidden = true;
      if (this.assetPickerHelp) {
        this.assetPickerHelp.textContent = "Choose related assets";
      }
      return;
    }

    this.assetFieldSelection.textContent = buildRelatedAssetSummary(
      this.selectedAssetDisplayNames,
      this.selectedAssetSummaryLabel(),
    );
    this.assetFieldSelection.hidden = false;
    this.assetList.innerHTML = `<div class="survey-upload-chip-list">${this.selectedAssets
      .map((asset) =>
        this.renderSelectionChip({
          label: asset.displayName,
          removeAction: "remove-asset",
          removeValue: asset.key,
          removeLabel: `Remove related asset ${asset.displayName}`,
        }),
      )
      .join("")}</div>`;
    this.assetList.hidden = false;
    if (this.assetPickerHelp) {
      this.assetPickerHelp.textContent = "Add related assets";
    }
  }

  renderGltfFolderSelection() {
    if (!this.gltfFolderSelection) {
      return;
    }

    const folderAssetDisplayNames = this.selectedAssets
      .filter((asset) => asset.source === "folder")
      .map((asset) => asset.displayName);
    if (folderAssetDisplayNames.length === 0) {
      this.gltfFolderSelection.textContent = "";
      this.gltfFolderSelection.hidden = true;
      return;
    }

    this.gltfFolderSelection.textContent = buildRelatedAssetSummary(
      folderAssetDisplayNames,
      "from the selected GLTF bundle folder",
    );
    this.gltfFolderSelection.hidden = false;
  }

  relatedAssetRule() {
    const file = this.primaryFileInput?.files?.[0];
    return RELATED_ASSET_RULES[this.fileExtension(file?.name)] || null;
  }

  fileExtension(filename) {
    return getFileExtension(filename);
  }

  get selectedAssetFiles() {
    return this.selectedAssets.map((asset) => asset.file);
  }

  get selectedAssetDisplayNames() {
    return this.selectedAssets.map((asset) => asset.displayName);
  }

  clearSelectedAssets() {
    this.selectedAssets = [];
  }

  removeSelectedAssetsBySource(source) {
    this.selectedAssets = this.selectedAssets.filter((asset) => asset.source !== source);
  }

  mergeSelectedAssets(assets) {
    this.selectedAssets = mergeRelatedAssetSelections(this.selectedAssets, assets);
  }

  removeSelectedAssetByKey(key) {
    this.selectedAssets = removeRelatedAssetSelection(this.selectedAssets, key);
    this.renderGltfFolderSelection();
    this.renderAssetSelection();
  }

  selectedAssetSummaryLabel() {
    const sources = new Set(this.selectedAssets.map((asset) => asset.source));
    if (sources.size === 1) {
      return ASSET_SOURCE_LABELS[[...sources][0]];
    }
    return "from the selected folder and fallback file picker";
  }

  renderSelectionChip({ label, removeAction, removeValue = "", removeLabel }) {
    const valueAttribute = removeValue ? ` data-remove-value="${this.escapeHtml(removeValue)}"` : "";
    return `
      <div class="survey-upload-chip">
        <span class="survey-upload-chip__label">${this.escapeHtml(label)}</span>
        <button
          class="survey-upload-chip__remove"
          type="button"
          data-upload-action="${removeAction}"${valueAttribute}
          aria-label="${this.escapeHtml(removeLabel)}"
        >
          <span aria-hidden="true">×</span>
        </button>
      </div>
    `;
  }

  async handleUpload(event) {
    event.preventDefault();
    if (this.sessionBlocked) {
      return;
    }

    const primaryInput = this.primaryFileInput;
    const assetInput = this.assetFileInput;
    const primaryFile = primaryInput.files[0];
    if (!primaryFile) {
      this.showMessage(this.filesMessage, "Choose a primary file before uploading.", "error");
      return;
    }
    if (!PRIMARY_UPLOAD_EXTENSIONS.has(this.fileExtension(primaryFile.name))) {
      primaryInput.value = "";
      this.showMessage(this.filesMessage, "Unsupported primary file type selected.", "error");
      this.syncUploadAssetField();
      return;
    }
    const assetRule = this.relatedAssetRule();
    if (!assetRule && this.selectedAssetFiles.length > 0) {
      assetInput.value = "";
      this.clearSelectedAssets();
      this.showMessage(this.filesMessage, "Related assets are allowed only when the primary file is .obj or .gltf.", "error");
      this.syncUploadAssetField();
      return;
    }
    const invalidAsset = this.selectedAssetFiles.find(
      (file) => !assetRule?.extensions.has(this.fileExtension(file.name)),
    );
    if (invalidAsset) {
      assetInput.value = "";
      this.clearSelectedAssets();
      this.showMessage(this.filesMessage, `Unsupported ${assetRule.name} asset type selected.`, "error");
      this.renderAssetSelection();
      return;
    }

    const formData = new FormData();
    formData.append("file", primaryFile);
    appendRelatedAssetsToFormData(formData, this.selectedAssetFiles);

    this.uploadSubmit.disabled = true;
    this.clearMessage(this.filesMessage);

    try {
      await api.post(`/api/v1/surveys/${this.surveyId}/files`, formData);
      this.uploadForm.reset();
      this.clearSelectedAssets();
      this.syncUploadAssetField();
      this.showMessage(this.filesMessage, "Upload accepted. Processing status will update automatically.");
      await this.refreshAll();
    } catch (error) {
      if (isUnauthorized(error)) {
        await this.loadWorkspace();
        return;
      }
      this.showMessage(this.filesMessage, this.describeError(error), "error");
    } finally {
      this.uploadSubmit.disabled = false;
    }
  }

  async handleFilesClick(event) {
    const retryButton = event.target.closest("[data-retry-job-id]");
    if (!retryButton) {
      return;
    }

    await this.handleRetry(retryButton.dataset.retryJobId, retryButton);
  }

  handleUploadSelectionClick(event) {
    const uploadActionButton = event.target.closest("[data-upload-action]");
    if (!uploadActionButton) {
      return;
    }

    if (uploadActionButton.dataset.uploadAction === "remove-primary-file") {
      this.clearPrimarySelection();
      return;
    }
    if (uploadActionButton.dataset.uploadAction === "remove-asset") {
      this.removeSelectedAssetByKey(uploadActionButton.dataset.removeValue || "");
    }
  }

  clearPrimarySelection() {
    if (this.primaryFileInput) {
      this.primaryFileInput.value = "";
    }
    this.clearPrimaryDependentSelections();
    this.clearMessage(this.filesMessage);
    this.syncUploadAssetField();
  }

  clearPrimaryDependentSelections() {
    if (this.assetFileInput) {
      this.assetFileInput.value = "";
    }
    if (this.gltfFolderInput) {
      this.gltfFolderInput.value = "";
    }
    this.clearSelectedAssets();
  }

  async handleApprovalActionClick(event) {
    const actionButton = event.target.closest("[data-approval-action]");
    if (!actionButton) {
      return;
    }

    const action = actionButton.dataset.approvalAction;
    if (action === "show-reject") {
      this.rejectFormVisible = true;
      this.renderApprovalActions();
      this.rejectionReasonInput.focus();
      return;
    }

    actionButton.disabled = true;
    this.clearMessage(this.approvalMessage);

    try {
      if (action === "submit") {
        await api.post(`/api/v1/surveys/${this.surveyId}/submit`, {});
      } else if (action === "approve") {
        await api.post(`/api/v1/surveys/${this.surveyId}/approve`, {});
      } else if (action === "archive") {
        await api.post(`/api/v1/surveys/${this.surveyId}/archive`, {});
      }

      this.rejectFormVisible = false;
      await this.refreshAll();
    } catch (error) {
      if (isUnauthorized(error)) {
        await this.loadWorkspace();
        return;
      }
      this.showMessage(this.approvalMessage, this.describeError(error), "error");
    } finally {
      actionButton.disabled = false;
    }
  }

  async handleReject(event) {
    event.preventDefault();
    this.hideRejectionError();

    const reason = this.rejectionReasonInput.value.trim();
    if (!reason) {
      this.showRejectionError("Enter a rejection reason.");
      return;
    }

    this.rejectSubmit.disabled = true;
    this.clearMessage(this.approvalMessage);

    try {
      await api.post(`/api/v1/surveys/${this.surveyId}/reject`, { reason });
      this.rejectFormVisible = false;
      this.rejectionReasonInput.value = "";
      await this.refreshAll();
    } catch (error) {
      if (isUnauthorized(error)) {
        await this.loadWorkspace();
        return;
      }
      const message = this.describeError(error);
      this.showRejectionError(message);
      this.showMessage(this.approvalMessage, message, "error");
    } finally {
      this.rejectSubmit.disabled = false;
    }
  }

  async handleRetry(jobId, button) {
    button.disabled = true;
    this.clearMessage(this.filesMessage);

    try {
      await api.post(`/api/v1/processing-jobs/${jobId}/retry`, {});
      this.showMessage(this.filesMessage, "Processing retry accepted. Status will update automatically.");
      await this.refreshAll();
    } catch (error) {
      if (isUnauthorized(error)) {
        await this.loadWorkspace();
        return;
      }
      this.showMessage(this.filesMessage, this.describeError(error), "error");
    } finally {
      button.disabled = false;
    }
  }

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
  }

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

  async handleAuditFilterSubmit(event) {
    event.preventDefault();
    this.clearMessage(this.auditMessage);
    const formData = new FormData(this.auditFilterForm);
    this.auditFilters = {
      action: (formData.get("action")?.toString().trim() || "").toUpperCase(),
      from_date: formData.get("from_date")?.toString() || "",
      to_date: formData.get("to_date")?.toString() || "",
    };
    await this.refreshAudit();
  }

  async handleAuditReset() {
    this.auditFilters = {};
    this.auditFilterForm.reset();
    await this.refreshAudit();
  }

  syncPolling() {
    const activeIds = new Set();

    for (const file of this.files) {
      const job = file.processing_job;
      if (!job || !NON_TERMINAL_JOB_STATUSES.has(job.status)) {
        continue;
      }

      activeIds.add(job.id);
      if (!this.pollStops.has(job.id)) {
        const stop = startPolling(
          async () => {
            try {
              return await api.get(`/api/v1/processing-jobs/${job.id}`);
            } catch (error) {
              if (isUnauthorized(error)) {
                await this.loadWorkspace();
                return null;
              }
              this.showMessage(this.filesMessage, this.describeError(error), "error");
              return null;
            }
          },
          (payload) => this.handlePolledJobUpdate(payload),
          { interval: 5000 },
        );
        this.pollStops.set(job.id, stop);
      }
    }

    for (const [jobId, stop] of this.pollStops.entries()) {
      if (!activeIds.has(jobId)) {
        stop();
        this.pollStops.delete(jobId);
      }
    }
  }

  async handlePolledJobUpdate(payload) {
    if (!payload?.file) {
      return;
    }

    const fileIndex = this.files.findIndex((entry) => entry.id === payload.file.id);
    if (fileIndex !== -1) {
      this.files[fileIndex] = payload.file;
      this.renderFiles();
    }

    if (TERMINAL_JOB_STATUSES.has(payload.status)) {
      const stop = this.pollStops.get(payload.id);
      if (stop) {
        stop();
        this.pollStops.delete(payload.id);
      }
      await this.refreshAll();
    }
  }

  stopPolling() {
    for (const stop of this.pollStops.values()) {
      stop();
    }
    this.pollStops.clear();
  }

  renderFatalSurveyError(error) {
    const message = this.describeError(error);
    this.showMessage(this.overviewMessage, message, "error");
    this.filesContent.innerHTML = this.renderStatePanel("Survey unavailable", message, "error");
    this.approvalHistoryContent.innerHTML = this.renderStatePanel("Survey unavailable", message, "error");
    this.measurementsContent.innerHTML = this.renderStatePanel("Survey unavailable", message, "error");
    this.auditContent.innerHTML = this.renderStatePanel("Survey unavailable", message, "error");
    this.mapViewer?.showUnavailableState("Survey unavailable", message, "error");
    this.modelViewer?.showUnavailableState("Survey unavailable", message, "error");
    this.uploadForm.hidden = true;
    this.syncUploadAssetField();
    this.measurementForm.hidden = true;
    this.auditFilterForm.hidden = true;
    this.rejectionForm.hidden = true;
    this.rejectFormVisible = false;
    this.approvalActions.innerHTML = "";
    this.stopPolling();
  }

  showMessage(element, message, tone = "info") {
    if (!element) {
      return;
    }
    element.textContent = message;
    element.hidden = false;
    element.classList.toggle("is-error", tone === "error");
  }

  clearMessage(element) {
    if (!element) {
      return;
    }
    element.textContent = "";
    element.hidden = true;
    element.classList.remove("is-error");
  }

  showRejectionError(message) {
    this.rejectionError.textContent = message;
    this.rejectionError.hidden = false;
  }

  hideRejectionError() {
    this.rejectionError.textContent = "";
    this.rejectionError.hidden = true;
  }

  describeError(error) {
    if (error instanceof ApiError) {
      return this.extractErrorMessage(error.body);
    }
    if (error instanceof Error && error.message) {
      return error.message;
    }
    return "The request could not be completed.";
  }

  extractErrorMessage(body) {
    if (typeof body === "string" && body.trim()) {
      return body;
    }
    if (!body || typeof body !== "object") {
      return "The request could not be completed.";
    }
    if (typeof body.detail === "string" && body.detail.trim()) {
      return body.detail;
    }

    const messages = [];
    for (const value of Object.values(body)) {
      if (Array.isArray(value)) {
        messages.push(...value.filter(Boolean).map(String));
      } else if (value && typeof value === "object") {
        for (const nested of Object.values(value)) {
          if (Array.isArray(nested)) {
            messages.push(...nested.filter(Boolean).map(String));
          } else if (nested) {
            messages.push(String(nested));
          }
        }
      } else if (value) {
        messages.push(String(value));
      }
    }

    return messages[0] || "The request could not be completed.";
  }

  renderStatePanel(title, description, tone = "empty") {
    const toneClass = tone === "error" ? " state-panel--error" : "";
    return `
      <section class="state-panel${toneClass}">
        <div>
          <h2>${this.escapeHtml(title)}</h2>
          <p>${this.escapeHtml(description)}</p>
        </div>
      </section>
    `;
  }

  renderBadgeLabel(value, kind) {
    if (!value) {
      return '<span class="survey-muted-value">Unavailable</span>';
    }
    return this.renderBadge(value, kind);
  }

  renderBadge(value, kind) {
    const tone = this.badgeTone(value, kind);
    return `<span class="status-badge status-badge--${tone}">${this.escapeHtml(this.prettyEnum(value))}</span>`;
  }

  badgeTone(value, kind) {
    const normalized = String(value || "").toUpperCase();
    if (["APPROVED", "READY", "COMPLETED", "ACTIVE"].includes(normalized)) {
      return "success";
    }
    if (["FAILED", "REJECTED", "ARCHIVED"].includes(normalized)) {
      return normalized === "ARCHIVED" && kind === "survey" ? "neutral" : "danger";
    }
    if (["PENDING_APPROVAL", "UPLOADING", "PROCESSING", "QUEUED", "RUNNING"].includes(normalized)) {
      return "warning";
    }
    return "neutral";
  }

  prettyEnum(value) {
    if (value === "TWO_D") {
      return "2D";
    }
    if (value === "THREE_D") {
      return "3D";
    }
    return String(value)
      .replaceAll("_", " ")
      .toLowerCase()
      .replace(/\b\w/g, (character) => character.toUpperCase());
  }

  referenceLabel(prefix, id) {
    if (!id) {
      return '<span class="survey-muted-value">Not available</span>';
    }
    return `${this.escapeHtml(prefix)} #${this.escapeHtml(String(id))}`;
  }

  userLabel(id) {
    if (!id) {
      return '<span class="survey-muted-value">Not available</span>';
    }
    if (this.auth?.user?.id === id) {
      return this.escapeHtml(this.auth.user.email);
    }
    return `User #${this.escapeHtml(String(id))}`;
  }

  presentValue(value) {
    if (value === null || value === undefined || value === "") {
      return '<span class="survey-muted-value">Not provided</span>';
    }
    return this.escapeHtml(String(value));
  }

  formatDate(value) {
    if (!value) {
      return '<span class="survey-muted-value">Not provided</span>';
    }
    const date = /^\d{4}-\d{2}-\d{2}$/.test(value) ? new Date(`${value}T00:00:00Z`) : new Date(value);
    if (Number.isNaN(date.getTime())) {
      return this.escapeHtml(String(value));
    }
    return this.escapeHtml(
      new Intl.DateTimeFormat(undefined, {
        year: "numeric",
        month: "short",
        day: "numeric",
        timeZone: "UTC",
      }).format(date),
    );
  }

  formatDateTime(value) {
    if (!value) {
      return '<span class="survey-muted-value">Not available</span>';
    }
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) {
      return this.escapeHtml(String(value));
    }
    return this.escapeHtml(
      new Intl.DateTimeFormat(undefined, {
        year: "numeric",
        month: "short",
        day: "numeric",
        hour: "numeric",
        minute: "2-digit",
      }).format(date),
    );
  }

  formatBytes(value) {
    const bytes = Number(value);
    if (!Number.isFinite(bytes) || bytes < 0) {
      return "Unknown size";
    }
    if (bytes === 0) {
      return "0 B";
    }
    const units = ["B", "KB", "MB", "GB", "TB"];
    const exponent = Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), units.length - 1);
    const amount = bytes / 1024 ** exponent;
    const digits = exponent === 0 ? 0 : amount >= 10 ? 1 : 2;
    return `${amount.toFixed(digits)} ${units[exponent]}`;
  }

  formatPercent(value) {
    const number = Number(value);
    return Number.isFinite(number) ? `${number}%` : "0%";
  }

  formatMeasurementValue(value, unit, escape = true) {
    const numeric = Number(value);
    const formatted = Number.isFinite(numeric)
      ? new Intl.NumberFormat(undefined, { maximumFractionDigits: 2 }).format(numeric)
      : String(value);
    const label = `${formatted} ${unit}`;
    return escape ? this.escapeHtml(label) : label;
  }

  renderAuditDetails(details) {
    if (!details || typeof details !== "object" || Object.keys(details).length === 0) {
      return '<span class="survey-muted-value">No additional details</span>';
    }
    return Object.entries(details)
      .map(
        ([key, value]) =>
          `<div><strong>${this.escapeHtml(this.prettyEnum(key))}:</strong> ${this.escapeHtml(
            typeof value === "object" ? JSON.stringify(value) : String(value),
          )}</div>`,
      )
      .join("");
  }

  escapeHtml(value) {
    return String(value)
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#39;");
  }
}
