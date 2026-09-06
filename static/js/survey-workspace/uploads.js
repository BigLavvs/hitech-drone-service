import {
  ApiError,
  APPROVAL_ACTION_LABELS,
  ASSET_SOURCE_LABELS,
  NON_TERMINAL_JOB_STATUSES,
  PRIMARY_UPLOAD_EXTENSIONS,
  RELATED_ASSET_RULES,
  TERMINAL_JOB_STATUSES,
  api,
  appendRelatedAssetsToFormData,
  buildRelatedAssetSummary,
  getFileExtension,
  isUnauthorized,
  mergeRelatedAssetSelections,
  prettyRole,
  removeRelatedAssetSelection,
  selectReferencedGltfBundleAssets,
  startPolling,
} from "./dependencies.js";

export const uploadMethods = {
  handlePrimaryFileChange() {
    const file = this.primaryFileInput?.files?.[0];
    if (file && !PRIMARY_UPLOAD_EXTENSIONS.has(this.fileExtension(file.name))) {
      this.primaryFileInput.value = "";
      this.showMessage(this.filesMessage, "Unsupported primary file type selected.", "error");
    } else {
      if (file) {
        this.clearPrimaryDependentSelections();
      }
      this.clearMessage(this.filesMessage);
    }
    this.syncUploadAssetField();
  },

  handleAssetFileChange() {
    if (!this.assetFileInput?.files?.length) {
      return;
    }

    const assetRule = this.relatedAssetRule();
    if (!assetRule) {
      this.assetFileInput.value = "";
      this.clearSelectedAssets();
      this.showMessage(this.filesMessage, "Related assets are allowed only when the primary file is .obj or .gltf.", "error");
      this.syncUploadAssetField();
      return;
    }

    const invalidAsset = [...this.assetFileInput.files].find(
      (file) => !assetRule.extensions.has(this.fileExtension(file.name)),
    );
    if (invalidAsset) {
      this.assetFileInput.value = "";
      this.clearSelectedAssets();
      this.showMessage(this.filesMessage, `Unsupported ${assetRule.name} asset type selected.`, "error");
      this.renderAssetSelection();
      return;
    }

    this.mergeSelectedAssets(
      [...this.assetFileInput.files].map((file) => ({
        file,
        displayName: file.name,
        source: "picker",
      })),
    );
    this.assetFileInput.value = "";
    this.renderAssetSelection();
    this.clearMessage(this.filesMessage);
  },

  async handleGltfFolderChange() {
    if (!this.gltfFolderInput?.files?.length) {
      this.renderGltfFolderSelection();
      return;
    }

    const primaryFile = this.primaryFileInput?.files?.[0];
    if (!primaryFile || this.fileExtension(primaryFile.name) !== ".gltf") {
      this.gltfFolderInput.value = "";
      this.clearSelectedAssets();
      this.showMessage(this.filesMessage, "GLTF bundle folder selection is available only for a .gltf primary file.", "error");
      this.syncUploadAssetField();
      return;
    }

    const folderFiles = [...this.gltfFolderInput.files];

    try {
      const selection = selectReferencedGltfBundleAssets({
        manifestText: await primaryFile.text(),
        folderFiles,
      });
      this.removeSelectedAssetsBySource("folder");
      this.mergeSelectedAssets(
        selection.selectedAssets.map((asset) => ({
          file: asset.file,
          displayName: asset.relativePath,
          source: "folder",
        })),
      );

      if (selection.unsupportedReferences.length > 0 || selection.missingReferences.length > 0) {
        const issues = [];
        if (selection.unsupportedReferences.length > 0) {
          issues.push(
            `Unsupported GLTF folder references: ${selection.unsupportedReferences.join(", ")}`,
          );
        }
        if (selection.missingReferences.length > 0) {
          issues.push(`Missing referenced files in the chosen folder: ${selection.missingReferences.join(", ")}`);
        }
        this.showMessage(
          this.filesMessage,
          `${issues.join(". ")}. Use the fallback related-assets picker if needed. Server validation remains authoritative.`,
          "error",
        );
      } else {
        this.clearMessage(this.filesMessage);
      }
    } catch (error) {
      this.gltfFolderInput.value = "";
      this.removeSelectedAssetsBySource("folder");
      this.showMessage(
        this.filesMessage,
        `${error instanceof Error ? error.message : "The GLTF bundle folder could not be matched."} Use the fallback related-assets picker if needed.`,
        "error",
      );
    }

    this.renderGltfFolderSelection();
    this.renderAssetSelection();
  },

  syncUploadAssetField() {
    if (!this.assetField || !this.assetFileInput || !this.primaryFileInput) {
      return;
    }

    const assetRule = this.relatedAssetRule();
    const currentExtension = this.fileExtension(this.primaryFileInput?.files?.[0]?.name);
    const primaryExtensionChanged =
      Boolean(this.uploadPrimaryExtension) && this.uploadPrimaryExtension !== currentExtension;

    this.assetField.hidden = !assetRule;
    this.assetFileInput.disabled = !assetRule;
    if (this.assetFieldLabel) {
      this.assetFieldLabel.textContent = assetRule?.label || "Related assets";
    }
    if (this.assetFieldHelp) {
      this.assetFieldHelp.textContent = assetRule?.help || "";
    }
    if (assetRule) {
      this.assetFileInput.setAttribute("accept", assetRule.accept);
    } else {
      this.assetFileInput.removeAttribute("accept");
    }
    if (!assetRule || primaryExtensionChanged) {
      this.assetFileInput.value = "";
    }
    if (this.assetPickerHelp) {
      this.assetPickerHelp.textContent =
        assetRule && this.selectedAssets.length > 0 ? "Add related assets" : "Choose related assets";
    }
    if (this.gltfFolderField && this.gltfFolderInput) {
      const showGltfFolder = currentExtension === ".gltf";
      this.gltfFolderField.hidden = !showGltfFolder;
      this.gltfFolderInput.disabled = !showGltfFolder;
      if (!showGltfFolder || primaryExtensionChanged) {
        this.gltfFolderInput.value = "";
      }
    }
    if (!assetRule || primaryExtensionChanged) {
      this.clearSelectedAssets();
    }
    this.uploadPrimaryExtension = assetRule ? currentExtension : "";
    this.renderPrimarySelection();
    this.renderGltfFolderSelection();
    this.renderAssetSelection();
  },

  renderPrimarySelection() {
    if (!this.primaryFileSelection || !this.primaryFileHelp) {
      return;
    }

    const primaryFile = this.primaryFileInput?.files?.[0];
    if (!primaryFile) {
      this.primaryFileHelp.textContent =
        "Choose one primary survey dataset file. Selecting another file replaces the current primary file.";
      this.primaryFileSelection.innerHTML = "";
      this.primaryFileSelection.hidden = true;
      return;
    }

    this.primaryFileHelp.textContent = "Choose or replace the primary survey dataset file.";
    this.primaryFileSelection.innerHTML = this.renderSelectionChip({
      label: primaryFile.name,
      removeAction: "remove-primary-file",
      removeLabel: `Remove primary file ${primaryFile.name}`,
    });
    this.primaryFileSelection.hidden = false;
  },

  renderAssetSelection() {
    if (!this.assetFieldSelection || !this.assetList) {
      return;
    }

    if (this.selectedAssets.length === 0) {
      this.assetFieldSelection.textContent = "";
      this.assetFieldSelection.hidden = true;
      this.assetList.innerHTML = "";
      this.assetList.hidden = true;
      if (this.assetPickerHelp) {
        this.assetPickerHelp.textContent = "Choose related assets";
      }
      return;
    }

    this.assetFieldSelection.textContent = buildRelatedAssetSummary(
      this.selectedAssetDisplayNames,
      this.selectedAssetSummaryLabel(),
    );
    this.assetFieldSelection.hidden = false;
    this.assetList.innerHTML = `<div class="survey-upload-chip-list">${this.selectedAssets
      .map((asset) =>
        this.renderSelectionChip({
          label: asset.displayName,
          removeAction: "remove-asset",
          removeValue: asset.key,
          removeLabel: `Remove related asset ${asset.displayName}`,
        }),
      )
      .join("")}</div>`;
    this.assetList.hidden = false;
    if (this.assetPickerHelp) {
      this.assetPickerHelp.textContent = "Add related assets";
    }
  },

  renderGltfFolderSelection() {
    if (!this.gltfFolderSelection) {
      return;
    }

    const folderAssetDisplayNames = this.selectedAssets
      .filter((asset) => asset.source === "folder")
      .map((asset) => asset.displayName);
    if (folderAssetDisplayNames.length === 0) {
      this.gltfFolderSelection.textContent = "";
      this.gltfFolderSelection.hidden = true;
      return;
    }

    this.gltfFolderSelection.textContent = buildRelatedAssetSummary(
      folderAssetDisplayNames,
      "from the selected GLTF bundle folder",
    );
    this.gltfFolderSelection.hidden = false;
  },

  relatedAssetRule() {
    const file = this.primaryFileInput?.files?.[0];
    return RELATED_ASSET_RULES[this.fileExtension(file?.name)] || null;
  },

  fileExtension(filename) {
    return getFileExtension(filename);
  },

  get selectedAssetFiles() {
    return this.selectedAssets.map((asset) => asset.file);
  },

  get selectedAssetDisplayNames() {
    return this.selectedAssets.map((asset) => asset.displayName);
  },

  clearSelectedAssets() {
    this.selectedAssets = [];
  },

  removeSelectedAssetsBySource(source) {
    this.selectedAssets = this.selectedAssets.filter((asset) => asset.source !== source);
  },

  mergeSelectedAssets(assets) {
    this.selectedAssets = mergeRelatedAssetSelections(this.selectedAssets, assets);
  },

  removeSelectedAssetByKey(key) {
    this.selectedAssets = removeRelatedAssetSelection(this.selectedAssets, key);
    this.renderGltfFolderSelection();
    this.renderAssetSelection();
  },

  selectedAssetSummaryLabel() {
    const sources = new Set(this.selectedAssets.map((asset) => asset.source));
    if (sources.size === 1) {
      return ASSET_SOURCE_LABELS[[...sources][0]];
    }
    return "from the selected folder and fallback file picker";
  },

  renderSelectionChip({ label, removeAction, removeValue = "", removeLabel }) {
    const valueAttribute = removeValue ? ` data-remove-value="${this.escapeHtml(removeValue)}"` : "";
    return `
      <div class="survey-upload-chip">
        <span class="survey-upload-chip__label">${this.escapeHtml(label)}</span>
        <button
          class="survey-upload-chip__remove"
          type="button"
          data-upload-action="${removeAction}"${valueAttribute}
          aria-label="${this.escapeHtml(removeLabel)}"
        >
          <span aria-hidden="true">×</span>
        </button>
      </div>
    `;
  },

  async handleUpload(event) {
    event.preventDefault();
    if (this.sessionBlocked) {
      return;
    }

    const primaryInput = this.primaryFileInput;
    const assetInput = this.assetFileInput;
    const primaryFile = primaryInput.files[0];
    if (!primaryFile) {
      this.showMessage(this.filesMessage, "Choose a primary file before uploading.", "error");
      return;
    }
    if (!PRIMARY_UPLOAD_EXTENSIONS.has(this.fileExtension(primaryFile.name))) {
      primaryInput.value = "";
      this.showMessage(this.filesMessage, "Unsupported primary file type selected.", "error");
      this.syncUploadAssetField();
      return;
    }
    const assetRule = this.relatedAssetRule();
    if (!assetRule && this.selectedAssetFiles.length > 0) {
      assetInput.value = "";
      this.clearSelectedAssets();
      this.showMessage(this.filesMessage, "Related assets are allowed only when the primary file is .obj or .gltf.", "error");
      this.syncUploadAssetField();
      return;
    }
    const invalidAsset = this.selectedAssetFiles.find(
      (file) => !assetRule?.extensions.has(this.fileExtension(file.name)),
    );
    if (invalidAsset) {
      assetInput.value = "";
      this.clearSelectedAssets();
      this.showMessage(this.filesMessage, `Unsupported ${assetRule.name} asset type selected.`, "error");
      this.renderAssetSelection();
      return;
    }

    const formData = new FormData();
    formData.append("file", primaryFile);
    appendRelatedAssetsToFormData(formData, this.selectedAssetFiles);

    this.uploadSubmit.disabled = true;
    this.clearMessage(this.filesMessage);

    try {
      await api.post(`/api/v1/surveys/${this.surveyId}/files`, formData);
      this.uploadForm.reset();
      this.clearSelectedAssets();
      this.syncUploadAssetField();
      this.showMessage(this.filesMessage, "Upload accepted. Processing status will update automatically.");
      await this.refreshAll();
    } catch (error) {
      if (isUnauthorized(error)) {
        await this.loadWorkspace();
        return;
      }
      this.showMessage(this.filesMessage, this.describeError(error), "error");
    } finally {
      this.uploadSubmit.disabled = false;
    }
  },

  async handleFilesClick(event) {
    const retryButton = event.target.closest("[data-retry-job-id]");
    if (!retryButton) {
      return;
    }

    await this.handleRetry(retryButton.dataset.retryJobId, retryButton);
  },

  handleUploadSelectionClick(event) {
    const uploadActionButton = event.target.closest("[data-upload-action]");
    if (!uploadActionButton) {
      return;
    }

    if (uploadActionButton.dataset.uploadAction === "remove-primary-file") {
      this.clearPrimarySelection();
      return;
    }
    if (uploadActionButton.dataset.uploadAction === "remove-asset") {
      this.removeSelectedAssetByKey(uploadActionButton.dataset.removeValue || "");
    }
  },

  clearPrimarySelection() {
    if (this.primaryFileInput) {
      this.primaryFileInput.value = "";
    }
    this.clearPrimaryDependentSelections();
    this.clearMessage(this.filesMessage);
    this.syncUploadAssetField();
  },

  clearPrimaryDependentSelections() {
    if (this.assetFileInput) {
      this.assetFileInput.value = "";
    }
    if (this.gltfFolderInput) {
      this.gltfFolderInput.value = "";
    }
    this.clearSelectedAssets();
  }
};
