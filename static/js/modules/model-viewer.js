import { api } from "../api.js";

const THREE_VERSION = "0.167.1";
const THREE_MODULE_URL = "three";
const ORBIT_CONTROLS_URL = "three/addons/controls/OrbitControls.js";
const GLTF_LOADER_URL = "three/addons/loaders/GLTFLoader.js";

let threeRuntimePromise = null;

export function createModelViewer(container, options = {}) {
  return new ModelViewer(container, options);
}

class ModelViewer {
  constructor(container, { messageElement } = {}) {
    this.container = container;
    this.messageElement = messageElement;
    this.runtime = null;
    this.renderer = null;
    this.scene = null;
    this.camera = null;
    this.controls = null;
    this.loader = null;
    this.modelRoot = null;
    this.gridHelper = null;
    this.resizeObserver = null;
    this.rendering = false;
    this.catalog = [];
    this.compatibleModels = [];
    this.potreeModels = [];
    this.currentModelId = null;
    this.loadSequence = 0;

    this.renderShell();
    this.bindEvents();
  }

  renderShell() {
    this.container.innerHTML = `
      <div class="survey-viewer-layout survey-viewer-layout--model">
        <aside class="survey-viewer-sidebar">
          <section class="survey-viewer-panel">
            <div class="survey-viewer-panel-header">
              <h3>Model source</h3>
              <select class="survey-viewer-select" data-model-select hidden></select>
            </div>
            <div class="survey-action-row">
              <button class="button" type="button" data-model-reset-button>Reset view</button>
              <button class="button" type="button" data-model-fullscreen-button>Fullscreen</button>
            </div>
          </section>
          <section class="survey-viewer-panel">
            <h3>Metadata</h3>
            <dl class="survey-viewer-metadata" data-model-metadata></dl>
          </section>
          <section class="survey-viewer-panel" data-model-notes-panel hidden>
            <h3>Unavailable in this viewer</h3>
            <div data-model-notes></div>
          </section>
        </aside>
        <div class="survey-viewer-stage survey-viewer-stage--model">
          <div class="survey-model-canvas" data-model-canvas></div>
          <div class="survey-viewer-state" data-model-state></div>
        </div>
      </div>
    `;

    this.selectElement = this.container.querySelector("[data-model-select]");
    this.resetButton = this.container.querySelector("[data-model-reset-button]");
    this.fullscreenButton = this.container.querySelector("[data-model-fullscreen-button]");
    this.metadataElement = this.container.querySelector("[data-model-metadata]");
    this.notesPanel = this.container.querySelector("[data-model-notes-panel]");
    this.notesElement = this.container.querySelector("[data-model-notes]");
    this.canvasElement = this.container.querySelector("[data-model-canvas]");
    this.stateElement = this.container.querySelector("[data-model-state]");
    this.stageElement = this.container.querySelector(".survey-viewer-stage");
  }

  bindEvents() {
    this.selectElement?.addEventListener("change", async (event) => {
      await this.loadSelectedModel(event.target.value);
    });
    this.resetButton?.addEventListener("click", () => this.resetView());
    this.fullscreenButton?.addEventListener("click", async () => {
      await this.enterFullscreen();
    });
  }

  async load(surveyId) {
    this.clearMessage();
    this.showState("Loading 3D models", "Fetching ready models for this survey.", "loading");
    this.renderMetadata(null);
    this.renderNotes();

    const catalog = await api.get(`/api/v1/surveys/${surveyId}/models`);
    this.catalog = Array.isArray(catalog) ? catalog : [];
    this.compatibleModels = this.catalog.filter((entry) =>
      ["glb", "gltf"].includes(String(entry.viewer_source_type || "").toLowerCase()),
    );
    this.potreeModels = this.catalog.filter(
      (entry) => String(entry.viewer_source_type || "").toLowerCase() === "potree",
    );

    this.renderSelector();
    this.renderNotes();

    if (this.catalog.length === 0) {
      this.showUnavailableState("No 3D model data ready", "This survey does not currently have any ready 3D model outputs.");
      return;
    }

    if (this.compatibleModels.length === 0) {
      this.showUnavailableState(
        "Three.js model unavailable",
        "Only Potree point-cloud output is ready for this survey. It cannot be rendered in this Three.js viewer.",
      );
      return;
    }

    await this.ensureRuntime();
    const selectedId =
      this.compatibleModels.find((entry) => String(entry.id) === String(this.currentModelId))?.id ??
      this.compatibleModels[0].id;
    await this.loadSelectedModel(selectedId);
  }

