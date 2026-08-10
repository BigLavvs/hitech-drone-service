import { api, ApiError } from "./api.js";
import { closeModal, openModal } from "./ui.js";

function messageForError(error) {
  if (!(error instanceof ApiError)) {
    return "The demo session could not be started. Try again.";
  }
  if (error.status === 403) {
    return "Your session expired. Refresh the page and try again.";
  }
  if (error.status === 404) {
    return "Assessment demo access is not enabled in this environment.";
  }
  const roleErrors = error.body?.role;
  if (Array.isArray(roleErrors) && roleErrors.length > 0) {
    return roleErrors[0];
  }
  return "The demo session could not be started. Try again.";
}

function setMessage(element, message) {
  if (!element) {
    return;
  }
  element.textContent = message || "";
  element.hidden = !message;
}

function setBusy(form, isBusy) {
  form.querySelectorAll("button").forEach((button) => {
    button.disabled = isBusy;
  });
}

function selectedRole(form) {
  return form.querySelector('input[name="role"]:checked')?.value || "";
}

function redirectToProjects(redirectTo) {
  window.location.assign(redirectTo || "/projects");
}

async function submitDemoRole(form, messageElement) {
  const role = selectedRole(form);
  if (!role) {
    setMessage(messageElement, "Select a demo role to continue.");
    return;
  }

  setMessage(messageElement, "");
  setBusy(form, true);
  try {
    const payload = await api.post("/api/v1/demo-auth/session", { role });
    redirectToProjects(payload.redirect_to);
  } catch (error) {
    setMessage(messageElement, messageForError(error));
  } finally {
    setBusy(form, false);
  }
}

function bindRoleCards(form) {
  form.querySelectorAll("[data-demo-role-card]").forEach((card) => {
    const input = card.querySelector('input[name="role"]');
    card.addEventListener("click", (event) => {
      if (event.target.closest("button")) {
        return;
      }
      input.checked = true;
    });
  });
}

function initialiseDemoModal() {
  const modal = document.getElementById("assessment-demo-modal");
  const openButton = document.querySelector("[data-demo-access-open]");
  const form = document.querySelector("[data-demo-access-form]");
  const messageElement = document.querySelector("[data-demo-access-message]");
  if (!modal || !openButton || !form || !messageElement) {
    return;
  }

  bindRoleCards(form);

  openButton.addEventListener("click", (event) => {
    event.preventDefault();
    setMessage(messageElement, "");
    openModal(modal);
  });

  form.addEventListener("submit", (event) => {
    event.preventDefault();
    void submitDemoRole(form, messageElement);
  });

  modal.addEventListener("click", (event) => {
    if (event.target.matches("[data-modal-close]")) {
      closeModal(modal);
    }
  });
}

document.addEventListener("DOMContentLoaded", () => {
  initialiseDemoModal();
});
