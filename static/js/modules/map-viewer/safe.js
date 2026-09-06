export function createSafePopupContent({ name, description }) {
  const container = document.createElement("div");

  if (name) {
    const heading = document.createElement("strong");
    heading.textContent = name;
    container.append(heading);
  }

  if (description) {
    const body = document.createElement("p");
    body.textContent = description;
    if (name) {
      body.style.margin = "0.35rem 0 0";
    }
    container.append(body);
  }

  return container;
}

export function prettyEnum(value) {
  return String(value || "")
    .replaceAll("_", " ")
    .toLowerCase()
    .replace(/\b\w/g, (character) => character.toUpperCase());
}

export function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

export function escapeAttribute(value) {
  return escapeHtml(value);
}
