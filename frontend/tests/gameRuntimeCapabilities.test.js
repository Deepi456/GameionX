import assert from 'node:assert/strict'
import test from 'node:test'
import {
  getRuntimeCapability,
  resolveRuntimeCapabilities,
  runRuntimeCapability,
} from '../src/gameRuntime/capabilityRegistry.js'

const sharedCapabilities = [
  'collectibles',
  'enemy_contact',
  'entity_creation',
  'goal_interaction',
  'target_scoring',
  'scoring',
  'timer',
]

const expectedCapabilities = {
  platform_adventure: [...sharedCapabilities, 'platform_movement', 'gravity'],
  top_down_collection: [...sharedCapabilities, 'four_direction_movement'],
  maze: [...sharedCapabilities, 'four_direction_movement'],
  dodge_arena: [...sharedCapabilities, 'four_direction_movement'],
  collection_challenge: [...sharedCapabilities, 'four_direction_movement'],
  target_arcade: [...sharedCapabilities, 'four_direction_movement'],
  flappy_dodge: [...sharedCapabilities, 'flapping', 'gravity', 'scrolling_obstacles'],
  space_shooter: [...sharedCapabilities, 'shooter_movement', 'projectiles', 'enemy_waves', 'shooter_scoring'],
  racing: [...sharedCapabilities, 'racing_movement', 'racing_obstacles'],
}

test('registry resolves each existing game type to implemented capabilities', () => {
  for (const [gameType, expected] of Object.entries(expectedCapabilities)) {
    const capabilities = resolveRuntimeCapabilities(gameType)
    assert.deepEqual(
      capabilities.map(({ id }) => id).sort(),
      [...expected].sort(),
      gameType,
    )
    for (const capability of capabilities) {
      assert.equal(typeof capability.implementation, 'function', `${gameType}.${capability.id}`)
      assert.ok(Array.isArray(capability.requiredParameters))
    }
  }
})

test('registry rejects unknown game types instead of selecting a fallback runtime', () => {
  assert.throws(
    () => resolveRuntimeCapabilities('unimplemented_genre'),
    /Unsupported runtime game type/,
  )
})

test('platform movement preserves left, right, idle, and grounded jump behavior', () => {
  const velocity = { x: 0, y: 0 }
  const scene = {
    spec: { game_type: 'platform_adventure', player: { speed: 240, jump_velocity: 450 } },
    player: {
      body: {
        blocked: { down: true },
        setVelocityX(value) { velocity.x = value },
        setVelocityY(value) { velocity.y = value },
      },
    },
  }

  assert.equal(runRuntimeCapability(scene, 'platform_movement', {
    left: true, right: false, jump: true,
  }), true)
  assert.deepEqual(velocity, { x: -240, y: -450 })
  runRuntimeCapability(scene, 'platform_movement', {
    left: false, right: true, jump: false,
  })
  assert.deepEqual(velocity, { x: 240, y: -450 })
  runRuntimeCapability(scene, 'platform_movement', {
    left: false, right: false, jump: false,
  })
  assert.equal(velocity.x, 0)
})

test('shared four-direction movement and registry scoring keep runtime behavior data-driven', () => {
  const velocity = { x: 0, y: 0 }
  const scene = {
    spec: { game_type: 'maze', player: { speed: 200 } },
    player: { body: {
      setVelocityX(value) { velocity.x = value },
      setVelocityY(value) { velocity.y = value },
    } },
    score: 0,
  }

  assert.equal(runRuntimeCapability(scene, 'four_direction_movement', {
    left: false, right: true, up: true, down: false,
  }), true)
  assert.deepEqual(velocity, { x: 200, y: -200 })
  runRuntimeCapability(scene, 'scoring', 25)
  assert.equal(scene.score, 25)
  assert.deepEqual(
    getRuntimeCapability('space_shooter', 'projectiles').requiredParameters,
    ['fire_control', 'player.fire_cooldown'],
  )
})