  async ensureRuntime() {
    if (this.runtime) {
      return this.runtime;
    }

    this.runtime = await ensureThreeRuntime();
    this.initialiseScene();
    return this.runtime;
  }

  initialiseScene() {
    if (this.renderer) {
      return;
    }

    const { THREE, OrbitControls, GLTFLoader } = this.runtime;
    this.scene = new THREE.Scene();
    this.scene.background = new THREE.Color("#f8fbfa");

    this.camera = new THREE.PerspectiveCamera(55, 1, 0.1, 100000);
    this.camera.position.set(12, 12, 12);

    this.renderer = new THREE.WebGLRenderer({ antialias: true, alpha: false });
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
    this.renderer.outputColorSpace = THREE.SRGBColorSpace;
    this.canvasElement.replaceChildren(this.renderer.domElement);

    this.controls = new OrbitControls(this.camera, this.renderer.domElement);
    this.controls.enableDamping = true;
    this.controls.dampingFactor = 0.08;
    this.controls.screenSpacePanning = true;

    this.loader = new GLTFLoader();
    this.loader.setCrossOrigin("anonymous");

    const ambientLight = new THREE.HemisphereLight(0xffffff, 0xcad7d2, 1.1);
    const keyLight = new THREE.DirectionalLight(0xffffff, 1.15);
    keyLight.position.set(15, 20, 10);
    const fillLight = new THREE.DirectionalLight(0xffffff, 0.5);
    fillLight.position.set(-12, 8, -10);
    this.scene.add(ambientLight, keyLight, fillLight);

    this.resizeObserver = new ResizeObserver(() => this.resizeRenderer());
    this.resizeObserver.observe(this.stageElement);
    this.resizeRenderer();
    this.startRenderLoop();
  }

  renderSelector() {
    if (this.compatibleModels.length <= 1) {
      this.selectElement.hidden = true;
      this.selectElement.innerHTML = "";
      return;
    }

    this.selectElement.hidden = false;
    this.selectElement.innerHTML = this.compatibleModels
      .map(
        (entry) => `
          <option value="${escapeAttribute(entry.id)}"${String(entry.id) === String(this.currentModelId) ? " selected" : ""}>
            ${escapeHtml(entry.original_filename)} (${escapeHtml((entry.display_format || entry.format || "").toUpperCase())})
          </option>
        `,
      )
      .join("");
  }

  renderMetadata(model) {
    if (!model) {
      this.metadataElement.innerHTML = '<div class="survey-viewer-empty-copy">Select a compatible model to inspect its metadata.</div>';
      return;
    }

    const entries = [
      ["Filename", model.original_filename],
      ["Format", model.display_format || model.format],
      ["Vertex count", formatInteger(model.vertex_count)],
      ["Bounding box", formatBoundingBox(model.bounding_box)],
      ["CRS", model.crs || "Not provided"],
    ];

    this.metadataElement.innerHTML = entries
      .map(
        ([label, value]) => `
          <div class="survey-viewer-metadata-item">
            <dt>${escapeHtml(label)}</dt>
            <dd>${escapeHtml(String(value))}</dd>
          </div>
        `,
      )
      .join("");
  }

