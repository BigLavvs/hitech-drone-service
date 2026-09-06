export const LEAFLET_ASSET_VERSION = "1.9.4";
const LEAFLET_CSS_URL = `https://cdn.jsdelivr.net/npm/leaflet@${LEAFLET_ASSET_VERSION}/dist/leaflet.css`;
const LEAFLET_JS_URL = `https://cdn.jsdelivr.net/npm/leaflet@${LEAFLET_ASSET_VERSION}/dist/leaflet.js`;

let leafletRuntimePromise = null;

export async function ensureLeafletRuntime() {
  if (!leafletRuntimePromise) {
    leafletRuntimePromise = Promise.all([
      ensureStylesheet(LEAFLET_CSS_URL, "leaflet-runtime-css"),
      ensureScript(LEAFLET_JS_URL, () => window.L),
    ]);
  }
  await leafletRuntimePromise;
}

function ensureStylesheet(href, id) {
  if (document.getElementById(id)) {
    return Promise.resolve();
  }

  return new Promise((resolve, reject) => {
    const link = document.createElement("link");
    link.id = id;
    link.rel = "stylesheet";
    link.href = href;
    link.onload = () => resolve();
    link.onerror = () => reject(new Error("The 2D map assets could not be loaded."));
    document.head.append(link);
  });
}

function ensureScript(src, test) {
  if (test()) {
    return Promise.resolve();
  }

  return new Promise((resolve, reject) => {
    const existing = document.querySelector(`script[data-runtime-src="${src}"]`);
    if (existing) {
      existing.addEventListener("load", () => resolve(), { once: true });
      existing.addEventListener(
        "error",
        () => reject(new Error("The 2D map assets could not be loaded.")),
        { once: true },
      );
      return;
    }

    const script = document.createElement("script");
    script.src = src;
    script.async = true;
    script.dataset.runtimeSrc = src;
    script.onload = () => resolve();
    script.onerror = () => reject(new Error("The 2D map assets could not be loaded."));
    document.head.append(script);
  });
}
