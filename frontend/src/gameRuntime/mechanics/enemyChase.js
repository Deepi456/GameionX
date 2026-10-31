export function updateChasingEnemies(scene) {
  if (scene.state !== 'playing' || !scene.player?.active) return

  scene.hazards?.children.iterate((hazard) => {
    if (!hazard.active || hazard.movement !== 'chasing') return
    const deltaX = scene.player.x - hazard.x
    const deltaY = scene.player.y - hazard.y
    const distance = Math.hypot(deltaX, deltaY)
    if (distance === 0) {
      hazard.body.setVelocity(0, 0)
      return
    }
    hazard.body.setVelocity(
      (deltaX / distance) * hazard.speed,
      (deltaY / distance) * hazard.speed,
    )
  })
}
