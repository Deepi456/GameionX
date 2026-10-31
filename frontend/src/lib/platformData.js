export const NAV_ITEMS = [
  { id: 'home', label: 'Home' },
  { id: 'create', label: 'Create Game' },
  { id: 'my-games', label: 'My Games' },
  { id: 'templates', label: 'Templates' },
  { id: 'agents', label: 'Agents' },
  { id: 'time-machine', label: 'Game Time Machine' },
  { id: 'community', label: 'Community' },
  { id: 'analytics', label: 'Analytics' },
  { id: 'settings', label: 'Settings' },
]

export const HOME_TEMPLATE_CARDS = [
  {
    id: 'platform-adventure',
    title: 'Platform Adventure',
    category: 'Platformer',
    description: 'Explore floating ruins and collect relics in a skybound journey.',
    theme: 'platform',
    image: '/assets/templates/platform.png',
  },
  {
    id: 'top-down-forest',
    title: 'Top Down Forest',
    category: 'Exploration',
    description: 'Sneak through enchanted woods and track glowing creatures.',
    theme: 'forest',
    image: '/assets/templates/top-down-forest.png',
  },
  {
    id: 'maze-escape',
    title: 'Maze Escape',
    category: 'Puzzle',
    description: 'Navigate shifting corridors and outsmart the maze patrol.',
    theme: 'maze',
    image: '/assets/templates/maze-escape.png',
  },
  {
    id: 'space-shooter',
    title: 'Space Shooter',
    category: 'Action',
    description: 'Protect your ship while dodging asteroid cannon fire.',
    theme: 'space',
    image: '/assets/templates/space-shooter.png',
  },
  {
    id: 'racing',
    title: 'Racing',
    category: 'Arcade',
    description: 'Push your drift lines and stay ahead of the pack.',
    theme: 'racing',
    image: '/assets/templates/racing.png',
  },
  {
    id: 'flappy-bird-style',
    title: 'Flappy Bird Style',
    category: 'Arcade',
    description: 'Tap to glide, weave through hazards, and chase a new high score.',
    theme: 'bird',
    image: '/assets/templates/flappy.png',
  },
]

export const GAME_STYLE_OPTIONS = [
  { id: 'platformer', name: 'Platformer', icon: '⛰' },
  { id: 'top-down', name: 'Top Down', icon: '◉' },
  { id: 'maze', name: 'Maze', icon: '▦' },
  { id: 'space-shooter', name: 'Space Shooter', icon: '✦' },
  { id: 'racing', name: 'Racing', icon: '⟡' },
  { id: 'collection', name: 'Collection', icon: '⬢' },
]

export const STARTER_TEMPLATES = [
  {
    id: 'neon-maze',
    title: 'Neon Maze Sprint',
    category: 'Arcade',
    style: 'Maze',
    difficulty: 'Intermediate',
    description: 'Dodge drones and escape the undercity grid in a tight, pulse-driven run.',
  },
  {
    id: 'sky-drift',
    title: 'Sky Drift Rush',
    category: 'Racing',
    style: 'Racing',
    difficulty: 'Advanced',
    description: 'Launch through a neon skyline, dodge traffic, and chain boost gates.',
  },
  {
    id: 'vault-legend',
    title: 'Vault Legend',
    category: 'Adventure',
    style: 'Platformer',
    difficulty: 'Beginner',
    description: 'Guide a solo hero through floating ruins and gem-rich caverns.',
  },
  {
    id: 'orbital-raid',
    title: 'Orbital Raid',
    category: 'Shooter',
    style: 'Space Shooter',
    difficulty: 'Advanced',
    description: 'Defend the signal relay while enemy squadrons breach the outer ring.',
  },
]

export const AGENT_CARDS = [
  {
    name: 'Game Designer Agent',
    responsibility: 'Shapes the game fantasy, level structure, mechanics, and creative brief.',
    status: 'Implemented',
    workflow: 'Prompt analysis → concept framing → game design spec',
  },
  {
    name: 'Game Builder Agent',
    responsibility: 'Converts the design into runtime-ready configuration and playable systems.',
    status: 'Implemented',
    workflow: 'Runtime generation → config validation → integration',
  },
  {
    name: 'QA Agent',
    responsibility: 'Checks structural validity, gameplay safety, and browser-play behavior.',
    status: 'Implemented',
    workflow: 'Structure tests → gameplay validation → repair loops',
  },
  {
    name: 'GameionX Workflow Orchestrator',
    responsibility: 'Coordinates the workflow and decides when to iterate, patch, or finalize.',
    status: 'Implemented',
    workflow: 'Prompt input → agent routing → final verification',
  },
]

export const COMMUNITY_GAMES = [
  { title: 'Solar Drift', creator: '@nova_arcade', genre: 'Racing', likes: 2650 },
  { title: 'Echo Vault', creator: '@pixel_fable', genre: 'Platformer', likes: 1824 },
  { title: 'Luna Circuit', creator: '@luma_lab', genre: 'Top Down', likes: 2301 },
  { title: 'Void Harvest', creator: '@orbitalstudios', genre: 'Collection', likes: 1994 },
]

export const TIME_MACHINE_SNAPSHOTS = [
  { version: 'v1.2', label: 'Playable prototype', timestamp: '2026-10-25T20:45:00Z' },
  { version: 'v1.5', label: 'Polished gameplay loop', timestamp: '2026-10-27T11:10:00Z' },
  { version: 'v1.8', label: 'AI-generated booster pass', timestamp: '2026-10-29T18:20:00Z' },
]

export function getAnalyticsSnapshot(games = []) {
  const totalGames = games.length
  const publishedGames = games.filter((game) => game.status === 'ready').length
  const inProgressGames = games.filter((game) => ['unverified', 'warning'].includes(game.status)).length
  const averages = games.reduce(
    (accumulator, game) => {
      if (!game.spec?.levels?.length) return accumulator
      return accumulator + game.spec.levels.length
    },
    0,
  )
  const generationRate = totalGames === 0 ? 0 : Math.round((publishedGames / totalGames) * 100)
  const storageUsed = Math.min(96, 12 + games.length * 9)

  return {
    totalGames,
    publishedGames,
    inProgressGames,
    generationRate,
    averageLevels: totalGames === 0 ? 0 : Math.round(averages / totalGames),
    storageUsed,
  }
}
