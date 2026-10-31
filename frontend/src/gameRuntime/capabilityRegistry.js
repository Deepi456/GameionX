import {
  applyDamage,
  addScore,
  configureGravity,
  configureFlappyObstacles,
  createRuntimeEnemy,
  hitEnemy,
  holdFlappingPlayer,
  interactWithGoal,
  moveFourDirections,
  movePlatformer,
  moveRacer,
  moveShooter,
  resolveProjectileHit,
  startFlappyObstacles,
  startShooterWaves,
  triggerFlap,
  updateFlappyObstacles,
  updateGameTimer,
  updateRacingObstacles,
  updateShooterProjectiles,
} from './sceneMechanics.js'
import {
  collectItem,
  createCollectibles,
} from './mechanics/collectibles.js'
import { updateChasingEnemies } from './mechanics/enemyChase.js'

export const RUNTIME_CAPABILITIES = Object.freeze({
  platform_movement: {
    implementation: movePlatformer,
    requiredParameters: ['player.speed'],
  },
  jumping: {
    implementation: movePlatformer,
    requiredParameters: ['player.jump_velocity', 'player.grounded'],
  },
  four_direction_movement: {
    implementation: moveFourDirections,
    requiredParameters: ['player.speed'],
  },
  shooter_movement: {
    implementation: moveShooter,
    requiredParameters: ['player.speed'],
  },
  racing_movement: {
    implementation: moveRacer,
    requiredParameters: ['player.speed', 'world_width'],
  },
  gravity: {
    implementation: configureGravity,
    requiredParameters: ['player.gravity'],
  },
  flapping: {
    implementation: holdFlappingPlayer,
    startInputImplementation: triggerFlap,
    requiredParameters: ['player.gravity', 'player.flap_strength'],
  },
  collectibles: {
    implementation: collectItem,
    createImplementation: createCollectibles,
    requiredParameters: ['levels[].collectibles[]', 'win_condition'],
  },
  enemy_contact: {
    implementation: hitEnemy,
    damageImplementation: applyDamage,
    createImplementation: createRuntimeEnemy,
    requiredParameters: ['levels[].enemies[]', 'player.max_health'],
  },
  entity_creation: {
    implementation: createRuntimeEnemy,
    requiredParameters: ['levels[].enemies[]', 'palette.enemy'],
  },
  goal_interaction: {
    implementation: interactWithGoal,
    requiredParameters: ['levels[].goal', 'win_condition'],
  },
  scrolling_obstacles: {
    implementation: updateFlappyObstacles,
    startImplementation: startFlappyObstacles,
    configureImplementation: configureFlappyObstacles,
    requiredParameters: ['levels[].obstacles[]'],
  },
  projectiles: {
    implementation: updateShooterProjectiles,
    hitImplementation: resolveProjectileHit,
    requiredParameters: ['fire_control', 'player.fire_cooldown'],
  },
  enemy_waves: {
    implementation: startShooterWaves,
    requiredParameters: ['win_condition.target', 'levels[].hazards[]'],
  },
  chasing_enemies: {
    implementation: updateChasingEnemies,
    requiredParameters: ['levels[].hazards[].movement', 'player.position'],
  },
  racing_obstacles: {
    implementation: updateRacingObstacles,
    requiredParameters: ['levels[].obstacles[]'],
  },
  timer: {
    implementation: updateGameTimer,
    requiredParameters: ['win_condition.target', 'lose_condition.target'],
  },
  scoring: {
    implementation: addScore,
    requiredParameters: ['score_events[].points'],
  },
  target_scoring: {
    implementation: (scene, player, target) => scene.hitTarget(player, target),
    requiredParameters: ['levels[].targets[].points', 'win_condition'],
  },
  shooter_scoring: {
    implementation: resolveProjectileHit,
    requiredParameters: ['levels[].hazards[].health', 'win_condition.target'],
  },
})

const SHARED_CAPABILITIES = [
  'collectibles',
  'enemy_contact',
  'entity_creation',
  'goal_interaction',
  'target_scoring',
  'scoring',
  'timer',
]

const GAME_TYPE_CAPABILITIES = Object.freeze({
  platform_adventure: [...SHARED_CAPABILITIES, 'platform_movement', 'gravity'],
  top_down_collection: [...SHARED_CAPABILITIES, 'four_direction_movement'],
  maze: [...SHARED_CAPABILITIES, 'four_direction_movement'],
  dodge_arena: [...SHARED_CAPABILITIES, 'four_direction_movement'],
  collection_challenge: [...SHARED_CAPABILITIES, 'four_direction_movement'],
  target_arcade: [...SHARED_CAPABILITIES, 'four_direction_movement'],
  flappy_dodge: [
    ...SHARED_CAPABILITIES,
    'flapping',
    'gravity',
    'scrolling_obstacles',
  ],
  space_shooter: [
    ...SHARED_CAPABILITIES,
    'shooter_movement',
    'projectiles',
    'enemy_waves',
    'shooter_scoring',
  ],
  racing: [...SHARED_CAPABILITIES, 'racing_movement', 'racing_obstacles'],
})

export function resolveRuntimeCapabilities(gameType, mechanicIds) {
  const capabilityIds = mechanicIds?.length
    ? mechanicIds
    : GAME_TYPE_CAPABILITIES[gameType]
  if (!capabilityIds) {
    throw new Error(`Unsupported runtime game type: ${gameType}`)
  }
  return capabilityIds.map((id) => {
    const capability = RUNTIME_CAPABILITIES[id]
    if (!capability) throw new Error(`Unsupported runtime mechanic: ${id}`)
    return { id, ...capability }
  })
}

export function getRuntimeCapability(gameType, capabilityId) {
  return resolveRuntimeCapabilities(gameType)
    .find((capability) => capability.id === capabilityId)
}

export function runRuntimeCapability(scene, capabilityId, ...args) {
  const capability = scene.capabilityMap
    ? scene.capabilityMap.get(capabilityId)
    : getRuntimeCapability(scene.spec.game_type, capabilityId)
  if (!capability) return false
  capability.implementation(scene, ...args)
  return true
}

export function runRuntimeCapabilityHook(scene, capabilityId, hook, ...args) {
  const capability = scene.capabilityMap
    ? scene.capabilityMap.get(capabilityId)
    : getRuntimeCapability(scene.spec.game_type, capabilityId)
  const implementation = capability?.[hook]
  if (!implementation) return false
  implementation(scene, ...args)
  return true
}
