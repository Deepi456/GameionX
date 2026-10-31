export function isPlatformGame(gameType) {
  return gameType === 'platform_adventure'
}

export function canReachGoal(gameType, winCondition, collected, required = 0) {
  if (gameType === 'top_down_collection') {
    return winCondition.type === 'collect_target' &&
      Number.isInteger(winCondition.target) &&
      winCondition.target > 0 &&
      collected >= winCondition.target
  }
  if (gameType === 'platform_adventure' && winCondition.type === 'reach_goal') {
    return collected >= required
  }
  if (gameType === 'maze') {
    return collected >= (winCondition.target ?? required)
  }
  return winCondition.type === 'reach_goal'
}

export function winsOnCollection(gameType, winCondition, collected) {
  return winCondition.type === 'collect_target' &&
    Number.isInteger(winCondition.target) &&
    winCondition.target > 0 &&
    collected >= winCondition.target &&
    gameType !== 'top_down_collection'
}

export function flappyScore(pipeRightX, birdLeftX, passed) {
  if (passed || pipeRightX >= birdLeftX) return { passed, scoreDelta: 0 }
  return { passed: true, scoreDelta: 1 }
}

export function safeFlapImpulse(
  gravity,
  flapStrength,
  playerY,
  ceilingY,
  playerHalfHeight,
  safetyMargin = 8,
) {
  const availableAscent = Math.max(
    0,
    playerY - ceilingY - playerHalfHeight - safetyMargin,
  )
  return Math.min(flapStrength, Math.sqrt(2 * gravity * availableAscent))
}

export function flappyPipeCollision(player, pair) {
  const horizontalOverlap = player.right > pair.leftX &&
    player.left < pair.leftX + pair.width
  const outsideGap = player.top < pair.gapTop || player.bottom > pair.gapBottom
  return horizontalOverlap && outsideGap
}

export function createFlappyPipePair(leftX, gapCenter, gapSize, width, worldHeight, speed = 180) {
  const boundedGapSize = Math.min(gapSize, worldHeight - 96)
  const gapTop = Math.max(
    48,
    Math.min(gapCenter - boundedGapSize / 2, worldHeight - boundedGapSize - 48),
  )
  const gapBottom = gapTop + boundedGapSize
  const topHeight = gapTop
  const bottomHeight = worldHeight - gapBottom
  const x = leftX + width / 2

  return {
    leftX,
    gapTop,
    gapBottom,
    width,
    worldHeight,
    speed,
    top: { x, y: topHeight / 2, width, height: topHeight },
    bottom: {
      x,
      y: gapBottom + bottomHeight / 2,
      width,
      height: bottomHeight,
    },
  }
}

export function moveFlappyPipePair(pair, deltaSeconds) {
  return createFlappyPipePair(
    pair.leftX - pair.speed * deltaSeconds,
    (pair.gapTop + pair.gapBottom) / 2,
    pair.gapBottom - pair.gapTop,
    pair.width,
    pair.worldHeight,
    pair.speed,
  )
}

export function projectileDamage(hazardHealth, damage) {
  const remainingHealth = Math.max(0, hazardHealth - damage)
  return {
    remainingHealth,
    destroyed: remainingHealth === 0,
  }
}

export function rectanglesOverlap(first, second) {
  return Math.abs(first.x - second.x) * 2 < first.width + second.width &&
    Math.abs(first.y - second.y) * 2 < first.height + second.height
}

export function shooterVelocity(left, right, up, down, speed) {
  const horizontal = Number(right) - Number(left)
  const vertical = Number(down) - Number(up)
  const magnitude = Math.hypot(horizontal, vertical) || 1
  return {
    x: (horizontal / magnitude) * speed,
    y: (vertical / magnitude) * speed,
  }
}

export function aimDirectionFromInput(input, current = { x: 0, y: -1 }) {
  if (input.left) return { x: -1, y: 0 }
  if (input.right) return { x: 1, y: 0 }
  if (input.up) return { x: 0, y: -1 }
  if (input.down) return { x: 0, y: 1 }
  return current
}

export function racingCollisionDamage(currentHealth, damage) {
  return Math.max(0, currentHealth - damage)
}

export function racingPlayerBounds(worldWidth, playerWidth, roadLeft = 0.25, roadRight = 0.75) {
  return {
    minX: worldWidth * roadLeft + playerWidth / 2,
    maxX: worldWidth * roadRight - playerWidth / 2,
  }
}

export function racingBoundaryContact(playerX, direction, bounds, tolerance = 1) {
  return (direction < 0 && playerX <= bounds.minX + tolerance) ||
    (direction > 0 && playerX >= bounds.maxX - tolerance)
}

export function nextRacingPlayerX(playerX, direction, speed, deltaMs, bounds) {
  const nextX = Math.max(
    bounds.minX,
    Math.min(
      bounds.maxX,
      playerX + direction * speed * Math.max(0, deltaMs) / 1000,
    ),
  )
  return {
    x: nextX,
    boundaryContact: racingBoundaryContact(nextX, direction, bounds),
  }
}

export function advanceRacingProgress(elapsedMs, deltaMs, totalSeconds, totalLaps = 3) {
  const totalDurationMs = totalSeconds * 1000
  const nextElapsedMs = Math.min(
    totalDurationMs,
    elapsedMs + Math.max(0, deltaMs),
  )
  const lapDurationMs = totalDurationMs / totalLaps
  const lapsCompleted = Math.min(totalLaps, Math.floor(nextElapsedMs / lapDurationMs))
  return {
    elapsedMs: nextElapsedMs,
    lapsCompleted,
    currentLap: Math.min(totalLaps, lapsCompleted + 1),
    lapProgress: nextElapsedMs >= totalDurationMs
      ? 1
      : (nextElapsedMs % lapDurationMs) / lapDurationMs,
    complete: nextElapsedMs >= totalDurationMs,
  }
}

export function startFlappyGame(state, flapPressed) {
  return state === 'ready' && flapPressed ? 'playing' : state
}

export function survivalObjectiveComplete(state, elapsedMs, targetSeconds, gameType) {
  return gameType !== 'flappy_dodge' &&
    state === 'playing' &&
    elapsedMs >= targetSeconds * 1000
}

export function initialGameState() {
  return 'ready'
}

export function startGame(state, validInput) {
  return state === 'ready' && validInput ? 'playing' : state
}

export function mayResolveGameplay(state) {
  return state === 'playing'
}

export function canTakeDamage(now, nextDamageAt) {
  return now >= nextDamageAt
}

export function mayArmGoal(playerX, playerY, startX, startY, threshold = 0.06) {
  return Math.hypot(playerX - startX, playerY - startY) > threshold
}

export function defeatTargetReached(gameType, winCondition, defeated) {
  return gameType === 'space_shooter' &&
    winCondition.type === 'defeat_target' &&
    Number.isInteger(winCondition.target) &&
    winCondition.target > 0 &&
    defeated >= winCondition.target
}

export function flappyCollisionEndsGame(gameType, state) {
  return gameType === 'flappy_dodge' && state === 'playing'
}
