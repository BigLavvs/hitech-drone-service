import { api } from "../api.js";
import { isUnauthorized, prettyRole } from "../modules/auth-shell.js";
import { BasePageController } from "./base.js";
import { DEFAULT_LIMIT } from "./constants.js";
import {
  escapeHtml,
  formatCoordinates,
  formatDateTime,
  handleRowKeyboard,
  handleRowNavigation,
  numberFieldValue,
  numericValue,
  optionalText,
  presentValue,
  renderBadge,
  renderPaginationInto,
  renderStatePanel,
  setField,
} from "./helpers.js";

export class ProjectDetailPageController extends BasePageController {
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
    this.membersCard = root.querySelector("[data-project-members-card]");
    this.membersMessage = root.querySelector("[data-project-members-message]");
    this.membersContent = root.querySelector("[data-project-members-content]");
    this.memberForm = root.querySelector("[data-project-member-form]");
    this.memberSelect = root.querySelector("#project-member-user-id");
    this.memberSubmit = root.querySelector("[data-project-member-submit]");
    this.paginationElement = root.querySelector("[data-sites-pagination]");
    this.offset = 0;
    this.limit = DEFAULT_LIMIT;
    this.project = null;
    this.members = [];
    this.availableMembers = [];
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
    await this.loadMembersIfManageable();
  }

  bindEvents() {
    if (this.bound) {
      return;
    }
    this.bound = true;
    this.editForm?.addEventListener("submit", (event) => this.handleProjectEdit(event));
    this.archiveButton?.addEventListener("click", () => this.handleProjectArchive());
    this.siteCreateForm?.addEventListener("submit", (event) => this.handleSiteCreate(event));
    this.memberForm?.addEventListener("submit", (event) => this.handleMemberAdd(event));
    this.membersContent?.addEventListener("click", (event) => this.handleMemberRemove(event));
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

  async loadMembersIfManageable() {
    if (!this.canManageMembers()) {
      this.membersCard.hidden = true;
      return;
    }

    if (this.project?.status !== "active") {
      this.membersCard.hidden = false;
      this.memberForm.hidden = true;
      this.membersContent.innerHTML = renderStatePanel(
        "Membership locked",
        "Project members can be managed only while the project is active.",
      );
      return;
    }

    this.membersCard.hidden = false;
    this.memberForm.hidden = false;
    this.clearMembersMessage();

    try {
      const [members, candidates] = await Promise.all([
        api.get(`/api/v1/projects/${this.projectId}/members`),
        api.get(`/api/v1/projects/${this.projectId}/available-members`),
      ]);
      this.members = Array.isArray(members) ? members : [];
      this.availableMembers = Array.isArray(candidates) ? candidates : [];
      this.renderMembers();
      this.renderMemberOptions();
    } catch (error) {
      if (isUnauthorized(error)) {
        this.renderUnauthenticated();
        return;
      }
      this.membersContent.innerHTML = renderStatePanel(
        "Members unavailable",
        this.describeError(error),
        "error",
      );
      this.showMembersMessage(this.describeError(error), "error");
    }
  }

  canManageMembers() {
    return ["ADMINISTRATOR", "PROJECT_MANAGER"].includes(this.auth?.user?.role);
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

  renderMembers() {
    if (!Array.isArray(this.members) || this.members.length === 0) {
      this.membersContent.innerHTML = renderStatePanel(
        "No project members",
        "No Survey Engineers or Viewers are currently assigned to this project.",
      );
      return;
    }

    const rows = this.members
      .map(
        (member) => `
          <tr>
            <td>
              <strong>${escapeHtml(member.email)}</strong>
            </td>
            <td>${escapeHtml(prettyRole(member.role))}</td>
            <td class="survey-table-actions">
              <button class="button survey-action-button" type="button" data-remove-member-id="${member.id}">Remove</button>
            </td>
          </tr>
        `,
      )
      .join("");

    this.membersContent.innerHTML = `
      <div class="table-wrap">
        <table class="data-table">
          <thead>
            <tr>
              <th scope="col">Email</th>
              <th scope="col">Role</th>
              <th scope="col">Action</th>
            </tr>
          </thead>
          <tbody>${rows}</tbody>
        </table>
      </div>
    `;
  }

  renderMemberOptions() {
    if (!this.memberSelect) {
      return;
    }

    const options = ['<option value="">Select a member</option>'];
    for (const member of this.availableMembers) {
      options.push(
        `<option value="${member.id}">${escapeHtml(member.email)} (${escapeHtml(prettyRole(member.role))})</option>`,
      );
    }
    this.memberSelect.innerHTML = options.join("");
    this.memberSelect.disabled = this.availableMembers.length === 0;
    this.memberSubmit.disabled = this.availableMembers.length === 0;
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

  async handleMemberAdd(event) {
    event.preventDefault();
    this.clearMembersMessage();
    if (!this.memberSelect.value) {
      this.showMembersMessage("Select a member to add.", "error");
      return;
    }

    this.memberSubmit.disabled = true;
    try {
      await api.post(`/api/v1/projects/${this.projectId}/members`, {
        user_id: Number(this.memberSelect.value),
      });
      await this.loadMembersIfManageable();
      this.showMembersMessage("Member added.");
    } catch (error) {
      if (isUnauthorized(error)) {
        this.renderUnauthenticated();
        return;
      }
      this.showMembersMessage(this.describeError(error), "error");
    } finally {
      this.memberSubmit.disabled = false;
    }
  }

  async handleMemberRemove(event) {
    const button = event.target.closest("[data-remove-member-id]");
    if (!button) {
      return;
    }

    this.clearMembersMessage();
    button.disabled = true;
    try {
      await api.delete(`/api/v1/projects/${this.projectId}/members/${button.dataset.removeMemberId}`);
      await this.loadMembersIfManageable();
      this.showMembersMessage("Member removed.");
    } catch (error) {
      if (isUnauthorized(error)) {
        this.renderUnauthenticated();
        return;
      }
      this.showMembersMessage(this.describeError(error), "error");
      button.disabled = false;
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
    this.membersCard.hidden = true;
    this.paginationElement.hidden = true;
  }

  renderLoadError(error) {
    this.overviewList.hidden = true;
    this.overviewState.hidden = false;
    this.overviewState.innerHTML = renderStatePanel("Project unavailable", this.describeError(error), "error");
    this.siteTableRegion.innerHTML = renderStatePanel("Sites unavailable", this.describeError(error), "error");
    this.paginationElement.hidden = true;
  }

  showMembersMessage(message, tone = "info") {
    if (!this.membersMessage) {
      return;
    }
    this.membersMessage.textContent = message;
    this.membersMessage.hidden = false;
    this.membersMessage.classList.toggle("is-error", tone === "error");
  }

  clearMembersMessage() {
    if (!this.membersMessage) {
      return;
    }
    this.membersMessage.textContent = "";
    this.membersMessage.hidden = true;
    this.membersMessage.classList.remove("is-error");
  }
}
