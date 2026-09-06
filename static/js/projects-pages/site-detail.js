import { api } from "../api.js";
import { isUnauthorized } from "../modules/auth-shell.js";
import { BasePageController } from "./base.js";
import { DEFAULT_LIMIT } from "./constants.js";
import {
  escapeHtml,
  formatCoordinates,
  formatDate,
  handleRowKeyboard,
  handleRowNavigation,
  numericValue,
  optionalText,
  renderBadge,
  renderPaginationInto,
  renderStatePanel,
  setField,
} from "./helpers.js";

export class SiteDetailPageController extends BasePageController {
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
