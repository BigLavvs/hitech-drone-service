/** Presentation-only helper for the upload workflow to be implemented later. */
export function updateUploadProgress(element, percent, label = "Uploading") {
  if (!element) return;
  const value = Math.max(0, Math.min(100, Number(percent) || 0));
  element.style.setProperty("--upload-progress", `${value}%`);
  element.setAttribute("aria-valuenow", String(value));
  element.setAttribute("aria-label", `${label}: ${value}%`);
  const labelElement = element.querySelector("[data-upload-label]");
  if (labelElement) labelElement.textContent = `${label} ${value}%`;
}
