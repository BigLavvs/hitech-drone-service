import { prettyRole } from "../modules/auth-shell.js";
import { DEFAULT_LIMIT } from "./constants.js";


export function renderPagination(payload) {
  const hasPrevious = Boolean(payload?.previous);
  const hasNext = Boolean(payload?.next);
  const count = Number(payload?.count || 0);
  if (!hasPrevious && !hasNext && count <= DEFAULT_LIMIT) {
    return "";
  }
  return `
    <div class="pagination-bar">
      <button class="button" type="button" data-page-direction="previous" ${hasPrevious ? "" : "disabled"}>Previous</button>
      <span>${count} total record${count === 1 ? "" : "s"}</span>
      <button class="button" type="button" data-page-direction="next" ${hasNext ? "" : "disabled"}>Next</button>
    </div>
  `;
}

export function renderPaginationInto(element, payload, onPage) {
  const hasPrevious = Boolean(payload?.previous);
  const hasNext = Boolean(payload?.next);
  const count = Number(payload?.count || 0);
  if (!hasPrevious && !hasNext && count <= DEFAULT_LIMIT) {
    element.hidden = true;
    element.innerHTML = "";
    return;
  }

  element.hidden = false;
  element.innerHTML = `
    <button class="button" type="button" data-page-direction="previous" ${hasPrevious ? "" : "disabled"}>Previous</button>
    <span>${count} total record${count === 1 ? "" : "s"}</span>
    <button class="button" type="button" data-page-direction="next" ${hasNext ? "" : "disabled"}>Next</button>
  `;
  [...element.querySelectorAll("[data-page-direction]")].forEach((button) => {
    button.addEventListener("click", () => onPage(button.dataset.pageDirection), { once: true });
  });
}

export function setField(container, fieldName, value, allowHtml = false) {
  const element = container.querySelector(`[data-${container.hasAttribute("data-project-overview-list") ? "project" : "site"}-field="${fieldName}"]`);
  if (!element) {
    return;
  }
  if (allowHtml) {
    element.innerHTML = value;
  } else {
    element.textContent = value;
  }
}

export function renderStatePanel(title, description, tone = "empty") {
  const toneClass = tone === "error" ? " state-panel--error" : "";
  return `
    <section class="state-panel${toneClass}">
      <div>
        <h2>${escapeHtml(title)}</h2>
        <p>${escapeHtml(description)}</p>
      </div>
    </section>
  `;
}

export function renderBadge(value) {
  const normalized = String(value || "").toUpperCase();
  let tone = "neutral";
  if (["ACTIVE", "READY", "APPROVED", "COMPLETED"].includes(normalized)) {
    tone = "success";
  } else if (["ARCHIVED", "REJECTED", "FAILED"].includes(normalized)) {
    tone = normalized === "ARCHIVED" ? "neutral" : "danger";
  } else if (["PROCESSING", "UPLOADING", "PENDING_APPROVAL", "QUEUED", "RUNNING"].includes(normalized)) {
    tone = "warning";
  }
  return `<span class="status-badge status-badge--${tone}">${escapeHtml(prettyRole(normalized))}</span>`;
}

export function presentValue(value, allowHtml = false) {
  if (value === null || value === undefined || value === "") {
    return allowHtml ? '<span class="survey-muted-value">Not provided</span>' : "Not provided";
  }
  return allowHtml ? escapeHtml(String(value)) : String(value);
}

export function formatCoordinates(coordinates) {
  if (!coordinates || typeof coordinates !== "object") {
    return "Not provided";
  }
  return `${Number(coordinates.lat).toFixed(6)}, ${Number(coordinates.lng).toFixed(6)}`;
}

export function formatDate(value) {
  if (!value) {
    return "Not provided";
  }
  const date = new Date(`${value}T00:00:00Z`);
  if (Number.isNaN(date.getTime())) {
    return String(value);
  }
  return new Intl.DateTimeFormat(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
    timeZone: "UTC",
  }).format(date);
}

export function formatDateTime(value) {
  if (!value) {
    return "Not available";
  }
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return String(value);
  }
  return new Intl.DateTimeFormat(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  }).format(date);
}

export function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

export function extractErrorMessage(body) {
  if (typeof body === "string" && body.trim()) {
    return body;
  }
  if (!body || typeof body !== "object") {
    return "The request could not be completed.";
  }
  if (typeof body.detail === "string" && body.detail.trim()) {
    return body.detail;
  }

  for (const value of Object.values(body)) {
    if (Array.isArray(value) && value.length > 0) {
      return String(value[0]);
    }
    if (value && typeof value === "object") {
      for (const nested of Object.values(value)) {
        if (Array.isArray(nested) && nested.length > 0) {
          return String(nested[0]);
        }
      }
    }
  }
  return "The request could not be completed.";
}

export function handleRowNavigation(event) {
  const ignored = event.target.closest("a, button, input, textarea, select, label");
  if (ignored) {
    return;
  }
  const row = event.target.closest("[data-href]");
  if (row) {
    window.location.assign(row.dataset.href);
  }
}

export function handleRowKeyboard(event) {
  const row = event.target.closest("[data-href]");
  if (!row) {
    return;
  }
  if (event.key === "Enter" || event.key === " ") {
    event.preventDefault();
    window.location.assign(row.dataset.href);
  }
}

export function optionalText(value) {
  const text = value?.toString().trim() || "";
  return text || null;
}

export function numericValue(value) {
  return Number(value);
}

export function numberFieldValue(value) {
  const parsed = Number(value);
  return Number.isFinite(parsed) && parsed > 0 ? parsed : null;
}
