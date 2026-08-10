import { initialiseUi } from "./ui.js";
import { initialiseAdminPage } from "./admin-page.js";
import { initialiseProjectPages } from "./projects-pages.js";
import { initialiseSurveyWorkspace } from "./survey-workspace.js";
import { initialiseShellAuth } from "./modules/auth-shell.js";

document.addEventListener("DOMContentLoaded", () => {
  initialiseUi();
  initialiseShellAuth().catch(() => {});
  initialiseAdminPage();
  initialiseProjectPages();
  initialiseSurveyWorkspace();
});
