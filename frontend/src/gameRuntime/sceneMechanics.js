import {
  flappyPipeCollision,
  flappyScore,
  moveFlappyPipePair,
  rectanglesOverlap,
  racingPlayerBounds,
  nextRacingPlayerX,
  advanceRacingProgress,
  safeFlapImpulse,
  shooterVelocity,
  survivalObjectiveComplete,
} from '../components/phaserPreviewRules.js'
import { colorFromHex } from '../components/phaserPreviewVisuals.js'

const HEIGHT = 480
const randomBetween = (minimum, maximum) =>
  Math.floor(Math.random() * (maximum - minimum + 1)) + minimum

export function configureGravity(scene, enabled) {
  scene.player.body.setAllowGravity(enabled)
}

export function movePlatformer(scene, { left, right, jump }) {
  if (left) scene.player.body.setVelocityX(-scene.spec.player.speed)
  else if (right) scene.player.body.setVelocityX(scene.spec.player.speed)
  else scene.player.body.setVelocityX(0)

  if (jump && scene.player.body.blocked.down) {
    scene.player.body.setVelocityY(-scene.spec.player.jump_velocity)
    scene.jumpCount = (scene.jumpCount || 0) + 1
  }
}

export function moveFourDirections(scene, { left, right, up, down }) {
  scene.player.body.setVelocityX(
    left ? -scene.spec.player.speed : right ? scene.spec.player.speed : 0,
  )
  scene.player.body.setVelocityY(
    up ? -scene.spec.player.speed : down ? scene.spec.player.speed : 0,
  )
}

export function moveShooter(scene, input) {
  const velocity = shooterVelocity(
    input.left,
    input.right,
    input.up,
    input.down,
    scene.spec.player.speed,
  )
  scene.player.body.setVelocity(velocity.x, velocity.y)
}

export function moveRacer(scene, { left, right }, delta = 0) {
  const bounds = racingPlayerBounds(scene.worldWidth, scene.player.width)
  const direction = Number(right) - Number(left)
  const movement = nextRacingPlayerX(
    scene.player.x,
    direction,
    scene.spec.player.speed * (scene.raceSpeedMultiplier || 1),
    delta,
    bounds,
  )
  scene.racingBoundaryContact = movement.boundaryContact
  scene.player.x = movement.x
  scene.player.body.setVelocity(0, 0)
  scene.player.body.updateFromGameObject()
}

export function holdFlappingPlayer(scene) {
  scene.player.body.setVelocityX(0)
}

export function startFlappyObstacles(scene) {
  const firstColumn = [...scene.obstacleColumns.values()][0]
  const pipeWidth = firstColumn?.width || 72
  scene.pipeSpawner = scene.time.addEvent({
    delay: 1500,
    loop: true,
    callback: () => scene.spawnFlappyPipePair(
      scene.worldWidth + pipeWidth,
      randomBetween(HEIGHT * 0.38, HEIGHT * 0.64),
      randomBetween(148, 176),
      pipeWidth,
    ),
  })
}

export function configureFlappyObstacles(scene, level) {
  const pipes = (level.obstacles || []).filter((item) => item.kind === 'pipe')
  const firstPipe = pipes[0]
  const pairX = firstPipe?.position.x ?? 0.68
  const upperPipe = pipes.find((pipe) => pipe.position.y < 0.5)
  const lowerPipe = pipes.find((pipe) => pipe.position.y >= 0.5)
  const topOfGap = upperPipe
    ? (upperPipe.position.y + upperPipe.height) * scene.worldHeight
    : scene.worldHeight * 0.36
  const bottomOfGap = lowerPipe
    ? lowerPipe.position.y * scene.worldHeight
    : topOfGap + 150
  const authoredGapSize = bottomOfGap - topOfGap
  const gapSize = authoredGapSize >= 120 && authoredGapSize <= 190
    ? authoredGapSize
    : 155
  const gapCenter = (topOfGap + bottomOfGap) / 2
  const pipeWidth = Math.max(
    52,
    (firstPipe?.width ?? 0.09) * scene.worldWidth,
  )

  scene.spawnFlappyPipePair(
    pairX * scene.worldWidth,
    gapCenter,
    gapSize,
    pipeWidth,
  )
  scene.hazards = scene.physics.add.group()
}

export function createRuntimeEnemy(scene, enemy) {
  if (scene.hasCapability('projectiles')) {
    return scene.createShooterHazard(
      enemy.x,
      enemy.y,
      34,
      32,
      {
        kind: 'enemy',
        damage: enemy.damage,
        health: 1,
        movement: enemy.movement,
        speed: 50,
      },
    )
  }
  const item = scene.addStyledEntity(
    enemy.x,
    enemy.y,
    34,
    32,
    'enemy',
    colorFromHex(scene.palette.enemy),
    colorFromHex(scene.palette.accent),
  )
  item.damage = enemy.damage
  item.movement = enemy.movement
  scene.enemies.add(item)
  item.body.setAllowGravity(false)
  return item
}

