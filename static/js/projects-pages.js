import { ProjectsPageController } from "./projects-pages/project-list.js";
import { ProjectDetailPageController } from "./projects-pages/project-detail.js";
import { SiteDetailPageController } from "./projects-pages/site-detail.js";

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
