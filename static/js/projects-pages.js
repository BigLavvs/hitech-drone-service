import { api, ApiError } from "./api.js";
import { applyShellUser, isUnauthorized, loadAuthState, prettyRole } from "./modules/auth-shell.js";

const DEFAULT_LIMIT = 20;

export function initialiseProjectPages() {
  const projectsRoot = document.querySelector("[data-projects-page]");
  if (projectsRoot) {
    new ProjectsPageController(projectsRoot).initialise();
  }

  const projectDetailRoot = document.querySelector("[data-project-detail-page]");
  if (projectDetailRoot) {
    new ProjectDetailPageController(projectDetailRoot).initialise();
  }

  const siteDetailRoot = document.querySelector("[data-site-detail-page]");
  if (siteDetailRoot) {
    new SiteDetailPageController(siteDetailRoot).initialise();
  }
}

class BasePageController {
  constructor(root) {
    this.root = root;
    this.auth = null;
    this.messageElement = root.querySelector("[data-page-message]");
  }

  async initialise() {
    try {
      this.auth = await loadAuthState();
      applyShellUser(this.auth);
      await this.load();
    } catch (error) {
      applyShellUser(null);
      if (isUnauthorized(error)) {
        this.renderUnauthenticated();
        return;
      }
      this.showMessage(this.describeError(error), "error");
      this.renderLoadError(error);
    }
  }

  async refresh() {
    try {
      this.auth = await loadAuthState({ force: true });
      applyShellUser(this.auth);
      await this.load();
    } catch (error) {
      applyShellUser(null);
      if (isUnauthorized(error)) {
        this.renderUnauthenticated();
        return;
      }
      this.showMessage(this.describeError(error), "error");
      this.renderLoadError(error);
    }
  }

  renderUnauthenticated() {
    this.showMessage("Your Hitech sign-in session is missing or expired. Sign in again to continue.", "error");
  }

  renderLoadError() {}

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
      return extractErrorMessage(error.body);
    }
    if (error instanceof Error && error.message) {
      return error.message;
    }
    return "The request could not be completed.";
  }
}

class ProjectsPageController extends BasePageController {
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

class ProjectDetailPageController extends BasePageController {
  constructor(root) {
    super(root);
    this.projectId = root.dataset.projectId;
    this.overviewList = root.querySelector("[data-project-overview-list]");
    this.overviewState = root.querySelector("[data-project-overview-state]");
    this.siteTableRegion = root.querySelector("[data-site-table-region]");
    this.editCard = root.querySelector("[data-project-edit-card]");
    this.editForm = root.querySelector("[data-project-edit-form]");
    this.editSubmit = root.querySelector("[data-project-edit-submit]");
    this.archiveButton = root.querySelector("[data-project-archive-button]");
    this.editManagerField = root.querySelector("[data-project-edit-manager-field]");
    this.siteCreateCard = root.querySelector("[data-site-create-card]");
    this.siteCreateForm = root.querySelector("[data-site-create-form]");
    this.siteCreateSubmit = root.querySelector("[data-site-create-submit]");
    this.paginationElement = root.querySelector("[data-sites-pagination]");
    this.offset = 0;
    this.limit = DEFAULT_LIMIT;
    this.project = null;
  }

  async load() {
    this.clearMessage();
    this.bindEvents();
    const [projectPayload, sitesPayload] = await Promise.all([
      api.get(`/api/v1/projects/${this.projectId}`),
      api.get(`/api/v1/projects/${this.projectId}/sites?limit=${this.limit}&offset=${this.offset}`),
    ]);
    this.project = projectPayload;
    this.renderProject();
    this.renderSites(sitesPayload);
    this.toggleForms();
  }

  bindEvents() {
    if (this.bound) {
      return;
    }
    this.bound = true;
    this.editForm?.addEventListener("submit", (event) => this.handleProjectEdit(event));
    this.archiveButton?.addEventListener("click", () => this.handleProjectArchive());
    this.siteCreateForm?.addEventListener("submit", (event) => this.handleSiteCreate(event));
    this.siteTableRegion?.addEventListener("click", (event) => handleRowNavigation(event));
    this.siteTableRegion?.addEventListener("keydown", (event) => handleRowKeyboard(event));
  }

