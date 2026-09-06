import { api } from "../api.js";
import { isUnauthorized } from "../modules/auth-shell.js";
import { BasePageController } from "./base.js";
import { DEFAULT_LIMIT } from "./constants.js";
import {
  escapeHtml,
  formatDateTime,
  handleRowKeyboard,
  handleRowNavigation,
  numberFieldValue,
  optionalText,
  presentValue,
  renderBadge,
  renderPagination,
  renderStatePanel,
} from "./helpers.js";

export class ProjectsPageController extends BasePageController {
  constructor(root) {
    super(root);
    this.tableRegion = root.querySelector("[data-projects-table-region]");
    this.summaryElement = root.querySelector("[data-projects-summary]");
    this.createCard = root.querySelector("[data-project-create-card]");
    this.createForm = root.querySelector("[data-project-create-form]");
    this.managerField = root.querySelector("[data-project-manager-field]");
    this.createSubmit = root.querySelector("[data-project-create-submit]");
    this.offset = 0;
    this.limit = DEFAULT_LIMIT;
  }

  async load() {
    this.clearMessage();
    this.bindEvents();
    this.toggleCreateForm();
    const payload = await api.get(`/api/v1/projects?limit=${this.limit}&offset=${this.offset}`);
    this.renderTable(payload);
  }

  bindEvents() {
    if (this.bound) {
      return;
    }
    this.bound = true;
    this.createForm?.addEventListener("submit", (event) => this.handleCreate(event));
    this.tableRegion?.addEventListener("click", (event) => handleRowNavigation(event));
    this.tableRegion?.addEventListener("keydown", (event) => handleRowKeyboard(event));
  }

  toggleCreateForm() {
    const role = this.auth?.user?.role;
    const canCreate = role === "ADMINISTRATOR" || role === "PROJECT_MANAGER";
    this.createCard.hidden = !canCreate;
    this.managerField.hidden = role !== "ADMINISTRATOR";
  }

  renderTable(payload) {
    const results = Array.isArray(payload?.results) ? payload.results : [];
    this.summaryElement.textContent = `${payload?.count ?? results.length} accessible project${(payload?.count ?? results.length) === 1 ? "" : "s"}`;

    if (results.length === 0) {
      this.tableRegion.innerHTML = renderStatePanel(
        "No accessible projects",
        "No projects are currently visible for this authenticated user.",
      );
      return;
    }

    const rows = results
      .map(
        (project) => `
          <tr class="clickable-row" tabindex="0" data-href="/projects/${project.id}">
            <td>
              <strong>${escapeHtml(project.name)}</strong>
              <div class="table-subcopy">Project #${escapeHtml(String(project.id))}</div>
            </td>
            <td>${presentValue(project.location)}</td>
            <td>${renderBadge(project.status)}</td>
            <td>${presentValue(project.project_manager_id ? `User #${project.project_manager_id}` : null)}</td>
            <td>${formatDateTime(project.updated_at)}</td>
            <td><a class="text-action" href="/projects/${project.id}">Open</a></td>
          </tr>
        `,
      )
      .join("");

    this.tableRegion.innerHTML = `
      <div class="table-wrap">
        <table class="data-table">
          <caption class="sr-only">Projects available to the signed-in user</caption>
          <thead>
            <tr>
              <th scope="col">Project name</th>
              <th scope="col">Location</th>
              <th scope="col">Status</th>
              <th scope="col">Project manager</th>
              <th scope="col">Updated</th>
              <th scope="col">Open</th>
            </tr>
          </thead>
          <tbody>${rows}</tbody>
        </table>
      </div>
      ${renderPagination(payload)}
    `;
    const onPage = this.changePage(payload);
    [...this.tableRegion.querySelectorAll("[data-page-direction]")].forEach((button) => {
      button.addEventListener("click", () => onPage(button.dataset.pageDirection));
    });
  }

  changePage(payload) {
    return async (direction) => {
      const nextOffset = direction === "next" ? this.offset + this.limit : Math.max(0, this.offset - this.limit);
      if ((direction === "next" && !payload.next) || (direction === "previous" && !payload.previous)) {
        return;
      }
      this.offset = nextOffset;
      await this.refresh();
    };
  }

  async handleCreate(event) {
    event.preventDefault();
    this.clearMessage();
    const formData = new FormData(this.createForm);
    const payload = {
      name: formData.get("name")?.toString().trim() || "",
      location: optionalText(formData.get("location")),
      description: optionalText(formData.get("description")),
    };
    if (this.auth?.user?.role === "ADMINISTRATOR") {
      payload.project_manager_id = numberFieldValue(formData.get("project_manager_id"));
    }

    this.createSubmit.disabled = true;
    try {
      await api.post("/api/v1/projects", payload);
      this.createForm.reset();
      this.showMessage("Project created successfully.");
      this.offset = 0;
      await this.refresh();
    } catch (error) {
      if (isUnauthorized(error)) {
        this.renderUnauthenticated();
        return;
      }
      this.showMessage(this.describeError(error), "error");
    } finally {
      this.createSubmit.disabled = false;
    }
  }

  renderUnauthenticated() {
    super.renderUnauthenticated();
    this.summaryElement.textContent = "Session required";
    this.createCard.hidden = true;
    this.tableRegion.innerHTML = renderStatePanel(
      "Session required",
      "Sign in again to load projects and create a new project.",
      "error",
    );
  }

  renderLoadError(error) {
    this.summaryElement.textContent = "Projects unavailable";
    this.tableRegion.innerHTML = renderStatePanel("Projects unavailable", this.describeError(error), "error");
  }
}
