/**
 * The only module that should make HTTP requests. Feature modules call these
 * helpers once their corresponding DRF endpoints exist.
 */

export class ApiError extends Error {
  constructor(message, { status, body } = {}) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.body = body;
  }
}

function csrfToken() {
  const match = document.cookie.match(/(?:^|; )csrftoken=([^;]+)/);
  return match ? decodeURIComponent(match[1]) : null;
}

export async function request(path, options = {}) {
  const { method = "GET", body, headers = {}, signal } = options;
  const isFormData = body instanceof FormData;
  const requestHeaders = { Accept: "application/json", ...headers };
  const csrf = csrfToken();

  if (body && !isFormData && !requestHeaders["Content-Type"]) {
    requestHeaders["Content-Type"] = "application/json";
  }
  if (csrf && !["GET", "HEAD", "OPTIONS"].includes(method.toUpperCase())) {
    requestHeaders["X-CSRFToken"] = csrf;
  }

  const response = await fetch(path, {
    method,
    headers: requestHeaders,
    body: body && !isFormData && typeof body !== "string" ? JSON.stringify(body) : body,
    credentials: "same-origin",
    signal,
  });
  const contentType = response.headers.get("content-type") || "";
  const payload = contentType.includes("application/json") ? await response.json() : await response.text();

  if (!response.ok) {
    throw new ApiError("The request could not be completed.", { status: response.status, body: payload });
  }
  return payload;
}

export const api = {
  get: (path, options) => request(path, { ...options, method: "GET" }),
  post: (path, body, options) => request(path, { ...options, method: "POST", body }),
  patch: (path, body, options) => request(path, { ...options, method: "PATCH", body }),
  put: (path, body, options) => request(path, { ...options, method: "PUT", body }),
  delete: (path, options) => request(path, { ...options, method: "DELETE" }),
};
