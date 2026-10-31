import { useEffect, useMemo, useRef } from 'react'
import Phaser from 'phaser'
import {
  canReachGoal,
  canTakeDamage,
  createFlappyPipePair,
  defeatTargetReached,
  flappyCollisionEndsGame,
  aimDirectionFromInput,
  initialGameState,
  mayArmGoal,
  mayResolveGameplay,
  projectileDamage,
  racingCollisionDamage,
  startGame,
} from './phaserPreviewRules'
import {
  resolveRuntimeCapabilities,
  runRuntimeCapability,
  runRuntimeCapabilityHook,
} from '../gameRuntime/capabilityRegistry'
import { runtimeConfigFromDefinition } from '../gameRuntime/gameDefinitionAdapter'
import {
  colorFromHex,
  getSceneBackground,
  getScenePalette,
  getSceneTheme,
  mapLevelEntities,
  placeMazeCollectibles,
} from './phaserPreviewVisuals'

const WIDTH = 800
const HEIGHT = 480

function createPreviewScene(spec) {
  return class PreviewScene extends Phaser.Scene {
    constructor() {
      super('gameionx-preview')
      this.spec = spec
    }
    hasCapability(capabilityId) {
      return this.capabilityIds?.has(capabilityId) || false
    }

    create(data = {}) {
      this.levelIndex = data.levelIndex || 0
      const level = this.spec.levels[this.levelIndex]
      this.score = 0
      this.health = this.spec.player.max_health
      this.collected = 0
      this.jumpCount = 0
      this.defeatedEnemies = 0
      this.finished = false
      this.goalReached = false
      this.goalArmed = false
      this.goalUnlocked = false
      this.nextFireAt = 0
      this.nextDamageAt = 0
      this.raceSpeedMultiplier = 1
      this.raceElapsedMs = 0
      this.raceTotalLaps = 3
      this.raceLapsCompleted = 0
      this.raceCurrentLap = 1
      this.raceDurationSeconds = this.spec.win_condition.target || 30
      this.aimDirection = { x: 0, y: -1 }
      this.ui = this.spec.ui || {}
      this.capabilities = resolveRuntimeCapabilities(
        this.spec.game_type,
        this.spec.runtime_mechanics,
      )
      this.capabilityIds = new Set(this.capabilities.map(({ id }) => id))
      this.capabilityMap = new Map(this.capabilities.map((item) => [item.id, item]))
      this.isPlatformer = this.hasCapability('platform_movement')
      this.isFlappy = this.hasCapability('flapping')
      this.isShooter = this.hasCapability('projectiles')
      this.isRacing = this.hasCapability('racing_movement')
      this.hasChasingEnemies = this.hasCapability('chasing_enemies')
      this.isTopDown = !this.isPlatformer && !this.isFlappy
      this.state = initialGameState()
      this.startedAt = null
      this.worldWidth = this.isFlappy
        ? WIDTH
        : Math.max(WIDTH, this.spec.world_width || WIDTH)
      this.worldHeight = this.isFlappy
        ? HEIGHT
        : Math.max(HEIGHT, this.spec.world_height || HEIGHT)
      this.startPosition = {
        x: this.spec.player.start.x,
        y: this.spec.player.start.y,
      }
      this.palette = getScenePalette(this.spec)
      this.theme = getSceneTheme(this.spec)
      this.renderEntities = mapLevelEntities(
        level,
        this.worldWidth,
        this.worldHeight,
      )
      if (this.spec.game_type === 'maze') {
        this.renderEntities.collectibles = placeMazeCollectibles(
          this.renderEntities.collectibles,
          this.renderEntities.walls,
          {
            x: this.spec.player.start.x * this.worldWidth,
            y: this.spec.player.start.y * this.worldHeight,
          },
          this.worldWidth,
          this.worldHeight,
          this.renderEntities.goal,
        )
      }
      this.addBackdrop(level)
      this.physics.world.setBounds(0, 0, this.worldWidth, this.worldHeight)
      this.cameras.main.setBounds(0, 0, this.worldWidth, this.worldHeight)
      this.platforms = this.physics.add.group({ immovable: true, allowGravity: false })
      this.solidScenery = this.physics.add.staticGroup()

      const solidObjects = this.isPlatformer
        ? this.renderEntities.platforms
        : this.renderEntities.walls
      solidObjects.forEach((solid) => {
        const item = this.addStyledEntity(
          solid.x,
          solid.y,
          solid.width,
          solid.height,
          this.isPlatformer ? 'platform' : 'wall',
          colorFromHex(this.isPlatformer ? this.palette.primary : this.palette.wall),
          colorFromHex(this.palette.secondary),
        )
        this.platforms.add(item)
        item.movement = solid.movement
        item.body.setImmovable(true)
      })
      this.addScenery()

      const playerSize = this.isFlappy
        ? { width: 34, height: 26 }
        : this.isRacing
          ? { width: 34, height: 48 }
          : this.spec.player.appearance === 'ship'
            ? { width: 36, height: 40 }
            : { width: 30, height: 38 }
      this.player = this.addStyledEntity(
        this.spec.player.start.x * this.worldWidth,
        this.spec.player.start.y * this.worldHeight,
        playerSize.width,
        playerSize.height,
        this.spec.player.appearance,
        colorFromHex(this.palette.player),
        colorFromHex(this.palette.accent),
      )
      this.physics.add.existing(this.player)
      this.player.body.setSize(playerSize.width, playerSize.height)
      this.player.body.setCollideWorldBounds(true)
      this.player.body.setAllowGravity(false)
      if (this.worldWidth > WIDTH || this.worldHeight > HEIGHT) {
        this.cameras.main.startFollow(this.player, true, 0.12, 0.12)
      }
      this.addObstacles(level)
      if (this.isRacing) this.createRacingFinishLine()
      this.physics.add.collider(this.player, this.platforms)
      this.physics.add.collider(this.player, this.solidScenery)
      this.goal = this.physics.add.staticGroup()
      if (this.renderEntities.goal) {
        const goal = this.addStyledEntity(
          this.renderEntities.goal.x,
          this.renderEntities.goal.y,
          42,
          42,
          this.renderEntities.goal.kind,
          colorFromHex(this.palette.accent),
          colorFromHex(this.palette.secondary),
        )
        this.goal.add(goal)
        this.goalSprite = goal
        this.physics.add.overlap(
          this.player,
          this.goal,
          () => runRuntimeCapability(this, 'goal_interaction'),
          null,
          this,
        )
        if (
          this.spec.game_type === 'top_down_collection' ||
          (this.spec.win_condition.type === 'reach_goal' &&
            level.collectible_target > 0)
        ) {
          this.goalLabel = this.add.text(
            goal.x,
            goal.y + 31,
            'LOCKED',
            { color: '#fff4c2', fontFamily: 'system-ui', fontSize: '12px', fontStyle: 'bold' },
          ).setOrigin(0.5)
        }
      }

      this.addScore = (points) => runRuntimeCapability(this, 'scoring', points)
      runRuntimeCapabilityHook(
        this,
        'collectibles',
        'createImplementation',
        this.renderEntities.collectibles,
      )
      this.targets = this.physics.add.group()
      this.renderEntities.targets.forEach((target) => {
        const item = this.addStyledEntity(
          target.x,
          target.y,
          34,
          34,
          'target',
          colorFromHex(this.palette.enemy),
          colorFromHex(this.palette.accent),
        )
        item.points = target.points
        this.targets.add(item)
      })
      this.physics.add.overlap(
        this.player,
        this.targets,
        (player, item) => runRuntimeCapability(this, 'target_scoring', player, item),
        null,
        this,
      )

      this.enemies = this.physics.add.group()
      this.renderEntities.enemies.forEach((enemy) => {
        runRuntimeCapability(this, 'entity_creation', enemy)
      })
      this.physics.add.collider(
        this.player,
        this.enemies,
        (player, enemy) => runRuntimeCapability(this, 'enemy_contact', player, enemy),
        null,
        this,
      )
      this.projectiles = this.physics.add.group()
      this.cursors = this.input.keyboard.createCursorKeys()
      this.shooterKeysDown = new Set()
      const fireKey = {
        space: Phaser.Input.Keyboard.KeyCodes.SPACE,
        enter: Phaser.Input.Keyboard.KeyCodes.ENTER,
        x: Phaser.Input.Keyboard.KeyCodes.X,
      }[this.spec.fire_control]
      this.keys = this.input.keyboard.addKeys({
        up: Phaser.Input.Keyboard.KeyCodes.W,
        left: Phaser.Input.Keyboard.KeyCodes.A,
        down: Phaser.Input.Keyboard.KeyCodes.S,
        right: Phaser.Input.Keyboard.KeyCodes.D,
        space: Phaser.Input.Keyboard.KeyCodes.SPACE,
        restart: Phaser.Input.Keyboard.KeyCodes.R,
        fire: fireKey || Phaser.Input.Keyboard.KeyCodes.SPACE,
      })
      this.onGameKeyDown = (event) => this.handleGameKeyDown(event)
      this.onGameKeyUp = (event) => this.shooterKeysDown.delete(event.code)
      this.game.canvas.addEventListener('keydown', this.onGameKeyDown)
      this.game.canvas.addEventListener('keyup', this.onGameKeyUp)
      this.input.keyboard.addCapture([
        Phaser.Input.Keyboard.KeyCodes.LEFT,
        Phaser.Input.Keyboard.KeyCodes.RIGHT,
        Phaser.Input.Keyboard.KeyCodes.UP,
        Phaser.Input.Keyboard.KeyCodes.DOWN,
        Phaser.Input.Keyboard.KeyCodes.A,
        Phaser.Input.Keyboard.KeyCodes.D,
        Phaser.Input.Keyboard.KeyCodes.W,
        Phaser.Input.Keyboard.KeyCodes.S,
        Phaser.Input.Keyboard.KeyCodes.SPACE,
        Phaser.Input.Keyboard.KeyCodes.ENTER,
        Phaser.Input.Keyboard.KeyCodes.X,
      ])
      this.game.canvas.tabIndex = 0
      this.focusCanvas = () => this.game.canvas.focus()
      this.game.canvas.addEventListener('pointerdown', this.focusCanvas)
      this.game.canvas.focus()
      this.statusText = this.add.text(16, 14, '', {
        color: '#ffffff',
        fontFamily: 'system-ui',
        fontSize: '16px',
      }).setScrollFactor(0)
      this.startPrompt = this.add.text(
        WIDTH / 2,
        HEIGHT / 2,
        this.ui.start_prompt || this.getStartPrompt(),
        {
          color: this.isFlappy ? '#17324d' : '#ffffff',
          backgroundColor: this.isFlappy ? '#ffffffcc' : '#111827cc',
          fontFamily: 'system-ui',
          fontSize: '20px',
          fontStyle: 'bold',
          padding: { x: 12, y: 8 },
        },
      ).setOrigin(0.5).setScrollFactor(0)
      if (this.isRacing) this.racingFinishLine.setDepth(2)
      this.events.once(Phaser.Scenes.Events.SHUTDOWN, this.shutdown, this)
      this.updateGoalLock()
      this.updateStatus()
    }

    addBackdrop(level) {
      const colors = this.palette
      const backgroundColor = getSceneBackground(this.spec, level)
      const background = colorFromHex(backgroundColor)
      this.cameras.main.setBackgroundColor(backgroundColor)
      const graphics = this.add.graphics()
      graphics.fillStyle(background, 1).fillRect(0, 0, this.worldWidth, this.worldHeight)

      if (this.theme === 'forest') {
        graphics.fillStyle(colorFromHex(colors.primary), 1)
        graphics.fillRect(0, this.worldHeight * 0.72, this.worldWidth, this.worldHeight * 0.28)
        graphics.fillStyle(colorFromHex(colors.secondary), 0.36)
        for (let index = 0; index < 12; index += 1) {
          const x = (index * 173 + 41) % this.worldWidth
          const canopyY = this.worldHeight * (0.36 + (index % 3) * 0.08)
          const radius = 54 + (index % 4) * 13
          graphics.fillCircle(x, canopyY, radius)
          graphics.fillRect(x - 8, canopyY, 16, this.worldHeight * 0.28)
        }
        graphics.fillStyle(colorFromHex(colors.accent), 0.24)
        graphics.fillRect(0, this.worldHeight * 0.91, this.worldWidth, this.worldHeight * 0.09)
        graphics.lineStyle(3, colorFromHex(colors.secondary), 0.55)
        for (let y = this.worldHeight * 0.15; y < this.worldHeight * 0.8; y += 90) {
          graphics.lineBetween(0, y, this.worldWidth, y + 18)
        }
      } else if (this.theme === 'sky') {
        graphics.fillStyle(colorFromHex(colors.accent), 0.9)
        graphics.fillCircle(this.worldWidth * 0.82, this.worldHeight * 0.18, 38)
        if (!this.isFlappy) {
          graphics.fillStyle(colorFromHex(colors.secondary), 0.8)
          graphics.fillTriangle(0, this.worldHeight * 0.78, this.worldWidth * 0.32, this.worldHeight * 0.32, this.worldWidth * 0.62, this.worldHeight * 0.78)
          graphics.fillStyle(colorFromHex(colors.primary), 0.75)
          graphics.fillTriangle(this.worldWidth * 0.3, this.worldHeight * 0.8, this.worldWidth * 0.7, this.worldHeight * 0.4, this.worldWidth, this.worldHeight * 0.8)
        }
        graphics.fillStyle(0x74b96a, 1).fillRect(0, this.worldHeight * 0.94, this.worldWidth, this.worldHeight * 0.06)
      } else if (this.theme === 'space') {
        graphics.fillStyle(colorFromHex(colors.primary), 0.48)
        graphics.fillCircle(this.worldWidth * 0.78, this.worldHeight * 0.27, 76)
        graphics.lineStyle(5, colorFromHex(colors.secondary), 0.55)
        graphics.strokeEllipse(this.worldWidth * 0.78, this.worldHeight * 0.27, 160, 34)
        for (let index = 0; index < 95; index += 1) {
          const x = (index * 137 + 19) % this.worldWidth
          const y = (index * 79 + 31) % this.worldHeight
          const radius = index % 9 === 0 ? 2 : 1
          graphics.fillStyle(index % 4 === 0 ? colorFromHex(colors.accent) : 0xffffff, 0.5 + (index % 5) / 10)
          graphics.fillCircle(x, y, radius)
        }
      } else if (this.theme === 'road') {
        graphics.fillStyle(colorFromHex(colors.primary), 1)
        graphics.fillRect(this.worldWidth * 0.23, 0, this.worldWidth * 0.54, this.worldHeight)
        graphics.fillStyle(colorFromHex(colors.secondary), 1)
        graphics.fillRect(this.worldWidth * 0.25, 0, this.worldWidth * 0.5, this.worldHeight)
        graphics.fillStyle(0xffffff, 0.85)
        graphics.fillRect(this.worldWidth * 0.27, 0, 5, this.worldHeight)
        graphics.fillRect(this.worldWidth * 0.73, 0, 5, this.worldHeight)
        graphics.fillStyle(colorFromHex(colors.accent), 1)
        for (let y = -20; y < this.worldHeight; y += 72) {
          graphics.fillRect(this.worldWidth * 0.495, y, 8, 38)
        }
      } else if (this.theme === 'maze') {
        graphics.fillStyle(colorFromHex(colors.primary), 1)
        graphics.fillRect(0, 0, this.worldWidth, this.worldHeight)
        graphics.lineStyle(1, colorFromHex(colors.secondary), 0.3)
        for (let x = 0; x <= this.worldWidth; x += 32) graphics.lineBetween(x, 0, x, this.worldHeight)
        for (let y = 0; y <= this.worldHeight; y += 32) graphics.lineBetween(0, y, this.worldWidth, y)
      } else {
        graphics.lineStyle(1, colorFromHex(colors.primary), 0.38)
        for (let x = 0; x <= this.worldWidth; x += 40) graphics.lineBetween(x, 0, x, this.worldHeight)
        for (let y = 0; y <= this.worldHeight; y += 40) graphics.lineBetween(0, y, this.worldWidth, y)
      }
    }

    addStyledEntity(x, y, width, height, kind, color, secondaryColor) {
      const container = this.add.container(x, y)
      const graphics = this.make.graphics({ x: 0, y: 0, add: false })
      const left = -width / 2
      const top = -height / 2
      const color2 = secondaryColor ?? color
      graphics.fillStyle(color, 1)

      if (kind === 'bird') {
        graphics.fillEllipse(0, 1, width * 0.78, height * 0.72)
        graphics.fillStyle(color2, 1).fillEllipse(-width * 0.08, height * 0.1, width * 0.42, height * 0.25)
        graphics.fillStyle(0xffffff, 1).fillCircle(width * 0.2, -height * 0.12, height * 0.11)
        graphics.fillStyle(0x172033, 1).fillCircle(width * 0.23, -height * 0.12, height * 0.045)
        graphics.fillStyle(0xf97316, 1).fillTriangle(width * 0.3, 0, width * 0.58, height * 0.08, width * 0.3, height * 0.2)
      } else if (kind === 'ship') {
        graphics.fillTriangle(0, top, left, height * 0.36, width / 2, height * 0.36)
        graphics.fillStyle(color2, 1).fillTriangle(0, -height * 0.13, -width * 0.16, height * 0.25, width * 0.16, height * 0.25)
        graphics.fillStyle(0xf97316, 1).fillTriangle(-width * 0.1, height * 0.37, 0, height * 0.62, width * 0.1, height * 0.37)
      } else if (kind === 'car') {
        graphics.fillRoundedRect(-width * 0.34, -height * 0.46, width * 0.68, height * 0.92, 8)
        graphics.fillStyle(color2, 1).fillRoundedRect(-width * 0.22, -height * 0.2, width * 0.44, height * 0.32, 5)
        graphics.fillStyle(0x172033, 1)
        graphics.fillRoundedRect(-width * 0.46, -height * 0.32, width * 0.15, height * 0.26, 3)
        graphics.fillRoundedRect(width * 0.31, -height * 0.32, width * 0.15, height * 0.26, 3)
        graphics.fillRoundedRect(-width * 0.46, height * 0.12, width * 0.15, height * 0.26, 3)
        graphics.fillRoundedRect(width * 0.31, height * 0.12, width * 0.15, height * 0.26, 3)
      } else if (kind === 'explorer' || kind === 'player') {
        graphics.fillStyle(color2, 1).fillCircle(0, -height * 0.25, width * 0.22)
        graphics.fillStyle(color, 1).fillRoundedRect(-width * 0.28, -height * 0.04, width * 0.56, height * 0.52, 6)
        graphics.fillStyle(0x263238, 1)
        graphics.fillRoundedRect(-width * 0.25, height * 0.35, width * 0.18, height * 0.18, 3)
        graphics.fillRoundedRect(width * 0.07, height * 0.35, width * 0.18, height * 0.18, 3)
      } else if (kind === 'platform' && this.theme === 'forest') {
        graphics.fillStyle(color, 1).fillRoundedRect(left, -height * 0.25, width, height * 0.75, 4)
        graphics.fillStyle(color2, 1).fillRoundedRect(left, -height * 0.5, width, height * 0.35, 4)
        graphics.fillStyle(0xffffff, 0.18).fillRect(left + 4, -height * 0.43, width - 8, 2)
      } else if (kind === 'tree') {
        graphics.fillStyle(0x755236, 1).fillRoundedRect(-width * 0.12, height * 0.04, width * 0.24, height * 0.48, 4)
        graphics.fillStyle(color, 1)
        graphics.fillCircle(-width * 0.19, -height * 0.13, width * 0.31)
        graphics.fillCircle(width * 0.17, -height * 0.19, width * 0.34)
        graphics.fillCircle(0, -height * 0.38, width * 0.32)
      } else if (kind === 'bush') {
        graphics.fillEllipse(-width * 0.18, 0, width * 0.58, height * 0.76)
        graphics.fillEllipse(width * 0.18, -height * 0.04, width * 0.58, height * 0.8)
      } else if (kind === 'cloud') {
        graphics.fillEllipse(-width * 0.2, height * 0.08, width * 0.52, height * 0.62)
        graphics.fillEllipse(width * 0.14, -height * 0.14, width * 0.58, height * 0.82)
        graphics.fillEllipse(width * 0.36, height * 0.1, width * 0.42, height * 0.56)
      } else if (kind === 'path') {
        graphics.fillRoundedRect(left, top, width, height, Math.min(height / 2, 20))
      } else if (kind === 'water') {
        graphics.fillRoundedRect(left, top, width, height, Math.min(height / 2, 24))
        graphics.lineStyle(Math.max(2, height * 0.07), color2, 0.65)
        for (let ripple = 0; ripple < 3; ripple += 1) {
          const rippleY = top + height * (0.28 + ripple * 0.24)
          graphics.lineBetween(left + width * 0.12, rippleY, left + width * 0.54, rippleY)
        }
      } else if (kind === 'flower') {
        for (let flower = 0; flower < 3; flower += 1) {
          const flowerX = left + width * (0.25 + flower * 0.25)
          graphics.fillStyle(color2, 1).fillCircle(flowerX, -height * 0.08, Math.max(3, height * 0.2))
          graphics.fillStyle(color, 1).fillCircle(flowerX, -height * 0.08, Math.max(2, height * 0.08))
        }
      } else if (kind === 'grass' || kind === 'roadside') {
        for (let blade = 0; blade < 6; blade += 1) {
          const bladeX = left + ((blade + 0.5) * width) / 6
          graphics.fillTriangle(bladeX, height / 2, bladeX - width * 0.08, -height * 0.2, bladeX + width * 0.04, height / 2)
        }
      } else if (kind === 'mountain' || kind === 'rock') {
        graphics.fillTriangle(left, height / 2, 0, top, width / 2, height / 2)
        graphics.fillStyle(color2, 1).fillTriangle(-width * 0.1, -height * 0.25, 0, top, width * 0.18, -height * 0.22)
      } else if (kind === 'pipe' || kind === 'pipe-bottom') {
        graphics.fillRoundedRect(left + width * 0.13, top, width * 0.74, height, 5)
        graphics.fillStyle(color2, 1).fillRoundedRect(
          left,
          kind === 'pipe-bottom' ? top + height * 0.2 : top + height * 0.68,
          width,
          height * 0.12,
          4,
        )
        graphics.fillStyle(0xffffff, 0.2).fillRect(left + width * 0.2, top, width * 0.1, height * 0.66)
      } else if (kind === 'cone') {
        graphics.fillTriangle(0, top, left, height / 2, width / 2, height / 2)
        graphics.fillStyle(color2, 1).fillRect(left + width * 0.15, height * 0.12, width * 0.7, height * 0.1)
      } else if (kind === 'barrier') {
        graphics.fillRoundedRect(left, -height * 0.28, width, height * 0.56, 4)
        graphics.fillStyle(color2, 1)
        graphics.fillRect(left + width * 0.12, -height * 0.18, width * 0.16, height * 0.36)
        graphics.fillRect(width * 0.22, -height * 0.18, width * 0.16, height * 0.36)
      } else if (kind === 'guard' || kind === 'enemy') {
        graphics.fillRoundedRect(left + width * 0.12, top + height * 0.22, width * 0.76, height * 0.7, 8)
        graphics.fillStyle(0xffffff, 1)
        graphics.fillCircle(-width * 0.14, -height * 0.05, Math.max(2, width * 0.09))
        graphics.fillCircle(width * 0.14, -height * 0.05, Math.max(2, width * 0.09))
        graphics.fillStyle(0x172033, 1)
        graphics.fillCircle(-width * 0.14, -height * 0.05, Math.max(1, width * 0.04))
        graphics.fillCircle(width * 0.14, -height * 0.05, Math.max(1, width * 0.04))
      } else if (kind === 'zombie') {
        graphics.fillStyle(color2, 1).fillEllipse(0, -height * 0.2, width * 0.62, height * 0.48)
        graphics.fillStyle(color, 1).fillRoundedRect(
          -width * 0.32, -height * 0.02, width * 0.64, height * 0.74, 5,
        )
        graphics.fillStyle(0xb8c19c, 1)
        graphics.fillCircle(-width * 0.13, -height * 0.2, Math.max(2, width * 0.07))
        graphics.fillCircle(width * 0.13, -height * 0.2, Math.max(2, width * 0.07))
        graphics.fillStyle(0x571c1c, 1)
        graphics.fillCircle(-width * 0.13, -height * 0.2, Math.max(1, width * 0.035))
        graphics.fillCircle(width * 0.13, -height * 0.2, Math.max(1, width * 0.035))
        graphics.fillStyle(color, 1)
        graphics.fillRoundedRect(-width * 0.46, height * 0.08, width * 0.18, height * 0.42, 3)
        graphics.fillRoundedRect(width * 0.28, height * 0.06, width * 0.18, height * 0.42, 3)
      } else if (kind === 'building') {
        graphics.fillStyle(0x30383b, 1).fillRoundedRect(left, top, width, height, 5)
        graphics.fillStyle(color, 1).fillRect(left + width * 0.12, top + height * 0.12, width * 0.76, height * 0.12)
        graphics.fillStyle(color2, 1)
        const columns = width > 100 ? 3 : 2
        const rows = height > 100 ? 3 : 2
        for (let row = 0; row < rows; row += 1) {
          for (let column = 0; column < columns; column += 1) {
            graphics.fillRect(
              left + width * (0.14 + column * 0.28),
              top + height * (0.34 + row * 0.2),
              width * 0.13,
              height * 0.1,
            )
          }
        }
        graphics.fillStyle(0x151b1d, 1).fillRect(-width * 0.11, height * 0.3, width * 0.22, height * 0.2)
      } else if (kind === 'spike') {
        graphics.fillTriangle(0, top, left, height / 2, width / 2, height / 2)
      } else if (kind === 'chest') {
        graphics.fillRoundedRect(left, -height * 0.22, width, height * 0.68, 5)
        graphics.fillStyle(color2, 1).fillRect(left, -height * 0.28, width, height * 0.17)
        graphics.fillStyle(0x5b3a1b, 1).fillRect(-width * 0.06, -height * 0.08, width * 0.12, height * 0.2)
      } else if (kind === 'exit') {
        graphics.fillStyle(color2, 1).fillRoundedRect(-width * 0.04, -height * 0.48, width * 0.08, height * 0.96, 2)
        graphics.fillStyle(color, 1).fillTriangle(
          -width * 0.02,
          -height * 0.46,
          width * 0.48,
          -height * 0.27,
          -width * 0.02,
          -height * 0.05,
        )
        graphics.fillStyle(color2, 1).fillCircle(0, height * 0.48, width * 0.08)
      } else if (kind === 'beacon') {
        graphics.fillCircle(0, 0, Math.min(width, height) * 0.47)
        graphics.lineStyle(3, color2, 1).strokeCircle(0, 0, Math.min(width, height) * 0.3)
      } else if (kind === 'target') {
        graphics.fillCircle(0, 0, Math.min(width, height) * 0.48)
        graphics.fillStyle(color2, 1).fillCircle(0, 0, Math.min(width, height) * 0.29)
        graphics.fillStyle(0xffffff, 1).fillCircle(0, 0, Math.min(width, height) * 0.12)
      } else if (kind === 'gem') {
        graphics.fillTriangle(0, top, width / 2, -height * 0.12, width * 0.32, height / 2)
        graphics.fillTriangle(0, top, -width / 2, -height * 0.12, -width * 0.32, height / 2)
        graphics.fillTriangle(-width * 0.32, height / 2, width * 0.32, height / 2, 0, height * 0.2)
      } else if (kind === 'star') {
        graphics.fillCircle(0, 0, Math.min(width, height) * 0.42)
        graphics.fillStyle(color2, 1).fillCircle(0, 0, Math.min(width, height) * 0.18)
      } else if (kind === 'coin') {
        graphics.fillCircle(0, 0, Math.min(width, height) * 0.42)
        graphics.lineStyle(3, color2, 1).strokeCircle(0, 0, Math.min(width, height) * 0.28)
      } else if (kind === 'key') {
        graphics.lineStyle(3, color2, 1).strokeCircle(-width * 0.2, 0, height * 0.25)
        graphics.fillStyle(color, 1)
        graphics.fillRoundedRect(-width * 0.02, -height * 0.07, width * 0.52, height * 0.14, 2)
        graphics.fillRect(width * 0.22, height * 0.02, width * 0.1, height * 0.18)
        graphics.fillRect(width * 0.4, height * 0.02, width * 0.1, height * 0.14)
      } else {
        graphics.fillRoundedRect(left, top, width, height, 5)
      }

      container.add(graphics)
      container.setSize(width, height)
      return container
    }

    addScenery() {
      this.renderEntities.scenery.forEach((scenery) => {
        if (this.isFlappy && scenery.kind === 'mountain') return
        const item = this.addStyledEntity(
          scenery.x,
          scenery.y,
          scenery.width,
          scenery.height,
          scenery.kind,
          scenery.kind === 'path' ? colorFromHex(this.palette.accent) : colorFromHex(this.palette.secondary),
          colorFromHex(this.palette.primary),
        )
        if (scenery.solid) {
          this.solidScenery.add(item)
          item.body.setSize(scenery.width, scenery.height)
        }
      })
    }

    addObstacles(level) {
      this.obstacles = this.physics.add.group()
      this.obstacleColumns = new Map()
      if (this.isFlappy) {
        runRuntimeCapabilityHook(
          this,
          'scrolling_obstacles',
          'configureImplementation',
          level,
        )
        return
      }
      this.renderEntities.obstacles.forEach((obstacle) => {
        const item = this.addStyledEntity(
          obstacle.x,
          obstacle.y,
          obstacle.width,
          obstacle.height,
          obstacle.kind === 'pipe' && obstacle.normalizedY > 0.5
            ? 'pipe-bottom'
            : obstacle.kind,
          colorFromHex(this.palette.obstacle),
          colorFromHex(this.palette.secondary),
        )
        item.kind = obstacle.kind
        item.speed = obstacle.speed || (this.isFlappy ? 180 : 0)
        item.movement = obstacle.movement
        item.damage = obstacle.damage
        this.physics.add.existing(item)
        item.body.setSize(obstacle.width, obstacle.height)
        item.body.setAllowGravity(false)
        this.obstacles.add(item)
      })
      if (this.isRacing) {
        this.createRacingObstacle(this.worldWidth * 0.35, this.worldHeight * 0.18)
        this.createRacingObstacle(this.worldWidth * 0.65, this.worldHeight * 0.34)
        this.createRacingOpponents()
      }
      this.hazards = this.physics.add.group()
      this.renderEntities.hazards.forEach((hazard) => {
        this.createShooterHazard(
          hazard.x,
          hazard.y,
          hazard.width,
          hazard.height,
          hazard,
        )
      })
      this.physics.add.overlap(this.player, this.obstacles, this.hitObstacle, null, this)
      this.physics.add.overlap(this.player, this.hazards, this.hitHazard, null, this)
      if (this.isRacing) {
        this.physics.add.overlap(
          this.player,
          this.racingOpponents,
          this.hitRacingOpponent,
          null,
          this,
        )
      }
    }

    createRacingObstacle(x, y) {
      const obstacle = this.addStyledEntity(
        x,
        y,
        30,
        38,
        'cone',
        colorFromHex(this.palette.obstacle),
        colorFromHex(this.palette.accent),
      )
      obstacle.kind = 'cone'
      obstacle.damage = 1
      obstacle.speed = 140
      this.physics.add.existing(obstacle)
      obstacle.body.setSize(24, 30)
      obstacle.body.setAllowGravity(false)
      this.obstacles.add(obstacle)
      return obstacle
    }

    createRacingOpponents() {
      this.racingOpponents = this.physics.add.group()
      for (const [index, spawn] of [
        { x: 0.35, y: 0.16 },
        { x: 0.65, y: 0.3 },
        { x: 0.5, y: 0.04 },
      ].entries()) {
        const opponent = this.addStyledEntity(
          spawn.x * this.worldWidth,
          spawn.y * this.worldHeight,
          32,
          46,
          'car',
          colorFromHex(this.palette.enemy),
          colorFromHex(this.palette.secondary),
        )
        opponent.damage = 1
        opponent.racingSpeed = 115 + index * 12
        this.physics.add.existing(opponent)
        opponent.body.setSize(28, 40)
        opponent.body.setAllowGravity(false)
        this.racingOpponents.add(opponent)
      }
    }

    recycleRacingOpponent(opponent) {
      const lanes = [0.34, 0.5, 0.66]
      const lane = lanes[Phaser.Math.Between(0, lanes.length - 1)]
      const highestOpponentY = Math.min(
        ...this.racingOpponents.getChildren()
          .filter((item) => item !== opponent && item.active)
          .map((item) => item.y),
        0,
      )
      opponent.x = lane * this.worldWidth
      opponent.y = highestOpponentY - Phaser.Math.Between(90, 170)
      opponent.body.updateFromGameObject()
    }

    createRacingFinishLine() {
      const roadLeft = this.worldWidth * 0.25
      const roadWidth = this.worldWidth * 0.5
      const tileSize = 16
      this.racingFinishLineStartY = 34
      this.racingFinishLine = this.add.graphics()
      this.racingFinishLine.fillStyle(0xffffff, 1)
      for (let column = 0; column < roadWidth / tileSize; column += 1) {
        for (let row = 0; row < 2; row += 1) {
          if ((column + row) % 2 === 0) {
            this.racingFinishLine.fillRect(
              column * tileSize,
              row * tileSize,
              tileSize,
              tileSize,
            )
          }
        }
      }
      this.racingFinishLine.setPosition(roadLeft, this.racingFinishLineStartY)
    }

    createShooterHazard(x, y, width, height, properties = {}) {
      const item = this.addStyledEntity(
        x,
        y,
        width,
        height,
        properties.kind || 'enemy',
        colorFromHex(this.palette.enemy),
        colorFromHex(this.palette.accent),
      )
      item.kind = properties.kind || 'enemy'
      item.speed = properties.speed || 90
      item.movement = properties.movement || 'horizontal'
      item.damage = properties.damage || 1
      item.health = properties.health || 1
      this.physics.add.existing(item)
      this.hazards.add(item)
      item.body.setSize(width, height)
      item.body.setAllowGravity(false)
      item.body.setCollideWorldBounds(true)
      item.body.setBounce(1, 1)
      item.direction = Phaser.Math.Between(0, 1) ? 1 : -1
      if (this.state === 'playing') {
        if (item.movement === 'chasing') {
          item.body.setVelocity(0, 0)
        } else if (item.movement === 'vertical') {
          item.body.setVelocityY(item.speed * item.direction)
        } else {
          item.body.setVelocityX(item.speed * item.direction)
        }
      } else {
        item.body.setVelocity(0, 0)
      }
      return item
    }

    spawnFlappyPipePair(x, gapCenter, gapSize, width, speed = 180) {
      const geometry = createFlappyPipePair(x, gapCenter, gapSize, width, HEIGHT, speed)
      const column = { ...geometry, passed: false }
      column.visual = this.add.graphics().setDepth(1)
      this.drawFlappyPipePair(column, geometry)
      this.obstacleColumns.set(column, column)
    }

    drawFlappyPipePair(column, geometry) {
      const pipeColor = colorFromHex(this.palette.obstacle)
      const pipeEdge = colorFromHex(this.palette.wall)
      const highlight = colorFromHex(this.palette.secondary)
      const capHeight = 18
      const left = geometry.leftX
      const upperHeight = geometry.gapTop
      const lowerHeight = geometry.worldHeight - geometry.gapBottom
      const lowerY = geometry.gapBottom

      column.visual.clear()
      column.visual.fillStyle(pipeColor, 1)
      column.visual.fillRect(left + 5, 0, geometry.width - 10, upperHeight)
      column.visual.fillRect(left + 5, lowerY, geometry.width - 10, lowerHeight)
      column.visual.fillStyle(highlight, 1)
      column.visual.fillRect(left + 12, 0, 8, Math.max(0, upperHeight - capHeight))
      column.visual.fillRect(left + 12, lowerY + capHeight, 8, Math.max(0, lowerHeight - capHeight))
      column.visual.fillStyle(pipeEdge, 1)
      column.visual.fillRect(left, geometry.gapTop - capHeight, geometry.width, capHeight)
      column.visual.fillRect(left, geometry.gapBottom, geometry.width, capHeight)
      column.visual.lineStyle(3, pipeEdge, 1)
      column.visual.strokeRect(left + 5, 0, geometry.width - 10, upperHeight)
      column.visual.strokeRect(left + 5, lowerY, geometry.width - 10, lowerHeight)
      column.visual.lineStyle(2, highlight, 1)
      column.visual.strokeRect(left, geometry.gapTop - capHeight, geometry.width, capHeight)
      column.visual.strokeRect(left, geometry.gapBottom, geometry.width, capHeight)
    }

    spawnShooterEnemy() {
      if (this.finished || this.defeatedEnemies >= (this.spec.win_condition.target || 8)) return
      if (this.hasChasingEnemies) {
        const archetype = this.renderEntities.hazards.find(
          (hazard) => hazard.movement === 'chasing',
        )
        if (!archetype) return
        let position = {
          x: Phaser.Math.Between(42, this.worldWidth - 42),
          y: Phaser.Math.Between(42, this.worldHeight - 42),
        }
        for (let attempt = 0; attempt < 8; attempt += 1) {
          if (Phaser.Math.Distance.Between(
            position.x, position.y, this.player.x, this.player.y,
          ) >= 130) break
          position = {
            x: Phaser.Math.Between(42, this.worldWidth - 42),
            y: Phaser.Math.Between(42, this.worldHeight - 42),
          }
        }
        this.createShooterHazard(
          position.x,
          position.y,
          archetype.width,
          archetype.height,
          {
            kind: archetype.kind,
            health: archetype.health,
            movement: archetype.movement,
            speed: archetype.speed,
            damage: archetype.damage,
          },
        )
        return
      }
      const x = Phaser.Math.Between(42, this.worldWidth - 42)
      const y = Phaser.Math.Between(38, Math.min(130, this.worldHeight * 0.35))
      this.createShooterHazard(x, y, 42, 34, {
        kind: 'enemy',
        health: Phaser.Math.Between(1, 2),
        movement: Phaser.Math.Between(0, 1) ? 'horizontal' : 'vertical',
        speed: Phaser.Math.Between(55, 100),
        damage: 1,
      })
    }

    getStartPrompt() {
      if (this.isFlappy) return 'Press SPACE or ↑ to flap'
      if (this.isShooter) return 'Press a direction or SPACE to launch'
      if (this.isRacing) return 'Press ← or A to start · steer to dodge'
      return 'Press a movement key to start'
    }

    handleGameKeyDown(event) {
      if (event.repeat) return
      if (this.isShooter) this.shooterKeysDown.add(event.code)
      if (this.finished) {
        if (event.code === 'KeyR') {
          this.scene.restart({ levelIndex: this.levelIndex })
        }
        return
      }
      const flappingKey = event.code === 'Space' || event.code === 'ArrowUp'
      if (this.isFlappy && !flappingKey) return
      if (this.state === 'ready') {
        const fireControlKey = {
          space: 'Space',
          enter: 'Enter',
          x: 'KeyX',
        }[this.spec.fire_control]
        const validStartKey = this.isFlappy
          ? flappingKey
          : ['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown', 'KeyA', 'KeyD', 'KeyW', 'KeyS', fireControlKey].includes(event.code)
        if (startGame(this.state, validStartKey) !== 'playing') return
        this.state = 'playing'
        this.startedAt = this.time.now
        this.startPrompt.destroy()
        if (this.hasCapability('gravity')) runRuntimeCapability(this, 'gravity', true)
        this.startTimedMechanics()
      }
      if (this.isFlappy) {
        runRuntimeCapabilityHook(this, 'flapping', 'startInputImplementation')
      }
      if (this.isShooter && event.code === ({
        space: 'Space',
        enter: 'Enter',
        x: 'KeyX',
      }[this.spec.fire_control])) {
        this.fireProjectile()
      }
      this.updateStatus()
    }

    startTimedMechanics() {
      runRuntimeCapabilityHook(this, 'scrolling_obstacles', 'startImplementation')
      runRuntimeCapability(this, 'enemy_waves')
    }

    shutdown() {
      if (this.focusCanvas) {
        this.game.canvas.removeEventListener('pointerdown', this.focusCanvas)
        this.focusCanvas = null
      }
      if (this.onGameKeyDown) {
        this.game.canvas.removeEventListener('keydown', this.onGameKeyDown)
        this.onGameKeyDown = null
      }
      if (this.onGameKeyUp) {
        this.game.canvas.removeEventListener('keyup', this.onGameKeyUp)
        this.onGameKeyUp = null
      }
      this.pipeSpawner?.remove(false)
      this.enemySpawner?.remove(false)
    }

    hitObstacle(player, obstacle) {
      if (!mayResolveGameplay(this.state) || this.finished) return
      if (flappyCollisionEndsGame(this.spec.game_type, this.state)) {
        this.finish('Game over')
        return
      }
      this.applyPlayerDamage(obstacle.damage || 1)
      if (this.isRacing && obstacle.active) {
        obstacle.y = -obstacle.height
        obstacle.body.updateFromGameObject()
        this.applyRacingSpeedPenalty()
      }
    }

    hitRacingOpponent(player, opponent) {
      if (!mayResolveGameplay(this.state) || this.finished || !opponent.active) return
      this.applyPlayerDamage(opponent.damage || 1)
      this.applyRacingSpeedPenalty()
      this.recycleRacingOpponent(opponent)
    }

    applyRacingSpeedPenalty() {
      this.raceSpeedMultiplier = 0.55
      this.time.delayedCall(900, () => {
        if (this.player?.active && this.state === 'playing') {
          this.raceSpeedMultiplier = 1
        }
      })
    }

    hitHazard(projectileOrPlayer, hazard) {
      if (!mayResolveGameplay(this.state) || this.finished || !hazard.active) return
      if (projectileOrPlayer === this.player) {
        this.applyPlayerDamage(hazard.damage || 1)
        return
      }
      const hit = projectileDamage(hazard.health, projectileOrPlayer.damage || 1)
      hazard.health = hit.remainingHealth
      projectileOrPlayer.destroy()
      if (hit.destroyed) {
        hazard.destroy()
        runRuntimeCapability(this, 'scoring', 100)
        this.defeatedEnemies += 1
      }
      this.updateStatus()
      if (defeatTargetReached(
        this.spec.game_type,
        this.spec.win_condition,
        this.defeatedEnemies,
      )) {
        this.finish('You win!')
      }
    }

    hitTarget(player, item) {
      if (!mayResolveGameplay(this.state) || this.finished || !item.active) return
      item.destroy()
      runRuntimeCapability(this, 'scoring', item.points)
      this.collected += 1
      this.updateStatus()
      if (this.spec.win_condition.type === 'collect_target' &&
        this.collected >= this.spec.win_condition.target) {
        this.finish('Targets cleared!')
      }
    }

    reachGoal() {
      if (
        !mayResolveGameplay(this.state) ||
        this.finished ||
        this.goalReached ||
        !this.goalArmed
      ) return
      if (!canReachGoal(
        this.spec.game_type,
        this.spec.win_condition,
        this.collected,
        this.spec.levels[this.levelIndex].collectible_target,
      )) return
      this.goalReached = true
      this.finish('You win!')
    }

    hitEnemy(player, enemy) {
      if (!mayResolveGameplay(this.state) || this.finished) return
      this.applyPlayerDamage(enemy.damage)
    }

    applyPlayerDamage(damage) {
      if (!canTakeDamage(this.time.now, this.nextDamageAt)) return
      this.health = racingCollisionDamage(this.health, damage)
      this.nextDamageAt = this.time.now + (this.isShooter ? 1000 : 750)
      this.player.invulnerable = true
      this.player.setAlpha(0.65)
      this.time.delayedCall(this.isShooter ? 1000 : 750, () => {
        if (!this.player?.active) return
        this.player.invulnerable = false
        this.player.setAlpha(1)
      })
      this.updateStatus()
      if (this.health <= 0) this.finish('Game over')
    }

    finish(message) {
      if (
        message === 'You win!' &&
        this.levelIndex < this.spec.levels.length - 1
      ) {
        this.time.delayedCall(650, () =>
          this.scene.restart({ levelIndex: this.levelIndex + 1 }),
        )
        return
      }
      this.finished = true
      this.state = message === 'Game over' || message === 'Time up' ? 'lost' : 'won'
      this.pipeSpawner?.remove(false)
      this.enemySpawner?.remove(false)
      this.physics.pause()
      this.updateStatus()
      const isLost = this.state === 'lost'
      const endMessage = isLost
        ? this.ui.game_over_message || message
        : this.ui.victory_message || message
      this.endText = this.add
        .text(WIDTH / 2, HEIGHT / 2, `${endMessage} · Press R to restart`, {
          color: '#ffffff',
          fontFamily: 'system-ui',
          fontSize: '28px',
          fontStyle: 'bold',
        })
        .setOrigin(0.5)
        .setScrollFactor(0)
    }

    updateGoalLock() {
      const level = this.spec.levels[this.levelIndex]
      const gatedGoal = this.spec.game_type === 'maze' ||
        this.spec.game_type === 'top_down_collection' ||
        (this.spec.game_type === 'platform_adventure' &&
          this.spec.win_condition.type === 'reach_goal' &&
          level.collectible_target > 0)
      if (!gatedGoal) return
      this.goalUnlocked = canReachGoal(
        this.spec.game_type,
        this.spec.win_condition,
        this.collected,
        level.collectible_target,
      )
      if (this.goalLabel) {
        this.goalLabel.setText(this.goalUnlocked ? 'UNLOCKED' : 'LOCKED')
        this.goalLabel.setColor(this.goalUnlocked ? '#d9ffb3' : '#fff4c2')
      }
    }

    updateStatus() {
      const level = this.spec.levels[this.levelIndex]
      const limit = level.time_limit || (
        this.spec.win_condition.type === 'survive'
          ? this.spec.win_condition.target
          : this.spec.lose_condition.target
      )
      const remaining = limit && this.startedAt !== null
        ? Math.max(
          0,
          Math.ceil((limit * 1000 - (this.time.now - this.startedAt)) / 1000),
        )
        : limit || null
      const objective = this.spec.win_condition.type === 'survive'
        ? this.ui.show_timer === false
          ? this.ui.objective_label || 'Survive'
          : `${this.ui.objective_label || 'Time'} ${remaining ?? '-'}s`
        : this.spec.win_condition.type === 'defeat_target'
        ? `Defeated ${this.defeatedEnemies}/${this.spec.win_condition.target ?? '-'}`
        : this.isFlappy
          ? `Pipes ${this.score}`
          : this.isRacing || this.spec.win_condition.type === 'survive'
            ? `Time ${remaining ?? '-'}s`
            : `Items ${this.collected}/${this.spec.win_condition.type === 'collect_target'
              ? this.spec.win_condition.target
              : level.collectible_target || '-'}`
      const hud = [this.spec.game_type.replaceAll('_', ' ')]
      if (this.ui.show_score !== false) hud.push(`Score ${this.score}`)
      if (this.ui.show_health !== false) hud.push(`Health ${this.health}`)
      hud.push(objective, this.state)
      this.statusText.setText(hud.join('   '))
      this.game.canvas.dataset.gameState = this.state
      this.game.canvas.dataset.score = String(this.score)
      this.game.canvas.dataset.health = String(this.health)
      this.game.canvas.dataset.collected = String(this.collected)
      this.game.canvas.dataset.defeatedEnemies = String(this.defeatedEnemies)
      this.game.canvas.dataset.hazards = String(this.hazards?.countActive() || 0)
      this.game.canvas.dataset.projectiles = String(this.projectiles.countActive())
      this.game.canvas.dataset.playerX = String(Math.round(this.player.x))
      this.game.canvas.dataset.playerY = String(Math.round(this.player.y))
      this.game.canvas.dataset.playerPresent = String(Boolean(this.player?.active))
      this.game.canvas.dataset.playerGrounded = String(
        Boolean(this.player?.body?.blocked.down),
      )
      this.game.canvas.dataset.playerVelocityY = String(
        Math.round(this.player?.body?.velocity?.y || 0),
      )
      this.game.canvas.dataset.jumpCount = String(this.jumpCount)
      this.game.canvas.dataset.collectibleObjects = String(this.collectibles?.countActive() || 0)
      this.game.canvas.dataset.collectiblePositions = JSON.stringify(
        this.collectibles?.getChildren()
          .filter((item) => item.active)
          .map((item) => ({
            x: Math.round(item.x),
            y: Math.round(item.y),
          })) || [],
      )
      this.game.canvas.dataset.enemyObjects = String(this.enemies?.countActive() || 0)
      this.game.canvas.dataset.targetObjects = String(this.targets?.countActive() || 0)
      this.game.canvas.dataset.goalPresent = String(Boolean(this.goalSprite?.active))
      this.game.canvas.dataset.obstacleObjects = String(
        this.isFlappy ? this.obstacleColumns.size : this.obstacles?.countActive() || 0,
      )
      this.game.canvas.dataset.hazardObjects = String(this.hazards?.countActive() || 0)
      this.game.canvas.dataset.platformObjects = String(this.renderEntities.platforms.length)
      this.game.canvas.dataset.wallObjects = String(this.renderEntities.walls.length)
      this.game.canvas.dataset.sceneryObjects = String(this.renderEntities.scenery.length)
      if (this.isRacing) {
        const limit = this.raceDurationSeconds
        const remaining = Math.max(0, Math.ceil(limit - this.raceElapsedMs / 1000))
        this.game.canvas.dataset.lap = String(this.raceCurrentLap)
        this.game.canvas.dataset.lapsCompleted = String(this.raceLapsCompleted)
        this.game.canvas.dataset.opponents = String(this.racingOpponents.countActive())
        this.statusText.setText(
          `Racing   Score ${this.score}   Health ${this.health}   Lap ${this.raceCurrentLap}/${this.raceTotalLaps}   Time ${remaining}s   ${this.state}`,
        )
      }
    }

    fireProjectile() {
      if (!mayResolveGameplay(this.state) || this.time.now < this.nextFireAt) return
      this.nextFireAt = this.time.now + this.spec.player.fire_cooldown
      const horizontal = this.aimDirection.x !== 0
      const projectile = this.add.rectangle(
        this.player.x + this.aimDirection.x * 24,
        this.player.y + this.aimDirection.y * 24,
        horizontal ? 20 : 6,
        horizontal ? 6 : 20,
        colorFromHex(this.palette.accent),
      ).setRotation(horizontal ? Math.PI / 2 : 0)
      projectile.damage = 1
      this.physics.add.existing(projectile)
      this.projectiles.add(projectile)
      projectile.body.setSize(horizontal ? 20 : 6, horizontal ? 6 : 20)
      projectile.body.setAllowGravity(false)
      projectile.body.setVelocity(
        this.aimDirection.x * 520,
        this.aimDirection.y * 520,
      )
    }

    update(_time, delta) {
      if (this.finished) {
        return
      }
      if (this.state === 'ready') {
        this.updateStatus()
        return
      }
      const left = this.isShooter
        ? this.shooterKeysDown.has('ArrowLeft') || this.shooterKeysDown.has('KeyA')
        : this.cursors.left.isDown || this.keys.left.isDown
      const right = this.isShooter
        ? this.shooterKeysDown.has('ArrowRight') || this.shooterKeysDown.has('KeyD')
        : this.cursors.right.isDown || this.keys.right.isDown
      const jump = this.isPlatformer &&
        (this.cursors.up.isDown || this.keys.up.isDown || this.keys.space.isDown)
      const up = this.isShooter
        ? this.shooterKeysDown.has('ArrowUp') || this.shooterKeysDown.has('KeyW')
        : this.cursors.up.isDown || this.keys.up.isDown
      const down = this.isShooter
        ? this.shooterKeysDown.has('ArrowDown') || this.shooterKeysDown.has('KeyS')
        : this.cursors.down.isDown || this.keys.down.isDown
      const fireKey = {
        space: 'Space',
        enter: 'Enter',
        x: 'KeyX',
      }[this.spec.fire_control]
      this.aimDirection = aimDirectionFromInput(
        { left, right, up, down },
        this.aimDirection,
      )
      if (this.isShooter && fireKey && this.shooterKeysDown.has(fireKey)) {
        this.fireProjectile()
      }
      const input = { left, right, up, down, jump }
      if (this.isPlatformer) {
        runRuntimeCapability(this, 'platform_movement', input)
      } else if (this.hasCapability('shooter_movement')) {
        runRuntimeCapability(this, 'shooter_movement', input)
      } else if (this.isRacing) {
        runRuntimeCapability(this, 'racing_movement', input, delta)
      } else if (this.isFlappy) {
        runRuntimeCapability(this, 'flapping', input)
      } else {
        runRuntimeCapability(this, 'four_direction_movement', input)
      }
      if (this.isRacing && this.racingBoundaryContact) {
        this.applyPlayerDamage(1)
        this.applyRacingSpeedPenalty()
      }
      this.platforms.children.iterate((platform) => {
        if (platform.movement === 'horizontal') platform.x += 0.45
        if (platform.movement === 'vertical') platform.y += 0.35
        if (platform.body && platform.movement !== 'static') platform.body.updateFromGameObject()
      })
      this.enemies.children.iterate((enemy) => {
        if (enemy.movement === 'horizontal') enemy.body.setVelocityX(45)
        if (enemy.movement === 'vertical') enemy.body.setVelocityY(35)
      })
      runRuntimeCapability(this, 'chasing_enemies')
      this.obstacles.children.iterate((obstacle) => {
        if (this.isRacing) {
          return
        } else if (!this.isFlappy && (obstacle.x < -40 || obstacle.y > this.worldHeight + 40)) {
          obstacle.destroy()
        }
      })
      runRuntimeCapability(this, 'racing_obstacles', delta)
      if (this.isFlappy) {
        runRuntimeCapability(this, 'scrolling_obstacles', delta)
      }
      this.goalArmed = this.goalArmed || mayArmGoal(
        this.player.x / this.worldWidth,
        this.player.y / this.worldHeight,
        this.startPosition.x,
        this.startPosition.y,
      )
      if (this.isFlappy && (
        this.player.y - this.player.height / 2 <= 0 ||
        this.player.y + this.player.height / 2 >= this.worldHeight * 0.94
      )) {
        this.hitObstacle(this.player, { damage: 1 })
      }
      for (const projectile of [...this.projectiles.getChildren()]) {
        if (!projectile?.active) continue
        if (this.isShooter) continue
        if (projectile.active && projectile.y < -30) projectile.destroy()
      }
      runRuntimeCapability(this, 'projectiles')
      this.updateStatus()
      runRuntimeCapability(this, 'timer')
      this.updateStatus()
    }
  }
}

