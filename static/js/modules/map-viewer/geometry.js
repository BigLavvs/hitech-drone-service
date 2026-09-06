export function normaliseBounds(rawBounds) {
  if (!Array.isArray(rawBounds) || rawBounds.length !== 4) {
    return null;
  }
  const [west, south, east, north] = rawBounds.map(Number);
  if (![west, south, east, north].every(Number.isFinite)) {
    return null;
  }
  return window.L.latLngBounds(
    window.L.latLng(south, west),
    window.L.latLng(north, east),
  );
}

export function clamp(value, min, max) {
  return Math.min(Math.max(value, min), max);
}
