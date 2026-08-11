const GLTF_ALLOWED_ASSET_EXTENSIONS = new Set([".bin", ".png", ".jpg", ".jpeg"]);

export function getFileExtension(filename) {
  if (!filename || !String(filename).includes(".")) {
    return "";
  }
  return `.${String(filename).split(".").pop().toLowerCase()}`;
}

export function normalizeFolderRelativePath(fileLike) {
  const rawPath =
    typeof fileLike?.webkitRelativePath === "string" && fileLike.webkitRelativePath.trim()
      ? fileLike.webkitRelativePath
      : fileLike?.name || "";
  const normalized = String(rawPath).replaceAll("\\", "/");
  const parts = normalized.split("/").filter(Boolean);
  if (parts.length <= 1) {
    return parts[0] || "";
  }
  return parts.slice(1).join("/");
}

export function extractGltfExternalReferences(manifestText) {
  let payload;
  try {
    payload = JSON.parse(manifestText);
  } catch {
    throw new Error("The selected primary GLTF file could not be read for folder matching.");
  }

  const references = [];
  for (const collectionName of ["buffers", "images"]) {
    const collection = payload?.[collectionName];
    if (collection === undefined) {
      continue;
    }
    if (!Array.isArray(collection)) {
      throw new Error(`GLTF ${collectionName} must be an array for folder matching.`);
    }
    for (const resource of collection) {
      if (!resource || typeof resource !== "object") {
        throw new Error(`GLTF ${collectionName} entries must be objects for folder matching.`);
      }
      const uri = resource.uri;
      if (uri === undefined || uri === null || uri === "") {
        continue;
      }
      if (typeof uri !== "string") {
        throw new Error("GLTF resource URIs must be strings for folder matching.");
      }
      if (uri.startsWith("data:")) {
        continue;
      }
      references.push(normalizeGltfResourcePath(uri));
    }
  }

  return references;
}

export function selectReferencedGltfBundleAssets({ manifestText, folderFiles }) {
  const references = extractGltfExternalReferences(manifestText);
  const referencedAssets = references.filter((reference) =>
    GLTF_ALLOWED_ASSET_EXTENSIONS.has(getFileExtension(reference)),
  );
  const unsupportedReferences = references.filter(
    (reference) => !GLTF_ALLOWED_ASSET_EXTENSIONS.has(getFileExtension(reference)),
  );
  const filesByRelativePath = new Map();

  for (const file of folderFiles) {
    const relativePath = normalizeFolderRelativePath(file);
    if (!relativePath || relativePath.endsWith(".gltf")) {
      continue;
    }
    filesByRelativePath.set(relativePath, file);
  }

  const selectedAssets = referencedAssets
    .filter((reference) => filesByRelativePath.has(reference))
    .map((reference) => ({
      file: filesByRelativePath.get(reference),
      relativePath: reference,
    }));
  const missingReferences = referencedAssets.filter((reference) => !filesByRelativePath.has(reference));

  return {
    missingReferences,
    references,
    selectedAssets,
    unsupportedReferences,
  };
}

export function appendRelatedAssetsToFormData(formData, assetFiles) {
  for (const assetFile of assetFiles) {
    formData.append("assets", assetFile);
  }
}

export function buildRelatedAssetSelectionKey({ displayName, file, source = "picker" }) {
  const pathValue =
    source === "folder"
      ? String(displayName || "")
      : String(file?.webkitRelativePath || file?.name || displayName || "");
  return pathValue.trim().replaceAll("\\", "/").toLowerCase();
}

export function mergeRelatedAssetSelections(existingAssets, incomingAssets) {
  const mergedAssets = new Map(existingAssets.map((asset) => [asset.key, asset]));
  for (const asset of incomingAssets) {
    const normalizedAsset = normalizeRelatedAssetSelection(asset);
    if (normalizedAsset) {
      mergedAssets.set(normalizedAsset.key, normalizedAsset);
    }
  }
  return [...mergedAssets.values()];
}

export function removeRelatedAssetSelection(selectedAssets, key) {
  return selectedAssets.filter((asset) => asset.key !== key);
}

export function buildRelatedAssetSummary(displayNames, sourceLabel = "selected") {
  if (!Array.isArray(displayNames) || displayNames.length === 0) {
    return "";
  }

  const label =
    displayNames.length === 1 ? "1 related asset selected" : `${displayNames.length} related assets selected`;
  return `${label} ${sourceLabel}: ${displayNames.join(", ")}`;
}

function normalizeGltfResourcePath(uri) {
  const normalized = String(uri).trim().replaceAll("\\", "/");
  if (!normalized) {
    throw new Error("GLTF resource URI is invalid for folder matching.");
  }
  if (
    normalized.startsWith("/") ||
    /^[a-z]+:/i.test(normalized) ||
    normalized.split("/").some((part) => !part || part === "." || part === "..")
  ) {
    throw new Error("GLTF resource URI is invalid for folder matching.");
  }
  return normalized;
}

function normalizeRelatedAssetSelection(asset) {
  const displayName = String(asset?.displayName || asset?.file?.name || "").trim();
  const file = asset?.file || null;
  const source = asset?.source || "picker";
  const key = buildRelatedAssetSelectionKey({ displayName, file, source });
  if (!displayName || !file || !key) {
    return null;
  }
  return { displayName, file, key, source };
}