  toggleForms() {
    const role = this.auth?.user?.role;
    const canManage = role === "ADMINISTRATOR" || role === "PROJECT_MANAGER";
    this.editCard.hidden = !canManage;
    this.siteCreateCard.hidden = !canManage;
    this.editManagerField.hidden = role !== "ADMINISTRATOR";
  }

  renderProject() {
    this.overviewList.hidden = false;
    this.overviewState.hidden = true;
    setField(this.overviewList, "name", this.project.name);
    setField(this.overviewList, "location", presentValue(this.project.location, true), true);
    setField(this.overviewList, "description", presentValue(this.project.description, true), true);
    setField(this.overviewList, "status", renderBadge(this.project.status), true);
    setField(
      this.overviewList,
      "project_manager_id",
      this.project.project_manager_id ? `User #${this.project.project_manager_id}` : '<span class="survey-muted-value">Not assigned</span>',
      true,
    );

    if (this.editForm) {
      this.editForm.elements.name.value = this.project.name || "";
      this.editForm.elements.location.value = this.project.location || "";
      this.editForm.elements.description.value = this.project.description || "";
      if (this.editForm.elements.project_manager_id) {
        this.editForm.elements.project_manager_id.value = this.project.project_manager_id || "";
      }
    }
  }

  renderSites(payload) {
    const results = Array.isArray(payload?.results) ? payload.results : [];
    if (results.length === 0) {
      this.siteTableRegion.innerHTML = renderStatePanel(
        "No sites yet",
        "This project does not currently have any sites.",
      );
    } else {
      const rows = results
        .map(
          (site) => `
            <tr class="clickable-row" tabindex="0" data-href="/projects/${this.projectId}/sites/${site.id}">
              <td>
                <strong>${escapeHtml(site.name)}</strong>
                <div class="table-subcopy">Site #${escapeHtml(String(site.id))}</div>
              </td>
              <td>${escapeHtml(formatCoordinates(site.coordinates))}</td>
              <td>${escapeHtml(site.coordinate_reference_system)}</td>
              <td>${formatDateTime(site.updated_at)}</td>
              <td><a class="text-action" href="/projects/${this.projectId}/sites/${site.id}">Open</a></td>
            </tr>
          `,
        )
        .join("");
      this.siteTableRegion.innerHTML = `
        <div class="table-wrap">
          <table class="data-table">
            <thead>
              <tr>
                <th scope="col">Site name</th>
                <th scope="col">Coordinates</th>
                <th scope="col">CRS</th>
                <th scope="col">Updated</th>
                <th scope="col">Open</th>
              </tr>
            </thead>
            <tbody>${rows}</tbody>
          </table>
        </div>
      `;
    }

    renderPaginationInto(this.paginationElement, payload, async (direction) => {
      this.offset = direction === "next" ? this.offset + this.limit : Math.max(0, this.offset - this.limit);
      await this.refresh();
    });
  }

  async handleProjectEdit(event) {
    event.preventDefault();
    this.clearMessage();
    const formData = new FormData(this.editForm);
    const payload = {
      name: formData.get("name")?.toString().trim() || "",
      location: optionalText(formData.get("location")),
      description: optionalText(formData.get("description")),
    };
    if (this.auth?.user?.role === "ADMINISTRATOR") {
      payload.project_manager_id = numberFieldValue(formData.get("project_manager_id"));
    }

    this.editSubmit.disabled = true;
    try {
      await api.patch(`/api/v1/projects/${this.projectId}`, payload);
      this.showMessage("Project updated successfully.");
      await this.refresh();
    } catch (error) {
      if (isUnauthorized(error)) {
        this.renderUnauthenticated();
        return;
      }
      this.showMessage(this.describeError(error), "error");
    } finally {
      this.editSubmit.disabled = false;
    }
  }

