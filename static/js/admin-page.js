import { api, ApiError } from "./api.js";
import { applyShellUser, isUnauthorized, loadAuthState, prettyRole } from "./modules/auth-shell.js";

const DEFAULT_LIMIT = 20;

export function initialiseAdminPage() {
  const root = document.querySelector("[data-admin-page]");
  if (!root) {
    return;
  }
  new AdminPageController(root).initialise();
}

class AdminPageController {
  constructor(root) {
    this.root = root;
    this.messageElement = root.querySelector("[data-admin-message]");
    this.userSummary = root.querySelector("[data-admin-user-summary]");
    this.userTableRegion = root.querySelector("[data-admin-users-region]");
    this.projectSummary = root.querySelector("[data-admin-project-summary]");
    this.projectTableRegion = root.querySelector("[data-admin-projects-region]");
    this.createForm = root.querySelector("[data-admin-create-form]");
    this.createSubmit = root.querySelector("[data-admin-create-submit]");
    this.auth = null;
  }

  async initialise() {
    this.bindEvents();
    try {
      this.auth = await loadAuthState({ force: true });
      applyShellUser(this.auth);
      await this.load();
    } catch (error) {
      if (isUnauthorized(error)) {
        window.location.assign("/login");
        return;
      }
      this.showMessage(this.describeError(error), "error");
    }
  }

  bindEvents() {
    if (this.bound) {
      return;
    }
    this.bound = true;
    this.createForm?.addEventListener("submit", (event) => this.handleCreate(event));
    this.userTableRegion?.addEventListener("click", (event) => this.handleUserUpdate(event));
  }

  async load() {
    this.clearMessage();
    const [usersPayload, projectsPayload] = await Promise.all([
      api.get(`/api/v1/users?limit=${DEFAULT_LIMIT}&offset=0`),
      api.get(`/api/v1/projects?limit=${DEFAULT_LIMIT}&offset=0`),
    ]);
    this.renderUsers(usersPayload);
    this.renderProjects(projectsPayload);
  }

  renderUsers(payload) {
    const users = Array.isArray(payload?.results) ? payload.results : [];
    this.userSummary.textContent = `${payload?.count ?? users.length} local user${(payload?.count ?? users.length) === 1 ? "" : "s"}`;

    if (users.length === 0) {
      this.userTableRegion.innerHTML = this.renderStatePanel(
        "No local users",
        "Create local user records to map Hitech Auth subjects into project ownership and access rules.",
      );
      return;
    }

    const rows = users
      .map(
        (user) => `
          <tr>
            <td>${escapeHtml(String(user.id))}</td>
            <td>${escapeHtml(user.external_id)}</td>
            <td>
              <input class="input" type="email" data-user-field="email" value="${escapeHtml(user.email)}" required>
            </td>
            <td>
                <select class="select" data-user-field="role">
                  ${renderRoleOptions(user.role)}
                </select>
            </td>
            <td>
                <label class="checkbox-inline">
                  <input type="checkbox" data-user-field="is_active" ${user.is_active ? "checked" : ""}>
                  Active
                </label>
            </td>
            <td>
                <button class="button" type="button" data-user-update-button data-user-id="${user.id}">Save</button>
            </td>
          </tr>
        `,
      )
      .join("");

    this.userTableRegion.innerHTML = `
      <div class="table-wrap">
        <table class="data-table">
          <thead>
            <tr>
              <th scope="col">ID</th>
              <th scope="col">External ID</th>
              <th scope="col">Email</th>
              <th scope="col">Role</th>
              <th scope="col">Status</th>
              <th scope="col">Update</th>
            </tr>
          </thead>
          <tbody>${rows}</tbody>
        </table>
      </div>
    `;
  }