  renderNotes() {
    if (this.potreeModels.length === 0) {
      this.notesPanel.hidden = true;
      this.notesElement.innerHTML = "";
      return;
    }

    this.notesPanel.hidden = false;
    this.notesElement.innerHTML = `
      <p class="survey-viewer-empty-copy">Point-cloud Potree output is present but not supported by this Three.js viewer.</p>
      <ul class="survey-viewer-note-list">
        ${this.potreeModels.map((entry) => `<li>${escapeHtml(entry.original_filename)}</li>`).join("")}
      </ul>
    `;
  }

  async loadSelectedModel(modelId) {
    const model = this.compatibleModels.find((entry) => String(entry.id) === String(modelId));
    if (!model) {
      return;
    }

    const requestId = ++this.loadSequence;
    this.currentModelId = model.id;
    this.renderSelector();
    this.renderMetadata(model);
    this.showState("Loading model", "Streaming the selected 3D model into the viewer.", "loading");

    try {
      const gltf = await new Promise((resolve, reject) => {
        this.loader.load(
          model.source_url,
          resolve,
          (event) => {
            if (requestId !== this.loadSequence) {
              return;
            }
            if (event.total > 0) {
              const progress = Math.round((event.loaded / event.total) * 100);
              this.showState("Loading model", `Streaming the selected 3D model into the viewer. ${progress}% complete.`, "loading");
            }
          },
          () => reject(new Error("Model load failed.")),
        );
      });

      if (requestId !== this.loadSequence) {
        return;
      }

      this.mountModel(gltf);
      this.fitCameraToModel(model);
      this.hideState();
    } catch (_error) {
      if (requestId !== this.loadSequence) {
        return;
      }
      this.clearModel();
      this.showState(
        "Model could not be displayed",
        "The selected model could not be loaded in this browser viewer.",
        "error",
      );
      this.showMessage("The selected model could not be loaded in the Three.js viewer.", "error");
    }
  }

  mountModel(gltf) {
    const { THREE } = this.runtime;
    this.clearModel();

    this.modelRoot = gltf.scene || gltf.scenes?.[0] || null;
    if (!this.modelRoot) {
      throw new Error("Empty model.");
    }

    this.modelRoot.traverse((node) => {
      if (node.isMesh) {
        node.castShadow = false;
        node.receiveShadow = false;
        if (node.material) {
          node.material.side = THREE.FrontSide;
        }
      }
    });
    this.scene.add(this.modelRoot);
  }

  fitCameraToModel(model) {
    const { THREE } = this.runtime;
    const box = new THREE.Box3();

    if (this.modelRoot) {
      box.setFromObject(this.modelRoot);
    }

    if (box.isEmpty() && model.bounding_box?.min && model.bounding_box?.max) {
      box.min.fromArray(model.bounding_box.min.map(Number));
      box.max.fromArray(model.bounding_box.max.map(Number));
    }

    if (box.isEmpty()) {
      this.controls.target.set(0, 0, 0);
      this.camera.position.set(12, 12, 12);
      this.controls.update();
      return;
    }

    const center = box.getCenter(new THREE.Vector3());
    const size = box.getSize(new THREE.Vector3());
    const radius = Math.max(size.length() * 0.6, 1);

    if (this.gridHelper) {
      this.scene.remove(this.gridHelper);
    }
    const gridSize = Math.max(Math.ceil(radius * 4), 10);
    this.gridHelper = new THREE.GridHelper(gridSize, 12, 0xd6e0db, 0xe7efec);
    this.gridHelper.position.set(center.x, box.min.y, center.z);
    this.scene.add(this.gridHelper);

    this.controls.target.copy(center);
    this.camera.near = Math.max(radius / 100, 0.01);
    this.camera.far = Math.max(radius * 100, 1000);
    this.camera.position.set(center.x + radius * 1.6, center.y + radius * 1.2, center.z + radius * 1.6);
    this.camera.updateProjectionMatrix();
    this.controls.update();
  }

  resetView() {
    const model = this.compatibleModels.find((entry) => String(entry.id) === String(this.currentModelId));
    if (!model) {
      return;
    }
    this.fitCameraToModel(model);
  }