  async handleProjectArchive() {
    this.clearMessage();
    this.archiveButton.disabled = true;
    try {
      await api.delete(`/api/v1/projects/${this.projectId}`);
      this.showMessage("Project archived successfully.");
      await this.refresh();
    } catch (error) {
      if (isUnauthorized(error)) {
        this.renderUnauthenticated();
        return;
      }
      this.showMessage(this.describeError(error), "error");
    } finally {
      this.archiveButton.disabled = false;
    }
  }

  async handleSiteCreate(event) {
    event.preventDefault();
    this.clearMessage();
    const formData = new FormData(this.siteCreateForm);
    const payload = {
      name: formData.get("name")?.toString().trim() || "",
      coordinates: {
        lat: numericValue(formData.get("lat")),
        lng: numericValue(formData.get("lng")),
      },
    };

    this.siteCreateSubmit.disabled = true;
    try {
      await api.post(`/api/v1/projects/${this.projectId}/sites`, payload);
      this.siteCreateForm.reset();
      this.showMessage("Site created successfully.");
      this.offset = 0;
      await this.refresh();
    } catch (error) {
      if (isUnauthorized(error)) {
        this.renderUnauthenticated();
        return;
      }
      this.showMessage(this.describeError(error), "error");
    } finally {
      this.siteCreateSubmit.disabled = false;
    }
  }

  renderUnauthenticated() {
    super.renderUnauthenticated();
    this.editCard.hidden = true;
    this.siteCreateCard.hidden = true;
    this.overviewList.hidden = true;
    this.overviewState.hidden = false;
    this.overviewState.innerHTML = renderStatePanel(
      "Session required",
      "Sign in again to load this project and its sites.",
      "error",
    );
    this.siteTableRegion.innerHTML = renderStatePanel(
      "Session required",
      "Sign in again to load project sites.",
      "error",
    );
    this.paginationElement.hidden = true;
  }

  renderLoadError(error) {
    this.overviewList.hidden = true;
    this.overviewState.hidden = false;
    this.overviewState.innerHTML = renderStatePanel("Project unavailable", this.describeError(error), "error");
    this.siteTableRegion.innerHTML = renderStatePanel("Sites unavailable", this.describeError(error), "error");
    this.paginationElement.hidden = true;
  }
}

class SiteDetailPageController extends BasePageController {
  constructor(root) {
    super(root);
    this.projectId = root.dataset.projectId;
    this.siteId = root.dataset.siteId;
    this.overviewList = root.querySelector("[data-site-overview-list]");
    this.overviewState = root.querySelector("[data-site-overview-state]");
    this.surveyTableRegion = root.querySelector("[data-survey-table-region]");
    this.siteEditCard = root.querySelector("[data-site-edit-card]");
    this.siteEditForm = root.querySelector("[data-site-edit-form]");
    this.siteEditSubmit = root.querySelector("[data-site-edit-submit]");
    this.siteDeleteButton = root.querySelector("[data-site-delete-button]");
    this.surveyCreateCard = root.querySelector("[data-survey-create-card]");
    this.surveyCreateForm = root.querySelector("[data-survey-create-form]");
    this.surveyCreateSubmit = root.querySelector("[data-survey-create-submit]");
    this.paginationElement = root.querySelector("[data-surveys-pagination]");
    this.offset = 0;
    this.limit = DEFAULT_LIMIT;
    this.site = null;
  }

  async load() {
    this.clearMessage();
    this.bindEvents();
    const [sitePayload, surveyPayload] = await Promise.all([
      api.get(`/api/v1/projects/${this.projectId}/sites/${this.siteId}`),
      api.get(`/api/v1/surveys?project_id=${this.projectId}&site_id=${this.siteId}&limit=${this.limit}&offset=${this.offset}`),
    ]);
    this.site = sitePayload;
    this.renderSite();
    this.renderSurveys(surveyPayload);
    this.toggleForms();
  }

