function focusableElements(container) {
  return [...container.querySelectorAll('a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])')];
}

export function openModal(modal) {
  if (!modal) return;
  modal.hidden = false;
  modal.setAttribute("aria-hidden", "false");
  document.body.dataset.modalOpen = "true";
  focusableElements(modal)[0]?.focus();
}

export function closeModal(modal) {
  if (!modal) return;
  modal.hidden = true;
  modal.setAttribute("aria-hidden", "true");
  delete document.body.dataset.modalOpen;
}

function bindModals() {
  document.addEventListener("click", (event) => {
    const opener = event.target.closest("[data-modal-open]");
    if (opener) {
      event.preventDefault();
      openModal(document.getElementById(opener.dataset.modalOpen));
    }
    const closer = event.target.closest("[data-modal-close]");
    if (closer) closeModal(closer.closest(".modal"));
    if (event.target.matches("[data-logout][href='#']")) event.preventDefault();
  });
  document.addEventListener("keydown", (event) => {
    const modal = document.querySelector(".modal:not([hidden])");
    if (event.key === "Escape") closeModal(modal);
  });
}

function bindTabs() {
  document.addEventListener("click", (event) => {
    const tab = event.target.closest('[role="tab"]');
    if (!tab) return;
    const tabList = tab.closest('[role="tablist"]');
    const panel = document.getElementById(tab.getAttribute("aria-controls"));
    if (!tabList || !panel) return;
    tabList.querySelectorAll('[role="tab"]').forEach((item) => {
      item.setAttribute("aria-selected", String(item === tab));
      item.tabIndex = item === tab ? 0 : -1;
    });
    document.querySelectorAll(`[data-tab-panel][data-tab-group="${tabList.dataset.tabGroup}"]`).forEach((item) => {
      item.hidden = item !== panel;
    });
  });
}

function bindMobileNavigation() {
  const toggle = document.querySelector("[data-mobile-nav-toggle]");
  const navigation = document.getElementById(toggle?.getAttribute("aria-controls"));
  if (!toggle || !navigation) return;

  const mobileBreakpoint = window.matchMedia("(max-width: 780px)");
  const setMenuState = (isOpen, returnFocus = false) => {
    navigation.hidden = !isOpen;
    toggle.setAttribute("aria-expanded", String(isOpen));
    toggle.setAttribute("aria-label", isOpen ? "Close navigation menu" : "Open navigation menu");
    if (returnFocus) toggle.focus();
  };
  const syncNavigation = () => {
    document.body.classList.add("mobile-nav-ready");
    toggle.hidden = !mobileBreakpoint.matches;
    if (mobileBreakpoint.matches) {
      setMenuState(false);
    } else {
      navigation.hidden = false;
      toggle.setAttribute("aria-expanded", "false");
      toggle.setAttribute("aria-label", "Open navigation menu");
    }
  };

  toggle.addEventListener("click", () => {
    setMenuState(toggle.getAttribute("aria-expanded") !== "true");
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && mobileBreakpoint.matches && !navigation.hidden) {
      setMenuState(false, true);
    }
  });
  document.addEventListener("click", (event) => {
    if (mobileBreakpoint.matches && !navigation.hidden && !navigation.contains(event.target) && !toggle.contains(event.target)) {
      setMenuState(false);
    }
  });
  mobileBreakpoint.addEventListener("change", syncNavigation);
  syncNavigation();
}

export function initialiseUi() {
  bindModals();
  bindTabs();
  bindMobileNavigation();
}