export default function PhaserPreview({ spec, gameDefinition }) {
  const containerRef = useRef(null)
  const runtimeConfig = useMemo(
    () => runtimeConfigFromDefinition(gameDefinition, spec.runtime_config || spec),
    [gameDefinition, spec],
  )

  useEffect(() => {
    if (!containerRef.current || !runtimeConfig) return undefined
    const container = containerRef.current
    let game
    let cancelled = false
    queueMicrotask(() => {
      if (cancelled) return
      game = new Phaser.Game({
        type: Phaser.AUTO,
        width: WIDTH,
        height: HEIGHT,
        parent: container,
        backgroundColor: runtimeConfig.palette?.background || '#172033',
        physics: {
          default: 'arcade',
          arcade: {
            gravity: {
              y: runtimeConfig.player.gravity ?? (
                runtimeConfig.player.movement_mode === 'platformer' ? 900 : 0
              ),
            },
            debug: false,
          },
        },
        scene: createPreviewScene({
          ...runtimeConfig,
          win_condition: {
            type: typeof runtimeConfig.win_condition === 'string'
              ? runtimeConfig.win_condition
              : runtimeConfig.win_condition.type,
            target: runtimeConfig.win_target
              ?? (typeof runtimeConfig.win_condition === 'object'
                ? runtimeConfig.win_condition.target
                : undefined),
          },
          lose_condition: {
            type: typeof runtimeConfig.lose_condition === 'string'
              ? runtimeConfig.lose_condition
              : runtimeConfig.lose_condition.type,
            target: runtimeConfig.lose_target
              ?? (typeof runtimeConfig.lose_condition === 'object'
                ? runtimeConfig.lose_condition.target
                : undefined),
          },
        }),
        scale: { mode: Phaser.Scale.FIT, autoCenter: Phaser.Scale.CENTER_BOTH },
      })
      game.canvas.tabIndex = 0
    })
    return () => {
      cancelled = true
      if (game) {
        game.destroy(true)
        game.canvas.remove()
      }
    }
  }, [runtimeConfig, gameDefinition])

  return <div className="preview-canvas" ref={containerRef} aria-label="Generated game preview" />
}
