function property(object, key, fallback) {
  return object?.properties?.[key] ?? fallback
}

function point(object) {
  return { x: object.position.x, y: object.position.y }
}

function size(object) {
  return {
    width: object.size?.width ?? 0.04,
    height: object.size?.height ?? 0.05,
  }
}

export function runtimeConfigFromDefinition(definition, legacyConfig) {
  if (!definition) return legacyConfig

  const levels = definition.levels.map((level) => {
    const runtimeLevel = {
      id: level.id,
      name: level.name,
      background: level.background,
      platforms: [],
      collectibles: [],
      enemies: [],
      walls: [],
      targets: [],
      scenery: [],
      obstacles: [],
      hazards: [],
      goal: null,
      time_limit: level.time_limit,
      collectible_target: 0,
    }

    for (const object of level.objects) {
      const dimensions = size(object)
      const position = point(object)
      if (object.kind === 'platform') {
        runtimeLevel.platforms.push({
          x: position.x,
          y: position.y,
          ...dimensions,
          movement: property(object, 'movement', 'static'),
        })
      } else if (['coin', 'gem', 'star'].includes(object.kind)) {
        runtimeLevel.collectibles.push({
          position,
          value: property(object, 'value', 10),
          kind: object.kind,
        })
      } else if (object.kind === 'enemy' && object.visual.shape === 'enemy') {
        runtimeLevel.enemies.push({
          position,
          damage: property(object, 'damage', 1),
          movement: property(object, 'movement', 'static'),
        })
      } else if (object.visual.shape === 'hazard') {
        runtimeLevel.hazards.push({
          position,
          ...dimensions,
          kind: object.kind,
          damage: property(object, 'damage', 1),
          health: property(object, 'health', 1),
          movement: property(object, 'movement', 'horizontal'),
          speed: property(object, 'speed', 60),
        })
      } else if (object.kind === 'wall') {
        runtimeLevel.walls.push({ position, ...dimensions })
      } else if (object.kind === 'target') {
        runtimeLevel.targets.push({
          position,
          points: property(object, 'points', 10),
        })
      } else if (object.visual.shape === 'scenery') {
        runtimeLevel.scenery.push({
          position,
          ...dimensions,
          kind: object.kind,
          solid: object.solid,
        })
      } else if (object.visual.shape === 'obstacle') {
        runtimeLevel.obstacles.push({
          position,
          ...dimensions,
          kind: object.kind,
          damage: property(object, 'damage', 1),
          movement: property(object, 'movement', 'static'),
          speed: property(object, 'speed', 0),
        })
      } else if (object.visual.shape === 'goal') {
        runtimeLevel.goal = { position, kind: object.kind }
      }
    }
    runtimeLevel.collectible_target = Math.min(
      legacyConfig?.levels?.find((item) => item.id === level.id)?.collectible_target
        ?? runtimeLevel.collectibles.length,
      runtimeLevel.collectibles.length,
    )
    return runtimeLevel
  })

  const legacyPlayer = legacyConfig?.player || {}
  const fireKey = definition.controls.find(({ action }) => action === 'fire')?.keys[0]
  const fireControl = {
    Space: 'space',
    Enter: 'enter',
    KeyX: 'x',
  }[fireKey] || 'none'

  return {
    ...(legacyConfig || {}),
    schema_version: '1.0',
    title: definition.title,
    game_type: definition.genre,
    camera: definition.camera,
    theme: definition.theme,
    palette: definition.palette,
    world_width: definition.world_width,
    world_height: definition.world_height,
    player: {
      ...legacyPlayer,
      start: point(definition.player),
      appearance: property(definition.player, 'appearance', definition.player.kind),
      movement_mode: property(definition.player, 'movement_mode', 'four_direction'),
      speed: property(definition.player, 'speed', legacyPlayer.speed ?? 240),
      max_health: property(
        definition.player,
        'max_health',
        legacyPlayer.max_health ?? 3,
      ),
      gravity: property(
        definition.player,
        'gravity',
        definition.physics?.gravity_y ?? legacyPlayer.gravity ?? 0,
      ),
      jump_velocity: property(
        definition.player,
        'jump_velocity',
        definition.physics?.jump_velocity ?? legacyPlayer.jump_velocity ?? 450,
      ),
      flap_strength: definition.physics?.flap_strength ?? legacyPlayer.flap_strength ?? 360,
      fire_cooldown: property(
        definition.player,
        'fire_cooldown',
        legacyPlayer.fire_cooldown ?? 250,
      ),
    },
    fire_control: fireControl,
    levels,
    runtime_mechanics: definition.mechanics
      .filter(({ support }) => support !== 'unsupported')
      .map(({ kind }) => kind),
    ui: definition.ui,
    win_condition: definition.win.condition,
    win_target: definition.win.target,
    lose_condition: definition.lose.condition,
    lose_target: definition.lose.target,
  }
}
