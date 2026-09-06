import { MapViewer } from "./map-viewer/viewer.js";

export function createMapViewer(container, options = {}) {
  return new MapViewer(container, options);
}
