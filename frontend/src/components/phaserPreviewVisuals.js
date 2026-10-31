export const DEFAULT_PALETTE = {
  background: '#172033',
  primary: '#61d7b0',
  secondary: '#8b7cff',
  accent: '#f3c969',
  player: '#ffd166',
  enemy: '#ef476f',
  obstacle: '#8d99ae',
  collectible: '#a78bfa',
  wall: '#64748b',
}

export function getScenePalette(spec) {
  return { ...DEFAULT_PALETTE, ...(spec.palette || {}) }
}

export function getSceneTheme(spec) {
  return spec.theme || 'neon'
}

export function getSceneBackground(spec, level) {
  return level?.background || getScenePalette(spec).background
}

export function colorFromHex(value) {
  if (typeof value !== 'string' || !/^#[0-9a-f]{6}$/i.test(value)) {
    return 0x172033
  }
  return Number.parseInt(value.slice(1), 16)
}

export function mapLevelEntities(level, worldWidth, worldHeight) {
  const mapPoint = ({ x, y }) => ({ x: x * worldWidth, y: y * worldHeight })
  const mapBounds = (position, width, height) =>
    normalizedBounds(position, width, height, worldWidth, worldHeight)

  return {
    platforms: level.platforms.map((platform) => ({
      ...mapBounds({ x: platform.x, y: platform.y }, platform.width, platform.height),
      movement: platform.movement,
    })),
    collectibles: level.collectibles.map((item) => ({
      ...mapPoint(item.position),
      kind: item.kind,
      value: item.value,
    })),
    enemies: level.enemies.map((enemy) => ({
      ...mapPoint(enemy.position),
      damage: enemy.damage,
      movement: enemy.movement,
    })),
    goal: level.goal ? { ...mapPoint(level.goal.position), kind: level.goal.kind } : null,
    obstacles: (level.obstacles || []).map((item) => ({
      ...mapBounds(item.position, item.width, item.height),
      kind: item.kind,
      damage: item.damage,
      movement: item.movement,
      speed: item.speed,
      normalizedY: item.position.y,
    })),
    hazards: (level.hazards || []).map((item) => ({
      ...mapBounds(item.position, item.width, item.height),
      kind: item.kind,
      damage: item.damage,
      health: item.health,
      movement: item.movement,
      speed: item.speed,
    })),
    scenery: (level.scenery || []).map((item) => ({
      ...mapBounds(item.position, item.width, item.height),
      kind: item.kind,
      solid: item.solid,
    })),
    walls: (level.walls || []).map((item) => ({
      ...mapBounds(item.position, item.width, item.height),
    })),
    targets: (level.targets || []).map((item) => ({
      ...mapPoint(item.position),
      points: item.points,
    })),
  }
}

export function getReachableMazePositions(start, walls, worldWidth, worldHeight) {
  const step = 16
  const playerHalfWidth = 15
  const playerHalfHeight = 19
  const columns = Math.floor((worldWidth - playerHalfWidth * 2) / step) + 1
  const rows = Math.floor((worldHeight - playerHalfHeight * 2) / step) + 1
  const positionAt = (column, row) => ({
    x: playerHalfWidth + column * step,
    y: playerHalfHeight + row * step,
  })
  const isWalkable = ({ x, y }) => walls.every((wall) => {
    const left = wall.x - wall.width / 2
    const right = wall.x + wall.width / 2
    const top = wall.y - wall.height / 2
    const bottom = wall.y + wall.height / 2
    return !(
      x + playerHalfWidth > left &&
      x - playerHalfWidth < right &&
      y + playerHalfHeight > top &&
      y - playerHalfHeight < bottom
    )
  })
  const nodes = new Map()
  for (let row = 0; row < rows; row += 1) {
    for (let column = 0; column < columns; column += 1) {
      const position = positionAt(column, row)
      if (isWalkable(position)) nodes.set(`${column},${row}`, position)
    }
  }

  const startNode = [...nodes.entries()]
    .map(([key, position]) => ({
      key,
      distance: (position.x - start.x) ** 2 + (position.y - start.y) ** 2,
    }))
    .sort((left, right) => left.distance - right.distance)[0]
  if (!startNode) return []

  const reachable = new Set([startNode.key])
  const queue = [startNode.key]
  for (let index = 0; index < queue.length; index += 1) {
    const [column, row] = queue[index].split(',').map(Number)
    for (const [nextColumn, nextRow] of [
      [column - 1, row],
      [column + 1, row],
      [column, row - 1],
      [column, row + 1],
    ]) {
      const key = `${nextColumn},${nextRow}`
      if (nodes.has(key) && !reachable.has(key)) {
        reachable.add(key)
        queue.push(key)
      }
    }
  }
  return [...reachable].map((key) => nodes.get(key))
}

export function placeMazeCollectibles(
  collectibles,
  walls,
  start,
  worldWidth,
  worldHeight,
  goal,
) {
  const reachable = getReachableMazePositions(
    start,
    walls,
    worldWidth,
    worldHeight,
  )
  const placed = []
  return collectibles.map((collectible) => {
    const position = reachable
      .filter(({ x, y }) =>
        !goal || Math.hypot(x - goal.x, y - goal.y) >= 40
      )
      .filter(({ x, y }) =>
        Math.abs(x - worldWidth / 2) >= worldWidth * 0.21 ||
        Math.abs(y - worldHeight / 2) >= 44
      )
      .filter(({ x, y }) =>
        placed.every((previous) => Math.hypot(x - previous.x, y - previous.y) >= 32)
      )
      .sort((left, right) =>
        ((left.x - collectible.x) ** 2 + (left.y - collectible.y) ** 2) -
        ((right.x - collectible.x) ** 2 + (right.y - collectible.y) ** 2)
      )
    const selected = position[0] || collectible
    placed.push(selected)
    return { ...collectible, x: selected.x, y: selected.y }
  })
}

export function normalizedBounds(position, width, height, canvasWidth, canvasHeight) {
  return {
    x: position.x * canvasWidth + (width * canvasWidth) / 2,
    y: position.y * canvasHeight + (height * canvasHeight) / 2,
    width: width * canvasWidth,
    height: height * canvasHeight,
  }
}
