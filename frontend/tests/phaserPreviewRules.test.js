import assert from 'node:assert/strict'
import test from 'node:test'
import {
  canReachGoal,
  canTakeDamage,
  createFlappyPipePair,
  defeatTargetReached,
  flappyCollisionEndsGame,
  flappyPipeCollision,
  flappyScore,
  initialGameState,
  isPlatformGame,
  mayArmGoal,
  mayResolveGameplay,
  moveFlappyPipePair,
  advanceRacingProgress,
  projectileDamage,
  racingBoundaryContact,
  racingCollisionDamage,
  racingPlayerBounds,
  nextRacingPlayerX,
  rectanglesOverlap,
  safeFlapImpulse,
  shooterVelocity,
  startGame,
  startFlappyGame,
  survivalObjectiveComplete,
  winsOnCollection,
} from '../src/components/phaserPreviewRules.js'

test('only platform adventures use gravity platform movement', () => {
  assert.equal(isPlatformGame('platform_adventure'), true)
  assert.equal(isPlatformGame('collection_challenge'), false)
  assert.equal(isPlatformGame('top_down_collection'), false)
})

test('goal collision cannot win before a top-down collection objective is complete', () => {
  const winCondition = { type: 'collect_target', target: 3 }
  assert.equal(canReachGoal('top_down_collection', winCondition, 0), false)
  assert.equal(canReachGoal('top_down_collection', winCondition, 3), true)
  assert.equal(canReachGoal('top_down_collection', { type: 'collect_target' }, 0), false)
  assert.equal(canReachGoal('top_down_collection', { type: 'collect_target', target: 0 }, 0), false)
})

test('maze exits remain locked until all configured keys are collected', () => {
  const winCondition = { type: 'reach_goal' }
  assert.equal(canReachGoal('maze', winCondition, 1, 3), false)
  assert.equal(canReachGoal('maze', winCondition, 3, 3), true)
})

test('goal collision only completes explicit reach-goal objectives', () => {
  assert.equal(canReachGoal('platform_adventure', { type: 'collect_target', target: 1 }, 1), false)
  assert.equal(canReachGoal('platform_adventure', { type: 'reach_goal' }, 4, 5), false)
  assert.equal(canReachGoal('platform_adventure', { type: 'reach_goal' }, 5, 5), true)
  assert.equal(canReachGoal('target_arcade', { type: 'collect_target', target: 3 }, 3), false)
  assert.equal(canReachGoal('maze', { type: 'reach_goal' }, 0), true)
})

test('collection victory is not awarded early or for top-down collection', () => {
  const winCondition = { type: 'collect_target', target: 3 }
  assert.equal(winsOnCollection('collection_challenge', winCondition, 2), false)
  assert.equal(winsOnCollection('collection_challenge', winCondition, 3), true)
  assert.equal(winsOnCollection('top_down_collection', winCondition, 3), false)
  assert.equal(winsOnCollection('platform_adventure', { type: 'collect_target' }, 1), false)
})

test('flappy scoring awards one point only after an obstacle is passed', () => {
  assert.deepEqual(flappyScore(120, 100, false), { passed: false, scoreDelta: 0 })
  assert.deepEqual(flappyScore(99, 100, false), { passed: true, scoreDelta: 1 })
  assert.deepEqual(flappyScore(70, 100, true), { passed: true, scoreDelta: 0 })
})

test('flappy pipe pairs fill the playfield and preserve their gap while scrolling', () => {
  const initial = createFlappyPipePair(500, 240, 150, 72, 480, 180)
  const moved = moveFlappyPipePair(initial, 0.5)

  assert.equal(initial.top.height, initial.gapTop)
  assert.equal(initial.bottom.height, 480 - initial.gapBottom)
  assert.equal(initial.top.x, initial.bottom.x)
  assert.equal(initial.gapBottom - initial.gapTop, 150)
  assert.equal(moved.leftX, 410)
  assert.equal(moved.top.x, initial.top.x - 90)
  assert.equal(moved.bottom.x, initial.bottom.x - 90)
  assert.equal(moved.top.y, initial.top.y)
  assert.equal(moved.bottom.y, initial.bottom.y)
  assert.equal(moved.gapBottom - moved.gapTop, 150)
})

