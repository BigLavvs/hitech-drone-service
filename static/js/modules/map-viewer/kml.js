export function parseKmlToGeoJson(kmlText) {
  const xml = new DOMParser().parseFromString(kmlText, "application/xml");
  if (xml.querySelector("parsererror")) {
    throw new Error("KML parsing failed.");
  }

  const placemarks = [...xml.getElementsByTagNameNS("*", "Placemark")];
  return {
    type: "FeatureCollection",
    features: placemarks.flatMap((placemark) => placemarkToFeatures(placemark)),
  };
}

function placemarkToFeatures(placemark) {
  const properties = {};
  const name = textContentByLocalName(placemark, "name");
  const description = textContentByLocalName(placemark, "description");
  if (name) {
    properties.name = name;
  }
  if (description) {
    properties.description = description;
  }

  const features = [];
  [...placemark.children].forEach((child) => {
    const localName = child.localName;
    if (localName === "Point") {
      const coordinates = parseCoordinateSequence(textContentByLocalName(child, "coordinates"));
      if (coordinates.length > 0) {
        features.push(featureFromGeometry({ type: "Point", coordinates: coordinates[0] }, properties));
      }
    }
    if (localName === "LineString") {
      const coordinates = parseCoordinateSequence(textContentByLocalName(child, "coordinates"));
      if (coordinates.length > 1) {
        features.push(featureFromGeometry({ type: "LineString", coordinates }, properties));
      }
    }
    if (localName === "Polygon") {
      const outer = [...child.getElementsByTagNameNS("*", "outerBoundaryIs")]
        .map((node) => firstLinearRing(node))
        .find((ring) => ring.length > 0);
      const inners = [...child.getElementsByTagNameNS("*", "innerBoundaryIs")]
        .map((node) => firstLinearRing(node))
        .filter((ring) => ring.length > 0);
      if (outer && outer.length > 3) {
        features.push(featureFromGeometry({ type: "Polygon", coordinates: [outer, ...inners] }, properties));
      }
    }
    if (localName === "MultiGeometry") {
      [...child.children].forEach((nestedChild) => {
        const nestedPlacemark = document.createElementNS(child.namespaceURI, "Placemark");
        nestedPlacemark.append(nestedChild.cloneNode(true));
        const nestedFeatures = placemarkToFeatures(nestedPlacemark).map((feature) => ({
          ...feature,
          properties,
        }));
        features.push(...nestedFeatures);
      });
    }
  });

  return features;
}

function firstLinearRing(node) {
  const ringNode = node.getElementsByTagNameNS("*", "LinearRing")[0];
  return parseCoordinateSequence(textContentByLocalName(ringNode, "coordinates"));
}

function parseCoordinateSequence(raw) {
  if (!raw) {
    return [];
  }
  return raw
    .trim()
    .split(/\s+/)
    .map((pair) => pair.split(",").slice(0, 2).map(Number))
    .filter((coords) => coords.length === 2 && coords.every(Number.isFinite));
}

function featureFromGeometry(geometry, properties) {
  return {
    type: "Feature",
    properties: { ...properties },
    geometry,
  };
}

function textContentByLocalName(node, localName) {
  if (!node) {
    return "";
  }
  const match = [...node.getElementsByTagNameNS("*", localName)][0];
  return match?.textContent?.trim() || "";
}
