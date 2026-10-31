import assert from 'node:assert/strict'
import test from 'node:test'
import {
  colorFromHex,
  getSceneBackground,
  getScenePalette,
  getSceneTheme,
  getReachableMazePositions,
  mapLevelEntities,
  normalizedBounds,
  placeMazeCollectibles,
} from '../src/components/phaserPreviewVisuals.js'

test('scene palette preserves validated colors and supports legacy runtime data', () => {
  assert.equal(getScenePalette({ palette: { background: '#79c9f2' } }).background, '#79c9f2')
  assert.equal(getScenePalette({}).player, '#ffd166')
  assert.equal(colorFromHex('#79c9f2'), 0x79c9f2)
  assert.equal(colorFromHex('not-a-color'), 0x172033)
})

test('entity rendering geometry is derived from normalized GameSpec coordinates', () => {
  assert.deepEqual(
    normalizedBounds({ x: 0.25, y: 0.5 }, 0.1, 0.2, 800, 480),
    { x: 240, y: 288, width: 80, height: 96 },
  )
})

test('runtime scene mapping preserves theme, backgrounds, entity counts, and positions', () => {
  const spec = {
    theme: 'forest',
    palette: { background: '#a8d889' },
  }
  const level = {
    background: '#bbeba2',
    platforms: [{ x: 0.1, y: 0.8, width: 0.4, height: 0.08, movement: 'static' }],
    collectibles: [
      { position: { x: 0.2, y: 0.7 }, kind: 'coin', value: 10 },
      { position: { x: 0.4, y: 0.7 }, kind: 'coin', value: 10 },
    ],
    enemies: [{ position: { x: 0.7, y: 0.6 }, damage: 1, movement: 'horizontal' }],
    goal: { position: { x: 0.9, y: 0.2 }, kind: 'exit' },
    scenery: [{ position: { x: 0.5, y: 0.3 }, width: 0.1, height: 0.2, kind: 'tree', solid: false }],
    obstacles: [],
  }

  const mapped = mapLevelEntities(level, 1200, 720)

  assert.equal(getSceneTheme(spec), 'forest')
  assert.equal(getSceneBackground(spec, level), '#bbeba2')
  assert.equal(mapped.platforms.length, 1)
  assert.equal(mapped.collectibles.length, 2)
  assert.deepEqual(mapped.collectibles.map(({ x, y }) => ({ x: Math.round(x), y: Math.round(y) })), [
    { x: 240, y: 504 },
    { x: 480, y: 504 },
  ])
  assert.deepEqual(mapped.enemies[0], {
    x: 840,
    y: 432,
    damage: 1,
    movement: 'horizontal',
  })
  assert.deepEqual(mapped.goal, { x: 1080, y: 144, kind: 'exit' })
  assert.equal(mapped.scenery[0].x, 660)
})

test('maze mapping preserves all collectible positions and obstacle body dimensions', () => {
  const level = {
    platforms: [],
    collectibles: [
      { position: { x: 0.2, y: 0.6 }, kind: 'key', value: 10 },
      { position: { x: 0.57, y: 0.44 }, kind: 'key', value: 10 },
      { position: { x: 0.82, y: 0.64 }, kind: 'key', value: 10 },
    ],
    enemies: [],
    goal: { position: { x: 0.9, y: 0.85 }, kind: 'exit' },
    obstacles: [
      {
        position: { x: 0.4, y: 0.4 },
        width: 0.05,
        height: 0.05,
        kind: 'barrier',
      },
    ],
    hazards: [],
    walls: [
      { position: { x: 0.25, y: 0.15 }, width: 0.05, height: 0.55 },
    ],
    scenery: [],
    targets: [],
  }

  const mapped = mapLevelEntities(level, 800, 480)

  assert.deepEqual(
    mapped.collectibles.map(({ x, y }) => ({
      x: Number(x.toFixed(1)),
      y: Number(y.toFixed(1)),
    })),
    [{ x: 160, y: 288 }, { x: 456, y: 211.2 }, { x: 656, y: 307.2 }],
  )
  assert.deepEqual(mapped.walls[0], {
    x: 220,
    y: 204,
    width: 40,
    height: 264,
  })
  assert.deepEqual(mapped.obstacles[0], {
    x: 340,
    y: 204,
    width: 40,
    height: 24,
    kind: 'barrier',
    damage: undefined,
    movement: undefined,
    speed: undefined,
    normalizedY: 0.4,
  })
  assert.deepEqual(mapped.goal, { x: 720, y: 408, kind: 'exit' })
})

test('maze pickups stay visible, avoid walls, and remain in the player reachable area', () => {
  const walls = [
    { x: 220, y: 204, width: 40, height: 264 },
    { x: 420, y: 276, width: 40, height: 264 },
    { x: 620, y: 204, width: 40, height: 264 },
  ]
  const collectibles = [
    { x: 192, y: 307.2, kind: 'coin', value: 10 },
    { x: 472, y: 230.4, kind: 'coin', value: 10 },
    { x: 664, y: 326.4, kind: 'coin', value: 10 },
  ]
  const start = { x: 80, y: 384 }
  const goal = { x: 720, y: 408 }
  const placed = placeMazeCollectibles(
    collectibles,
    walls,
    start,
    800,
    480,
    goal,
  )
  const reachable = getReachableMazePositions(start, walls, 800, 480)

  assert.equal(placed.length, 3)
  for (const item of placed) {
    assert.ok(item.x >= 15 && item.x <= 785)
    assert.ok(item.y >= 19 && item.y <= 461)
    assert.ok(reachable.some(({ x, y }) => x === item.x && y === item.y))
    assert.ok(
      Math.abs(item.x - 400) >= 168 || Math.abs(item.y - 240) >= 44,
      'collectibles should not be hidden by the centered READY prompt',
    )
    assert.ok(walls.every((wall) =>
      Math.abs(item.x - wall.x) >= wall.width / 2 + 12 ||
      Math.abs(item.y - wall.y) >= wall.height / 2 + 12
    ))
  }
  assert.notDeepEqual(placed[0], collectibles[0])
})
