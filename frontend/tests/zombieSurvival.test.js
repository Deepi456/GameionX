import assert from 'node:assert/strict'
import test from 'node:test'
import {
  aimDirectionFromInput,
  shooterVelocity,
  survivalObjectiveComplete,
} from '../src/components/phaserPreviewRules.js'
import { runtimeConfigFromDefinition } from '../src/gameRuntime/gameDefinitionAdapter.js'
import { updateChasingEnemies } from '../src/gameRuntime/mechanics/enemyChase.js'

const zombieDefinition = {
  title: 'Last Block',
  genre: 'dodge_arena',
  theme: 'neon',
  camera: 'top_down',
  palette: { background: '#242b30', enemy: '#86a86e' },
  world_width: 800,
  world_height: 480,
  player: {
    kind: 'explorer',
    position: { x: 0.5, y: 0.55 },
    properties: {
      appearance: 'explorer',
      movement_mode: 'four_direction',
      speed: 220,
      max_health: 5,
      gravity: 0,
      fire_cooldown: 300,
    },
  },
  controls: [{ action: 'fire', keys: ['Space'] }],
  physics: { gravity_y: 0 },
  mechanics: [
    { kind: 'four_direction_movement', support: 'supported' },
    { kind: 'projectiles', support: 'supported' },
    { kind: 'chasing_enemies', support: 'supported' },
  ],
  levels: [{
    id: 'level-1',
    name: 'City',
    background: '#242b30',
    time_limit: 45,
    objects: [
      {
        id: 'zombie-1',
        kind: 'zombie',
        position: { x: 0.25, y: 0.3 },
        size: { width: 0.05, height: 0.06 },
        visual: { shape: 'hazard', color: '#86a86e' },
        properties: { health: 2, movement: 'chasing', speed: 62, damage: 1 },
      },
      {
        id: 'building-1',
        kind: 'building',
        position: { x: 0.2, y: 0.2 },
        size: { width: 0.2, height: 0.2 },
        visual: { shape: 'scenery', color: '#69756a' },
        solid: false,
      },
    ],
  }],
  win: { condition: 'survive', target: 45, requires: [] },
  lose: { condition: 'health_depleted', target: null, requires: [] },
  ui: {
    show_score: true,
    show_health: true,
    show_timer: true,
    objective_label: 'Survive',
    instructions: 'Move and shoot.',
    start_prompt: 'Press SPACE to start',
    victory_message: 'You survived!',
    game_over_message: 'Game over',
  },
}

test('V2 definition drives the runtime entities, controls, mechanics, and objectives', () => {
  const runtime = runtimeConfigFromDefinition(zombieDefinition, {})
  const level = runtime.levels[0]

  assert.equal(runtime.player.appearance, 'explorer')
  assert.equal(runtime.player.movement_mode, 'four_direction')
  assert.equal(runtime.fire_control, 'space')
  assert.deepEqual(runtime.runtime_mechanics, [
    'four_direction_movement',
    'projectiles',
    'chasing_enemies',
  ])
  assert.equal(runtime.win_condition, 'survive')
  assert.equal(runtime.win_target, 45)
  assert.equal(level.hazards[0].kind, 'zombie')
  assert.equal(level.hazards[0].movement, 'chasing')
  assert.equal(level.scenery[0].kind, 'building')
  assert.equal(runtime.ui.instructions, 'Move and shoot.')
})

test('chasing enemies move toward the player at authored speed', () => {
  const velocity = { x: 0, y: 0 }
  const zombie = {
    x: 10,
    y: 20,
    speed: 50,
    movement: 'chasing',
    active: true,
    body: {
      setVelocity(x, y) {
        velocity.x = x
        velocity.y = y
      },
    },
  }
  const scene = {
    state: 'playing',
    player: { x: 40, y: 60, active: true },
    hazards: { children: { iterate(callback) { callback(zombie) } } },
  }

  updateChasingEnemies(scene)
  assert.ok(velocity.x > 0)
  assert.ok(velocity.y > 0)
  assert.ok(Math.abs(Math.hypot(velocity.x, velocity.y) - 50) < 1e-10)

  scene.state = 'ready'
  velocity.x = 0
  velocity.y = 0
  updateChasingEnemies(scene)
  assert.deepEqual(velocity, { x: 0, y: 0 })
})

test('legacy runtime data remains the fallback when no V2 definition is present', () => {
  const legacyConfig = { game_type: 'platform_adventure', levels: [] }
  assert.equal(runtimeConfigFromDefinition(null, legacyConfig), legacyConfig)
})

test('top-down shooting supports four-way movement, directional aim, and survival timing', () => {
  assert.deepEqual(shooterVelocity(true, false, false, false, 220), { x: -220, y: 0 })
  assert.deepEqual(
    aimDirectionFromInput({ left: false, right: true, up: false, down: false }),
    { x: 1, y: 0 },
  )
  assert.deepEqual(
    aimDirectionFromInput({ left: false, right: false, up: false, down: false }, { x: 1, y: 0 }),
    { x: 1, y: 0 },
  )
  assert.equal(survivalObjectiveComplete('playing', 45_000, 45, 'top_down_survival'), true)
})
