import { api, ApiError } from "../api.js";

let authPromise = null;

export async function loadAuthState({ force = false } = {}) {
  if (!authPromise || force) {
    authPromise = api.get("/api/v1/auth/validate");
  }
  return authPromise;
}

export function applyShellUser(auth) {
  const user = auth?.user;
  const emailElement = document.querySelector("[data-shell-user-email]");
  const roleElement = document.querySelector("[data-shell-user-role]");
  const initialsElement = document.querySelector("[data-shell-user-initials]");
  const adminNavigationItems = document.querySelectorAll("[data-admin-nav]");

  if (!emailElement || !roleElement || !initialsElement) {
    syncAdminNavigation(adminNavigationItems, user);
    return;
  }

  if (!user) {
    emailElement.textContent = "Session required";
    roleElement.textContent = "Not authenticated";
    initialsElement.textContent = "HS";
    syncAdminNavigation(adminNavigationItems, null);
    return;
  }

  emailElement.textContent = user.email;
  roleElement.textContent = prettyRole(user.role);
  initialsElement.textContent = initialsFor(user.email);
  syncAdminNavigation(adminNavigationItems, user);
}

export function isUnauthorized(error) {
  return error instanceof ApiError && error.status === 401;
}

export function prettyRole(role) {
  return String(role || "")
    .replaceAll("_", " ")
    .toLowerCase()
    .replace(/\b\w/g, (character) => character.toUpperCase());
}

export function initialsFor(email) {
  const source = String(email || "").split("@")[0] || "HD";
  const parts = source
    .split(/[^a-zA-Z0-9]+/)
    .filter(Boolean)
    .slice(0, 2);
  if (parts.length === 0) {
    return "HD";
  }
  return parts.map((part) => part[0].toUpperCase()).join("").slice(0, 2);
}

export async function initialiseShellAuth() {
  try {
    applyShellUser(await loadAuthState());
  } catch (error) {
    if (isUnauthorized(error)) {
      applyShellUser(null);
      return;
    }
    throw error;
  }
}

function syncAdminNavigation(elements, user) {
  const isAdmin = user?.role === "ADMINISTRATOR";
  elements.forEach((element) => {
    element.hidden = !isAdmin;
  });
}