  bindEvents() {
    if (this.bound) {
      return;
    }
    this.bound = true;
    this.siteEditForm?.addEventListener("submit", (event) => this.handleSiteEdit(event));
    this.siteDeleteButton?.addEventListener("click", () => this.handleSiteDelete());
    this.surveyCreateForm?.addEventListener("submit", (event) => this.handleSurveyCreate(event));
    this.surveyTableRegion?.addEventListener("click", (event) => handleRowNavigation(event));
    this.surveyTableRegion?.addEventListener("keydown", (event) => handleRowKeyboard(event));
  }

  toggleForms() {
    const role = this.auth?.user?.role;
    const canManageSite = role === "ADMINISTRATOR" || role === "PROJECT_MANAGER";
    const canCreateSurvey = canManageSite || role === "SURVEY_ENGINEER";
    this.siteEditCard.hidden = !canManageSite;
    this.surveyCreateCard.hidden = !canCreateSurvey;
  }

  renderSite() {
    this.overviewList.hidden = false;
    this.overviewState.hidden = true;
    setField(this.overviewList, "name", this.site.name);
    setField(this.overviewList, "coordinates", escapeHtml(formatCoordinates(this.site.coordinates)));
    setField(this.overviewList, "coordinate_reference_system", escapeHtml(this.site.coordinate_reference_system));
    setField(this.overviewList, "project_id", `Project #${this.site.project_id}`);

    if (this.siteEditForm) {
      this.siteEditForm.elements.name.value = this.site.name || "";
      this.siteEditForm.elements.lat.value = this.site.coordinates?.lat ?? "";
      this.siteEditForm.elements.lng.value = this.site.coordinates?.lng ?? "";
    }
  }

  renderSurveys(payload) {
    const results = Array.isArray(payload?.results) ? payload.results : [];
    if (results.length === 0) {
      this.surveyTableRegion.innerHTML = renderStatePanel(
        "No surveys yet",
        "This site does not currently have any surveys.",
      );
    } else {
      const rows = results
        .map(
          (survey) => `
            <tr class="clickable-row" tabindex="0" data-href="/surveys/${survey.id}">
              <td>
                <strong>${escapeHtml(survey.name)}</strong>
                <div class="table-subcopy">Survey #${escapeHtml(String(survey.id))}</div>
              </td>
              <td>${formatDate(survey.survey_date)}</td>
              <td>${renderBadge(survey.status)}</td>
              <td>${renderBadge(survey.processing_status)}</td>
              <td><a class="text-action" href="/surveys/${survey.id}">Open</a></td>
            </tr>
          `,
        )
        .join("");
      this.surveyTableRegion.innerHTML = `
        <div class="table-wrap">
          <table class="data-table">
            <thead>
              <tr>
                <th scope="col">Survey name</th>
                <th scope="col">Survey date</th>
                <th scope="col">Status</th>
                <th scope="col">Processing status</th>
                <th scope="col">Open</th>
              </tr>
            </thead>
            <tbody>${rows}</tbody>
          </table>
        </div>
      `;
    }

    renderPaginationInto(this.paginationElement, payload, async (direction) => {
      this.offset = direction === "next" ? this.offset + this.limit : Math.max(0, this.offset - this.limit);
      await this.refresh();
    });
  }

  async handleSiteEdit(event) {
    event.preventDefault();
    this.clearMessage();
    const formData = new FormData(this.siteEditForm);
    const payload = {
      name: formData.get("name")?.toString().trim() || "",
      coordinates: {
        lat: numericValue(formData.get("lat")),
        lng: numericValue(formData.get("lng")),
      },
    };

    this.siteEditSubmit.disabled = true;
    try {
      await api.patch(`/api/v1/projects/${this.projectId}/sites/${this.siteId}`, payload);
      this.showMessage("Site updated successfully.");
      await this.refresh();
    } catch (error) {
      if (isUnauthorized(error)) {
        this.renderUnauthenticated();
        return;
      }
      this.showMessage(this.describeError(error), "error");
    } finally {
      this.siteEditSubmit.disabled = false;
    }
  }

