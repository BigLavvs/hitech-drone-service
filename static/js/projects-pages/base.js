import { ApiError } from "../api.js";
import { applyShellUser, isUnauthorized, loadAuthState } from "../modules/auth-shell.js";
import { extractErrorMessage } from "./helpers.js";

export class BasePageController {
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