test('flappy pipe collisions respect the paired gap and full pipe bounds', () => {
  const pair = createFlappyPipePair(500, 240, 150, 72, 480)

  assert.equal(flappyPipeCollision(
    { left: 510, right: 540, top: 190, bottom: 220 },
    pair,
  ), false)
  assert.equal(flappyPipeCollision(
    { left: 510, right: 540, top: 100, bottom: 130 },
    pair,
  ), true)
  assert.equal(flappyPipeCollision(
    { left: 400, right: 490, top: 100, bottom: 130 },
    pair,
  ), false)
})

test('flap impulse is capped to the bird clearance above the next pipe gap', () => {
  const impulse = safeFlapImpulse(700, 340, 230, 172.8, 13)

  assert.ok(impulse < 250)
  assert.ok(230 - (impulse ** 2 / (2 * 700)) - 13 > 172.8)
  assert.equal(safeFlapImpulse(700, 220, 300, 0, 13), 220)
})

test('flappy scoring is one-time and waits until the whole pipe clears the bird', () => {
  const beforeClear = flappyScore(120, 100, false)
  const cleared = flappyScore(99, 100, false)

  assert.deepEqual(beforeClear, { passed: false, scoreDelta: 0 })
  assert.deepEqual(cleared, { passed: true, scoreDelta: 1 })
  assert.deepEqual(flappyScore(90, 100, cleared.passed), {
    passed: true,
    scoreDelta: 0,
  })
})

test('projectile damage destroys an enemy only when health reaches zero', () => {
  assert.deepEqual(projectileDamage(2, 1), { remainingHealth: 1, destroyed: false })
  assert.deepEqual(projectileDamage(1, 1), { remainingHealth: 0, destroyed: true })
})

test('shooter projectile collision uses the rendered object bounds', () => {
  assert.equal(
    rectanglesOverlap(
      { x: 400, y: 150, width: 6, height: 20 },
      { x: 402, y: 162, width: 42, height: 34 },
    ),
    true,
  )
  assert.equal(
    rectanglesOverlap(
      { x: 400, y: 150, width: 6, height: 20 },
      { x: 450, y: 162, width: 42, height: 34 },
    ),
    false,
  )
})

test('shooter movement is responsive in four directions with balanced diagonals', () => {
  assert.deepEqual(shooterVelocity(true, false, false, false, 280), { x: -280, y: 0 })
  assert.deepEqual(shooterVelocity(false, true, false, false, 280), { x: 280, y: 0 })
  assert.deepEqual(shooterVelocity(false, false, true, false, 280), { x: 0, y: -280 })
  assert.deepEqual(shooterVelocity(false, false, false, true, 280), { x: 0, y: 280 })

  const diagonal = shooterVelocity(false, true, true, false, 280)
  assert.ok(Math.abs(Math.hypot(diagonal.x, diagonal.y) - 280) < 1e-9)
})

test('racing collision reduces health without going below zero', () => {
  assert.equal(racingCollisionDamage(3, 1), 2)
  assert.equal(racingCollisionDamage(1, 5), 0)
})

test('racing player is clamped to road bounds and receives boundary-contact events', () => {
  const bounds = racingPlayerBounds(800, 34)
  assert.deepEqual(bounds, { minX: 217, maxX: 583 })
  assert.equal(racingBoundaryContact(bounds.minX, -1, bounds), true)
  assert.equal(racingBoundaryContact(bounds.maxX, 1, bounds), true)
  assert.equal(racingBoundaryContact(bounds.minX + 20, -1, bounds), false)
  assert.equal(racingBoundaryContact(bounds.maxX - 20, 1, bounds), false)
})

