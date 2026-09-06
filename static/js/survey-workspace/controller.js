import {
  applyShellUser,
  createMapViewer,
  createModelViewer,
  isUnauthorized,
  loadAuthState,
  api,
} from "./dependencies.js";
import { dataMethods } from "./data.js";
import { renderMethods } from "./rendering.js";
import { measurementMethods } from "./measurements.js";
import { uploadMethods } from "./uploads.js";
import { actionMethods } from "./actions.js";
import { utilityMethods } from "./utilities.js";

export class SurveyWorkspaceController {
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
}

for (const methods of [
  dataMethods,
  renderMethods,
  measurementMethods,
  uploadMethods,
  actionMethods,
  utilityMethods,
]) {
  Object.defineProperties(
    SurveyWorkspaceController.prototype,
    Object.getOwnPropertyDescriptors(methods),
  );
}
