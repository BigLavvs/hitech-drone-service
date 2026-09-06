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

export const actionMethods = {
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
  },

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
  },

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
  },

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
  },

  async handleAuditReset() {
    this.auditFilters = {};
    this.auditFilterForm.reset();
    await this.refreshAudit();
  },

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
  },

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
  },

  stopPolling() {
    for (const stop of this.pollStops.values()) {
      stop();
    }
    this.pollStops.clear();
  }
};