export function startShooterWaves(scene) {
  scene.hazards.children.iterate((hazard) => {
    if (hazard.movement === 'chasing') return
    if (hazard.movement === 'vertical') {
      hazard.body.setVelocityY(hazard.speed * (hazard.direction || 1))
    } else {
      hazard.body.setVelocityX(hazard.speed * (hazard.direction || 1))
    }
  })
  scene.enemySpawner = scene.time.addEvent({
    delay: 1100,
    loop: true,
    callback: () => {
      if (scene.hazards.countActive() < 5) scene.spawnShooterEnemy()
    },
  })
}

export function updateFlappyObstacles(scene, delta) {
  scene.obstacleColumns.forEach((column) => {
    if (scene.finished) return
    const moved = moveFlappyPipePair(column, delta / 1000)
    Object.assign(column, moved)
    scene.drawFlappyPipePair(column, moved)
    if (flappyPipeCollision({
      left: scene.player.x - scene.player.width / 2,
      right: scene.player.x + scene.player.width / 2,
      top: scene.player.y - scene.player.height / 2,
      bottom: scene.player.y + scene.player.height / 2,
    }, moved)) {
      scene.hitObstacle(scene.player, { damage: 1 })
      if (scene.finished) return
    }
    const scoring = flappyScore(
      moved.leftX + moved.width,
      scene.player.x - scene.player.width / 2,
      column.passed,
    )
    if (scoring.scoreDelta) {
      column.passed = true
      addScore(scene, scoring.scoreDelta)
    }
    if (moved.leftX + moved.width < 0) {
      column.visual.destroy()
      scene.obstacleColumns.delete(column)
    }
  })
}

export function updateRacingObstacles(scene, delta = 0) {
  scene.obstacles.children.iterate((obstacle) => {
    obstacle.body.setVelocityY(scene.state === 'playing' ? obstacle.speed || 140 : 0)
    if (obstacle.y > scene.worldHeight + obstacle.height / 2) {
      obstacle.y = -obstacle.height / 2
      obstacle.body.updateFromGameObject()
    }
  })
  scene.racingOpponents?.children.iterate((opponent) => {
    opponent.body.setVelocityY(
      scene.state === 'playing' ? opponent.racingSpeed : 0,
    )
    if (opponent.y > scene.worldHeight + opponent.height / 2) {
      scene.recycleRacingOpponent(opponent)
    }
  })
  if (scene.state !== 'playing') return

  const progress = advanceRacingProgress(
    scene.raceElapsedMs,
    delta,
    scene.raceDurationSeconds,
    scene.raceTotalLaps,
  )
  scene.raceElapsedMs = progress.elapsedMs
  scene.raceLapsCompleted = progress.lapsCompleted
  scene.raceCurrentLap = progress.currentLap
  scene.racingFinishLine.setY(
    scene.racingFinishLineStartY +
    progress.lapProgress * (scene.player.y - scene.racingFinishLineStartY),
  )
  if (progress.complete) scene.finish('You win!')
}

export function updateShooterProjectiles(scene) {
  for (const projectile of [...scene.projectiles.getChildren()]) {
    if (!projectile?.active) continue
    for (const hazard of [...scene.hazards.getChildren()]) {
      if (projectile.active && hazard?.active && rectanglesOverlap(projectile, hazard)) {
        scene.hitHazard(projectile, hazard)
      }
    }
    if (
      projectile.active &&
      (
        projectile.x < -30 ||
        projectile.x > scene.worldWidth + 30 ||
        projectile.y < -30 ||
        projectile.y > scene.worldHeight + 30
      )
    ) {
      projectile.destroy()
    }
  }
}

export function triggerFlap(scene) {
  const nextPipe = [...scene.obstacleColumns.keys()]
    .filter((column) => column.leftX + column.width >= scene.player.x)
    .sort((left, right) => left.leftX - right.leftX)[0]
  scene.player.body.setVelocityY(-safeFlapImpulse(
    scene.spec.player.gravity,
    scene.spec.player.flap_strength,
    scene.player.y,
    nextPipe?.gapTop ?? 0,
    scene.player.height / 2,
  ))
}

export function applyDamage(scene, damage) {
  scene.applyPlayerDamage(damage)
}

export function addScore(scene, points) {
  scene.score += points
}

export function updateGameTimer(scene) {
  if (
    scene.spec.win_condition.type === 'survive' &&
    !scene.isFlappy &&
    !scene.isRacing &&
    scene.startedAt !== null &&
    survivalObjectiveComplete(
      scene.state,
      scene.time.now - scene.startedAt,
      scene.spec.win_condition.target,
      scene.spec.game_type,
    )
  ) {
    scene.finish('You survived!')
  }
  if (
    scene.startedAt !== null &&
    scene.spec.lose_condition.type === 'time_expired' &&
    scene.time.now - scene.startedAt >= scene.spec.lose_condition.target * 1000
  ) {
    scene.finish('Time up')
  }
}

export function interactWithGoal(scene) {
  scene.reachGoal()
}

export function hitEnemy(scene, player, enemy) {
  scene.hitEnemy(player, enemy)
}

export function resolveProjectileHit(scene, projectile, hazard) {
  scene.hitHazard(projectile, hazard)
}
