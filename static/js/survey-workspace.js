import { SurveyWorkspaceController } from "./survey-workspace/controller.js";

export function initialiseSurveyWorkspace() {
  const root = document.querySelector("[data-survey-workspace]");
  if (!root) return;
  const controller = new SurveyWorkspaceController(root);
  controller.initialise();
}
