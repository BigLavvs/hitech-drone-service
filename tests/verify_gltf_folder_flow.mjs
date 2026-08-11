import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { resolve } from "node:path";

const helperSource = await readFile(
  resolve("static/js/modules/gltf-bundle.js"),
  "utf8",
);
const helperModule = await import(
  `data:text/javascript;charset=utf-8,${encodeURIComponent(helperSource)}`,
);

const {
  appendRelatedAssetsToFormData,
  mergeRelatedAssetSelections,
  removeRelatedAssetSelection,
  buildRelatedAssetSummary,
  selectReferencedGltfBundleAssets,
} = helperModule;

function createFolderFile(name, webkitRelativePath, type, content) {
  const file = new File([content], name, { type });
  Object.defineProperty(file, "webkitRelativePath", {
    configurable: true,
    value: webkitRelativePath,
  });
  return file;
}

const manifestText = JSON.stringify({
  asset: { version: "2.0" },
  buffers: [{ uri: "scene.bin" }],
  images: [{ uri: "textures/material_0_baseColor.jpeg" }],
});

const folderFiles = [
  createFolderFile("scene.gltf", "sample-bundle/scene.gltf", "model/gltf+json", manifestText),
  createFolderFile("scene.bin", "sample-bundle/scene.bin", "application/octet-stream", "buffer"),
  createFolderFile(
    "material_0_baseColor.jpeg",
    "sample-bundle/textures/material_0_baseColor.jpeg",
    "image/jpeg",
    "texture",
  ),
  createFolderFile("license.txt", "sample-bundle/license.txt", "text/plain", "ignore me"),
];

const selection = selectReferencedGltfBundleAssets({
  manifestText,
  folderFiles,
});

const pickerTexture = new File(["texture"], "detail.jpeg", { type: "image/jpeg" });
const duplicatePickerTexture = new File(["texture"], "material_0_baseColor.jpeg", { type: "image/jpeg" });

assert.deepEqual(selection.missingReferences, []);
assert.deepEqual(selection.unsupportedReferences, []);
assert.deepEqual(
  selection.selectedAssets.map((asset) => asset.relativePath),
  ["scene.bin", "textures/material_0_baseColor.jpeg"],
);
assert.equal(
  buildRelatedAssetSummary(
    selection.selectedAssets.map((asset) => asset.relativePath),
    "from the selected GLTF bundle folder",
  ),
  "2 related assets selected from the selected GLTF bundle folder: scene.bin, textures/material_0_baseColor.jpeg",
);

const mergedSelection = mergeRelatedAssetSelections(
  [],
  selection.selectedAssets.map((asset) => ({
    file: asset.file,
    displayName: asset.relativePath,
    source: "folder",
  })),
);
const mergedWithPickerAssets = mergeRelatedAssetSelections(mergedSelection, [
  {
    file: pickerTexture,
    displayName: pickerTexture.name,
    source: "picker",
  },
  {
    file: duplicatePickerTexture,
    displayName: duplicatePickerTexture.name,
    source: "picker",
  },
]);

assert.deepEqual(
  mergedWithPickerAssets.map((asset) => asset.displayName),
  ["scene.bin", "textures/material_0_baseColor.jpeg", "detail.jpeg", "material_0_baseColor.jpeg"],
);

const afterRemoval = removeRelatedAssetSelection(mergedWithPickerAssets, "textures/material_0_basecolor.jpeg");
assert.deepEqual(
  afterRemoval.map((asset) => asset.displayName),
  ["scene.bin", "detail.jpeg", "material_0_baseColor.jpeg"],
);

const formData = new FormData();
appendRelatedAssetsToFormData(
  formData,
  afterRemoval.map((asset) => asset.file),
);

assert.deepEqual(
  formData.getAll("assets").map((file) => file.name),
  ["scene.bin", "detail.jpeg", "material_0_baseColor.jpeg"],
);
assert.deepEqual(formData.getAll("file"), []);

console.log("Verified GLTF folder flow add, merge, remove, and repeated multipart assets.");