  renderProjects(payload) {
    const projects = Array.isArray(payload?.results) ? payload.results : [];
    this.projectSummary.textContent = `${payload?.count ?? projects.length} project${(payload?.count ?? projects.length) === 1 ? "" : "s"} visible across the service`;

    if (projects.length === 0) {
      this.projectTableRegion.innerHTML = this.renderStatePanel(
        "No projects",
        "Cross-project oversight will appear here once projects exist.",
      );
      return;
    }

    const rows = projects
      .map(
        (project) => `
          <tr>
            <td><a class="text-action" href="/projects/${project.id}">${escapeHtml(project.name)}</a></td>
            <td>${escapeHtml(project.location || "Not provided")}</td>
            <td>${renderBadge(project.status)}</td>
            <td>${escapeHtml(project.project_manager_id ? `User #${project.project_manager_id}` : "Not assigned")}</td>
          </tr>
        `,
      )
      .join("");

    this.projectTableRegion.innerHTML = `
      <div class="table-wrap">
        <table class="data-table">
          <thead>
            <tr>
              <th scope="col">Project</th>
              <th scope="col">Location</th>
              <th scope="col">Status</th>
              <th scope="col">Project manager</th>
            </tr>
          </thead>
          <tbody>${rows}</tbody>
        </table>
      </div>
    `;
  }

  async handleCreate(event) {
    event.preventDefault();
    this.clearMessage();
    const formData = new FormData(this.createForm);
    const payload = {
      email: formData.get("email")?.toString().trim() || "",
      external_id: formData.get("external_id")?.toString().trim() || "",
      role: formData.get("role")?.toString() || "VIEWER",
      is_active: formData.get("is_active") === "on",
    };

    this.createSubmit.disabled = true;
    try {
      await api.post("/api/v1/users", payload);
      this.createForm.reset();
      this.createForm.elements.role.value = "VIEWER";
      this.createForm.elements.is_active.checked = true;
      this.showMessage("Local user created.");
      await this.load();
    } catch (error) {
      if (isUnauthorized(error)) {
        window.location.assign("/login");
        return;
      }
      this.showMessage(this.describeError(error), "error");
    } finally {
      this.createSubmit.disabled = false;
    }
  }

  async handleUserUpdate(event) {
    const button = event.target.closest("[data-user-update-button]");
    if (!button) {
      return;
    }

    this.clearMessage();
    const row = button.closest("tr");
    const payload = {
      email: row.querySelector('[data-user-field="email"]')?.value?.trim() || "",
      role: row.querySelector('[data-user-field="role"]')?.value || "VIEWER",
      is_active: Boolean(row.querySelector('[data-user-field="is_active"]')?.checked),
    };
    button.disabled = true;

    try {
      await api.patch(`/api/v1/users/${button.dataset.userId}`, payload);
      this.showMessage(`Updated user #${button.dataset.userId}.`);
      await this.load();
    } catch (error) {
      if (isUnauthorized(error)) {
        window.location.assign("/login");
        return;
      }
      this.showMessage(this.describeError(error), "error");
    } finally {
      button.disabled = false;
    }
  }

  showMessage(message, tone = "info") {
    if (!this.messageElement) {
      return;
    }
    this.messageElement.textContent = message;
    this.messageElement.hidden = false;
    this.messageElement.classList.toggle("is-error", tone === "error");
  }

  clearMessage() {
    if (!this.messageElement) {
      return;
    }
    this.messageElement.textContent = "";
    this.messageElement.hidden = true;
    this.messageElement.classList.remove("is-error");
  }

  describeError(error) {
    if (error instanceof ApiError) {
      const body = error.body;
      if (typeof body?.detail === "string" && body.detail) {
        return body.detail;
      }
      if (body && typeof body === "object") {
        for (const value of Object.values(body)) {
          if (Array.isArray(value) && value.length > 0) {
            return String(value[0]);
          }
        }
      }
    }
    return error instanceof Error && error.message ? error.message : "The request could not be completed.";
  }

  renderStatePanel(title, description) {
    return `
      <section class="state-panel">
        <div>
          <h2>${escapeHtml(title)}</h2>
          <p>${escapeHtml(description)}</p>
        </div>
      </section>
    `;
  }
}

function renderRoleOptions(currentRole) {
  return ["ADMINISTRATOR", "PROJECT_MANAGER", "SURVEY_ENGINEER", "VIEWER"]
    .map(
      (role) =>
        `<option value="${role}" ${role === currentRole ? "selected" : ""}>${escapeHtml(prettyRole(role))}</option>`,
    )
    .join("");
}

function renderBadge(value) {
  const normalized = String(value || "").toUpperCase();
  const tone = normalized === "ACTIVE" ? "success" : normalized === "ARCHIVED" ? "neutral" : "warning";
  return `<span class="status-badge status-badge--${tone}">${escapeHtml(prettyRole(normalized))}</span>`;
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}
