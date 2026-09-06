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

export const utilityMethods = {
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
  },

  showMessage(element, message, tone = "info") {
    if (!element) {
      return;
    }
    element.textContent = message;
    element.hidden = false;
    element.classList.toggle("is-error", tone === "error");
  },

  clearMessage(element) {
    if (!element) {
      return;
    }
    element.textContent = "";
    element.hidden = true;
    element.classList.remove("is-error");
  },

  showRejectionError(message) {
    this.rejectionError.textContent = message;
    this.rejectionError.hidden = false;
  },

  hideRejectionError() {
    this.rejectionError.textContent = "";
    this.rejectionError.hidden = true;
  },

  describeError(error) {
    if (error instanceof ApiError) {
      return this.extractErrorMessage(error.body);
    }
    if (error instanceof Error && error.message) {
      return error.message;
    }
    return "The request could not be completed.";
  },

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
  },

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
  },

  renderBadgeLabel(value, kind) {
    if (!value) {
      return '<span class="survey-muted-value">Unavailable</span>';
    }
    return this.renderBadge(value, kind);
  },

  renderBadge(value, kind) {
    const tone = this.badgeTone(value, kind);
    return `<span class="status-badge status-badge--${tone}">${this.escapeHtml(this.prettyEnum(value))}</span>`;
  },

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
  },

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
  },

  referenceLabel(prefix, id) {
    if (!id) {
      return '<span class="survey-muted-value">Not available</span>';
    }
    return `${this.escapeHtml(prefix)} #${this.escapeHtml(String(id))}`;
  },

  userLabel(id) {
    if (!id) {
      return '<span class="survey-muted-value">Not available</span>';
    }
    if (this.auth?.user?.id === id) {
      return this.escapeHtml(this.auth.user.email);
    }
    return `User #${this.escapeHtml(String(id))}`;
  },

  presentValue(value) {
    if (value === null || value === undefined || value === "") {
      return '<span class="survey-muted-value">Not provided</span>';
    }
    return this.escapeHtml(String(value));
  },

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
  },

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
  },

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
  },

  formatPercent(value) {
    const number = Number(value);
    return Number.isFinite(number) ? `${number}%` : "0%";
  },

  formatMeasurementValue(value, unit, escape = true) {
    const numeric = Number(value);
    const formatted = Number.isFinite(numeric)
      ? new Intl.NumberFormat(undefined, { maximumFractionDigits: 2 }).format(numeric)
      : String(value);
    const label = `${formatted} ${unit}`;
    return escape ? this.escapeHtml(label) : label;
  },

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
  },

  escapeHtml(value) {
    return String(value)
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#39;");
  }
};
