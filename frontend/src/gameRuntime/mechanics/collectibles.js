import {
  mayResolveGameplay,
  winsOnCollection,
} from '../../components/phaserPreviewRules.js'
import { colorFromHex } from '../../components/phaserPreviewVisuals.js'

export function createCollectibles(scene, collectibleSpecs) {
  scene.collectibles = scene.physics.add.group()

  collectibleSpecs.forEach((collectible) => {
    const kind = scene.spec.game_type === 'maze' ? 'key' : collectible.kind
    const item = scene.addStyledEntity(
      collectible.x,
      collectible.y,
      24,
      24,
      kind,
      colorFromHex(scene.palette.collectible),
      colorFromHex(scene.palette.accent),
    )
    item.value = collectible.value
    scene.collectibles.add(item)
    item.body.setAllowGravity(false)
  })

  scene.physics.add.overlap(
    scene.player,
    scene.collectibles,
    (player, item) => collectItem(scene, player, item),
    null,
    scene,
  )
}

export function collectItem(scene, _player, item) {
  if (!mayResolveGameplay(scene.state) || scene.finished || !item.active) return

  scene.addScore(item.value)
  scene.collected += 1
  item.destroy()
  scene.updateGoalLock()
  scene.updateStatus()

  if (winsOnCollection(scene.spec.game_type, scene.spec.win_condition, scene.collected)) {
    scene.finish('You win!')
  }
}
