export function gazeToSector(gaze, gridSize) {
  const col = Math.min(gridSize - 1, Math.floor(gaze.x_norm * gridSize));
  const row = Math.min(gridSize - 1, Math.floor(gaze.y_norm * gridSize));
  return { row, col };
}

export function sectorToNormCenter(sector, gridSize) {
  return {
    x_norm: (sector.col + 0.5) / gridSize,
    y_norm: (sector.row + 0.5) / gridSize,
  };
}

export function sectorName(sector) {
  const rowNames = ["T", "M", "B"];
  const colNames = ["L", "C", "R"];
  return `${rowNames[sector.row]}${colNames[sector.col]}`;
}

// Center sector maps to a RANDOM corner so changes land in peripheral vision.
// Other sectors mirror through the grid centre.
export function getOppositeSector(sector, gridSize) {
  const mid = (gridSize - 1) / 2;
  if (sector.row === mid && sector.col === mid) {
    const corners = [
      { row: 0, col: 0 },
      { row: 0, col: gridSize - 1 },
      { row: gridSize - 1, col: 0 },
      { row: gridSize - 1, col: gridSize - 1 },
    ];
    return corners[Math.floor(Math.random() * corners.length)];
  }
  return {
    row: (gridSize - 1) - sector.row,
    col: (gridSize - 1) - sector.col,
  };
}