  async enterFullscreen() {
    if (!document.fullscreenElement) {
      await this.stageElement.requestFullscreen?.();
    } else if (document.fullscreenElement === this.stageElement) {
      await document.exitFullscreen?.();
    }
    this.resizeRenderer();
  }

  resizeRenderer() {
    if (!this.renderer || !this.camera) {
      return;
    }

    const width = Math.max(this.stageElement.clientWidth, 320);
    const height = Math.max(this.stageElement.clientHeight, 420);
    this.renderer.setSize(width, height, false);
    this.camera.aspect = width / height;
    this.camera.updateProjectionMatrix();
  }

  startRenderLoop() {
    if (this.rendering) {
      return;
    }

    this.rendering = true;
    const renderFrame = () => {
      if (!this.rendering || !this.renderer || !this.scene || !this.camera) {
        return;
      }
      this.controls?.update();
      this.renderer.render(this.scene, this.camera);
      requestAnimationFrame(renderFrame);
    };
    requestAnimationFrame(renderFrame);
  }

  stopRenderLoop() {
    this.rendering = false;
  }

  clearModel() {
    if (this.modelRoot) {
      this.scene?.remove(this.modelRoot);
      this.disposeObject3D(this.modelRoot);
      this.modelRoot = null;
    }
    if (this.gridHelper) {
      this.scene?.remove(this.gridHelper);
      this.gridHelper.geometry?.dispose?.();
      this.gridHelper.material?.dispose?.();
      this.gridHelper = null;
    }
  }

  disposeObject3D(object) {
    object.traverse((node) => {
      if (node.geometry) {
        node.geometry.dispose?.();
      }
      if (Array.isArray(node.material)) {
        node.material.forEach((material) => material?.dispose?.());
      } else {
        node.material?.dispose?.();
      }
    });
  }

  showUnavailableState(title, description, tone = "empty") {
    this.clearModel();
    this.showState(title, description, tone);
  }

  showState(title, description, tone = "empty") {
    const toneClass =
      tone === "loading" ? " survey-viewer-state--loading" : tone === "error" ? " survey-viewer-state--error" : "";
    const spinner =
      tone === "loading" ? '<div class="loading-indicator" aria-hidden="true"></div>' : "";

    this.stateElement.innerHTML = `
      <section class="state-panel${toneClass}">
        ${spinner}
        <div>
          <h2>${escapeHtml(title)}</h2>
          <p>${escapeHtml(description)}</p>
        </div>
      </section>
    `;
    this.stateElement.hidden = false;
  }

  hideState() {
    this.stateElement.hidden = true;
    this.stateElement.innerHTML = "";
  }

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

  destroy() {
    this.stopRenderLoop();
    this.clearModel();
    this.resizeObserver?.disconnect();
    this.resizeObserver = null;
    this.controls?.dispose?.();
    this.renderer?.dispose?.();
    this.canvasElement.replaceChildren();
    this.renderer = null;
    this.controls = null;
    this.scene = null;
    this.camera = null;
    this.loader = null;
  }
}

async function ensureThreeRuntime() {
  if (!threeRuntimePromise) {
    threeRuntimePromise = Promise.all([
      import(THREE_MODULE_URL),
      import(ORBIT_CONTROLS_URL),
      import(GLTF_LOADER_URL),
    ]).then(([THREE, controlsModule, loaderModule]) => ({
      THREE,
      OrbitControls: controlsModule.OrbitControls,
      GLTFLoader: loaderModule.GLTFLoader,
    }));
  }
  return threeRuntimePromise;
}

function formatInteger(value) {
  const number = Number(value);
  return Number.isFinite(number) ? new Intl.NumberFormat().format(number) : "Not provided";
}

function formatBoundingBox(bounds) {
  if (!bounds?.min || !bounds?.max) {
    return "Not provided";
  }
  return `min (${bounds.min.join(", ")}) / max (${bounds.max.join(", ")})`;
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

function escapeAttribute(value) {
  return escapeHtml(value);
}
