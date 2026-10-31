import assert from 'node:assert/strict'
import test from 'node:test'
import { createCollectibles } from '../src/gameRuntime/mechanics/collectibles.js'
import { mapLevelEntities } from '../src/components/phaserPreviewVisuals.js'

function createScene(gameType, winCondition, collectibleTarget = 0) {
  const created = []
  let overlapCallback
  let goalLockUpdates = 0
  let statusUpdates = 0
  const group = {
    children: [],
    add(item) {
      this.children.push(item)
    },
  }
  const scene = {
    spec: {
      game_type: gameType,
      win_condition: winCondition,
    },
    palette: {
      collectible: '#a78bfa',
      accent: '#f3c969',
    },
    state: 'playing',
    finished: false,
    collected: 0,
    score: 0,
    goalUnlocked: false,
    physics: {
      add: {
        group: () => group,
        overlap(_player, _items, callback) {
          overlapCallback = callback
        },
      },
    },
    player: {},
    addStyledEntity(x, y, width, height, kind, color, accentColor) {
      const item = {
        x,
        y,
        width,
        height,
        kind,
        color,
        accentColor,
        visible: true,
        active: true,
        body: {
          allowGravity: true,
          setAllowGravity(value) {
            this.allowGravity = value
          },
        },
        destroy() {
          this.active = false
        },
      }
      created.push(item)
      return item
    },
    addScore(points) {
      this.score += points
    },
    updateGoalLock() {
      goalLockUpdates += 1
      this.goalUnlocked = this.collected >= collectibleTarget
    },
    updateStatus() {
      statusUpdates += 1
    },
    finish(message) {
      this.finished = true
      this.finishMessage = message
    },
  }

  return {
    scene,
    group,
    created,
    collect(player, item) {
      overlapCallback(player, item)
    },
    get goalLockUpdates() {
      return goalLockUpdates
    },
    get statusUpdates() {
      return statusUpdates
    },
  }
}

test('collectible mechanic creates visible, stationary items from mapped spec positions', () => {
  const runtime = createScene('platform_adventure', { type: 'collect_target', target: 5 })
  const level = {
    platforms: [],
    collectibles: Array.from({ length: 5 }, (_, index) => ({
      position: { x: 0.15 + index * 0.1, y: 0.7 - index * 0.04 },
      kind: index === 4 ? 'gem' : 'coin',
      value: index + 1,
    })),
    enemies: [],
    goal: null,
    obstacles: [],
    hazards: [],
    scenery: [],
    walls: [],
    targets: [],
  }
  const mapped = mapLevelEntities(level, 800, 480)
  const collectibles = mapped.collectibles.map(({ x, y, kind, value }) => ({
    x,
    y,
    kind,
    value,
  }))

  createCollectibles(runtime.scene, collectibles)

  assert.equal(runtime.scene.collectibles, runtime.group)
  assert.equal(runtime.created.length, 5)
  assert.deepEqual(
    runtime.created.map(({ x, y, kind, value, visible, body }) => ({
      x, y, kind, value, visible, allowGravity: body.allowGravity,
    })),
    collectibles.map((item) => ({
      ...item,
      visible: true,
      allowGravity: false,
    })),
  )
  assert.equal(typeof runtime.collect, 'function')
})

test('collectible collision updates score and count, then completes the configured objective', () => {
  const runtime = createScene('platform_adventure', { type: 'collect_target', target: 2 })
  createCollectibles(runtime.scene, [
    { x: 200, y: 300, kind: 'coin', value: 10 },
    { x: 400, y: 260, kind: 'gem', value: 25 },
  ])
  const [first, second] = runtime.group.children

  runtime.collect(runtime.scene.player, first)
  assert.equal(runtime.scene.collected, 1)
  assert.equal(runtime.scene.score, 10)
  assert.equal(first.active, false)
  assert.equal(runtime.scene.finished, false)

  runtime.collect(runtime.scene.player, second)
  runtime.collect(runtime.scene.player, second)
  assert.equal(runtime.scene.collected, 2)
  assert.equal(runtime.scene.score, 35)
  assert.equal(runtime.scene.finished, true)
  assert.equal(runtime.scene.finishMessage, 'You win!')
  assert.equal(runtime.goalLockUpdates, 2)
  assert.equal(runtime.statusUpdates, 2)
})

test('maze collectibles render as keys and unlock the goal without bypassing goal interaction', () => {
  const runtime = createScene('maze', { type: 'reach_goal' }, 3)
  createCollectibles(runtime.scene, [
    { x: 80, y: 80, kind: 'coin', value: 10 },
    { x: 240, y: 160, kind: 'coin', value: 10 },
    { x: 560, y: 360, kind: 'coin', value: 10 },
  ])

  assert.deepEqual(runtime.created.map(({ kind }) => kind), ['key', 'key', 'key'])
  for (const item of runtime.group.children) runtime.collect(runtime.scene.player, item)

  assert.equal(runtime.scene.collected, 3)
  assert.equal(runtime.scene.goalUnlocked, true)
  assert.equal(runtime.scene.finished, false)
})
