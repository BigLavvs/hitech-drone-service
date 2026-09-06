export { api, ApiError } from "../api.js";
export { applyShellUser, isUnauthorized, loadAuthState, prettyRole } from "../modules/auth-shell.js";
export {
  appendRelatedAssetsToFormData,
  buildRelatedAssetSummary,
  getFileExtension,
  mergeRelatedAssetSelections,
  removeRelatedAssetSelection,
  selectReferencedGltfBundleAssets,
} from "../modules/gltf-bundle.js";
export { createMapViewer } from "../modules/map-viewer.js";
export { createModelViewer } from "../modules/model-viewer.js";
export { startPolling } from "../modules/polling.js";

export const TERMINAL_JOB_STATUSES = new Set(["completed", "failed"]);
export const NON_TERMINAL_JOB_STATUSES = new Set(["queued", "running"]);
export const PRIMARY_UPLOAD_ACCEPT =
  ".tif,.tiff,.png,.jpg,.jpeg,.kml,.geojson,.json,.geo.json,.obj,.glb,.gltf,.las,.laz,.ply,.stl";
export const PRIMARY_UPLOAD_EXTENSIONS = new Set(
  PRIMARY_UPLOAD_ACCEPT.split(",").map((value) => value.toLowerCase()),
);
export const RELATED_ASSET_RULES = {
  ".obj": {
    label: "OBJ related assets for an .obj primary file",
    help: "Choose all related files. Accepted companion formats: .mtl, .png, .jpg, .jpeg.",
    accept: ".mtl,.png,.jpg,.jpeg",
    extensions: new Set([".mtl", ".png", ".jpg", ".jpeg"]),
    name: "OBJ",
  },
  ".gltf": {
    label: "GLTF related assets for a .gltf primary file",
    help: "Fallback picker: choose every referenced .bin, .png, .jpg, or .jpeg file directly if folder selection is unavailable.",
    accept: ".bin,.png,.jpg,.jpeg",
    extensions: new Set([".bin", ".png", ".jpg", ".jpeg"]),
    name: "GLTF",
  },
};
export const ASSET_SOURCE_LABELS = {
  folder: "from the selected GLTF bundle folder",
  picker: "with the fallback file picker",
};
export const APPROVAL_ACTION_LABELS = {
  submitted: "Submitted",
  approved: "Approved",
  rejected: "Rejected",
  archived: "Archived",
};
