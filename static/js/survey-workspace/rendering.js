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

export const renderMethods = {
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
  },

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
  },

  renderUploadForm() {
    const role = this.auth?.user?.role;
    const canUpload = ["ADMINISTRATOR", "PROJECT_MANAGER", "SURVEY_ENGINEER"].includes(role);
    this.uploadForm.hidden = !canUpload;
    if (this.primaryFileInput) {
      this.primaryFileInput.setAttribute("accept", PRIMARY_UPLOAD_ACCEPT);
    }
    this.syncUploadAssetField();
  },

  renderApproval() {
    this.clearMessage(this.approvalMessage);
    this.renderApprovalFields();
    this.renderApprovalGuidance();
    this.renderApprovalActions();
    this.renderApprovalHistory();
  },

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
  },

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
  },

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
  },

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
  },

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
  },

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
};