  async handleSiteDelete() {
    this.clearMessage();
    this.siteDeleteButton.disabled = true;
    try {
      await api.delete(`/api/v1/projects/${this.projectId}/sites/${this.siteId}`);
      window.location.assign(`/projects/${this.projectId}`);
    } catch (error) {
      if (isUnauthorized(error)) {
        this.renderUnauthenticated();
        return;
      }
      this.showMessage(this.describeError(error), "error");
      this.siteDeleteButton.disabled = false;
    }
  }

  async handleSurveyCreate(event) {
    event.preventDefault();
    this.clearMessage();
    const formData = new FormData(this.surveyCreateForm);
    const payload = {
      project_id: Number(this.projectId),
      site_id: Number(this.siteId),
      name: formData.get("name")?.toString().trim() || "",
      survey_date: formData.get("survey_date")?.toString() || "",
      drone_model: optionalText(formData.get("drone_model")),
      pilot: optionalText(formData.get("pilot")),
      notes: optionalText(formData.get("notes")),
    };

    this.surveyCreateSubmit.disabled = true;
    try {
      const survey = await api.post("/api/v1/surveys", payload);
      this.surveyCreateForm.reset();
      this.showMessage("Survey created successfully.");
      window.location.assign(`/surveys/${survey.id}`);
    } catch (error) {
      if (isUnauthorized(error)) {
        this.renderUnauthenticated();
        return;
      }
      this.showMessage(this.describeError(error), "error");
    } finally {
      this.surveyCreateSubmit.disabled = false;
    }
  }

  renderUnauthenticated() {
    super.renderUnauthenticated();
    this.siteEditCard.hidden = true;
    this.surveyCreateCard.hidden = true;
    this.overviewList.hidden = true;
    this.overviewState.hidden = false;
    this.overviewState.innerHTML = renderStatePanel(
      "Session required",
      "Sign in again to load this site and its surveys.",
      "error",
    );
    this.surveyTableRegion.innerHTML = renderStatePanel(
      "Session required",
      "Sign in again to load site surveys.",
      "error",
    );
    this.paginationElement.hidden = true;
  }

  renderLoadError(error) {
    this.overviewList.hidden = true;
    this.overviewState.hidden = false;
    this.overviewState.innerHTML = renderStatePanel("Site unavailable", this.describeError(error), "error");
    this.surveyTableRegion.innerHTML = renderStatePanel("Surveys unavailable", this.describeError(error), "error");
    this.paginationElement.hidden = true;
  }
}

function renderPagination(payload) {
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

function renderPaginationInto(element, payload, onPage) {
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

function setField(container, fieldName, value, allowHtml = false) {
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

function renderStatePanel(title, description, tone = "empty") {
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

function renderBadge(value) {
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

function presentValue(value, allowHtml = false) {
  if (value === null || value === undefined || value === "") {
    return allowHtml ? '<span class="survey-muted-value">Not provided</span>' : "Not provided";
  }
  return allowHtml ? escapeHtml(String(value)) : String(value);
}

function formatCoordinates(coordinates) {
  if (!coordinates || typeof coordinates !== "object") {
    return "Not provided";
  }
  return `${Number(coordinates.lat).toFixed(6)}, ${Number(coordinates.lng).toFixed(6)}`;
}

function formatDate(value) {
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

function formatDateTime(value) {
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

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

function extractErrorMessage(body) {
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

function handleRowNavigation(event) {
  const ignored = event.target.closest("a, button, input, textarea, select, label");
  if (ignored) {
    return;
  }
  const row = event.target.closest("[data-href]");
  if (row) {
    window.location.assign(row.dataset.href);
  }
}

function handleRowKeyboard(event) {
  const row = event.target.closest("[data-href]");
  if (!row) {
    return;
  }
  if (event.key === "Enter" || event.key === " ") {
    event.preventDefault();
    window.location.assign(row.dataset.href);
  }
}

function optionalText(value) {
  const text = value?.toString().trim() || "";
  return text || null;
}

function numericValue(value) {
  return Number(value);
}

function numberFieldValue(value) {
  const parsed = Number(value);
  return Number.isFinite(parsed) && parsed > 0 ? parsed : null;
}