test('racing steering moves continuously by elapsed time and clamps at road edges', () => {
  const bounds = racingPlayerBounds(800, 34)
  const movedRight = nextRacingPlayerX(400, 1, 260, 250, bounds)
  const movedLeft = nextRacingPlayerX(movedRight.x, -1, 260, 500, bounds)
  const hitRightEdge = nextRacingPlayerX(580, 1, 260, 100, bounds)

  assert.deepEqual(movedRight, { x: 465, boundaryContact: false })
  assert.deepEqual(movedLeft, { x: 335, boundaryContact: false })
  assert.deepEqual(hitRightEdge, { x: bounds.maxX, boundaryContact: true })
})

test('racing progress advances laps and finishes only at the configured race duration', () => {
  const firstLap = advanceRacingProgress(0, 10_000, 30, 3)
  const secondLap = advanceRacingProgress(firstLap.elapsedMs, 10_000, 30, 3)
  const finalLap = advanceRacingProgress(secondLap.elapsedMs, 9_999, 30, 3)
  const finish = advanceRacingProgress(finalLap.elapsedMs, 1, 30, 3)

  assert.deepEqual(
    [firstLap.currentLap, firstLap.lapsCompleted, firstLap.complete],
    [2, 1, false],
  )
  assert.deepEqual(
    [secondLap.currentLap, secondLap.lapsCompleted, secondLap.complete],
    [3, 2, false],
  )
  assert.equal(finalLap.complete, false)
  assert.equal(finish.complete, true)
  assert.equal(finish.lapsCompleted, 3)
  assert.equal(finish.lapProgress, 1)
})

test('flappy gameplay waits for a flap input before starting gravity and timer', () => {
  assert.equal(startFlappyGame('ready', false), 'ready')
  assert.equal(startFlappyGame('ready', true), 'playing')
  assert.equal(startFlappyGame('playing', true), 'playing')
})

test('survival victory requires the active game to reach its full timer', () => {
  assert.equal(survivalObjectiveComplete('ready', 60_000, 30), false)
  assert.equal(survivalObjectiveComplete('playing', 29_999, 30), false)
  assert.equal(survivalObjectiveComplete('playing', 30_000, 30), true)
  assert.equal(survivalObjectiveComplete('playing', 60_000, 30, 'flappy_dodge'), false)
})

test('all games start ready, require valid input, and reset to ready on restart', () => {
  const initial = initialGameState()
  assert.equal(initial, 'ready')
  assert.equal(startGame(initial, false), 'ready')
  assert.equal(startGame(initial, true), 'playing')
  assert.equal(startGame('won', true), 'won')
  assert.equal(initialGameState(), 'ready')
})

test('collisions cannot resolve objectives before gameplay starts', () => {
  assert.equal(mayResolveGameplay('ready'), false)
  assert.equal(mayResolveGameplay('playing'), true)
  assert.equal(flappyCollisionEndsGame('flappy_dodge', 'ready'), false)
  assert.equal(flappyCollisionEndsGame('flappy_dodge', 'playing'), true)
})

test('a shooter collision applies one hit per cooldown, not once per frame', () => {
  assert.equal(canTakeDamage(1000, 1000), true)
  assert.equal(canTakeDamage(1001, 2000), false)
  assert.equal(canTakeDamage(2000, 2000), true)
})

test('forest destination cannot arm at spawn and shooter target requires actual defeats', () => {
  assert.equal(mayArmGoal(0.1, 0.8, 0.1, 0.8), false)
  assert.equal(mayArmGoal(0.2, 0.8, 0.1, 0.8), true)
  const target = { type: 'defeat_target', target: 3 }
  assert.equal(defeatTargetReached('space_shooter', target, 0), false)
  assert.equal(defeatTargetReached('space_shooter', target, 2), false)
  assert.equal(defeatTargetReached('space_shooter', target, 3), true)
  assert.equal(defeatTargetReached('space_shooter', { type: 'survive', target: 1 }, 99), false)
})
