
import { useEffect, useRef, useState } from 'react'
import {
  ArrowRight,
  BarChart3,
  Bell,
  Bot,
  ChevronDown,
  Clock3,
  Crown,
  Eye,
  EyeOff,
  FolderKanban,
  Gamepad2,
  History,
  Home,
  LayoutGrid,
  LockKeyhole,
  LogOut,
  Mail,
  Search,
  Settings,
  Sparkles,
  Users,
  Zap,
} from 'lucide-react'
import PhaserPreview from './components/PhaserPreview'
import {
  AGENT_CARDS,
  COMMUNITY_GAMES,
  GAME_STYLE_OPTIONS,
  HOME_TEMPLATE_CARDS,
  NAV_ITEMS,
  STARTER_TEMPLATES,
  TIME_MACHINE_SNAPSHOTS,
  getAnalyticsSnapshot,
} from './lib/platformData'
import { resolveApiBaseUrl } from './lib/apiBaseUrl'
import './App.css'
import './theme.css'

const API_BASE_URL = resolveApiBaseUrl(
  import.meta.env.VITE_API_BASE_URL,
  window.location.hostname,
)
const GENERATION_TIMEOUT_MS = 150_000
const STAGE_LABELS = {
  planning: 'Understanding prompt',
  designing: 'Designing game structure',
  gameplay_logic: 'Building the game',
  world_ui_building: 'Preparing game UI',
  runtime_integration: 'Preparing playable preview',
  validating: 'Structural validation',
  qa_playtest: 'Testing gameplay',
  repairing: 'Repairing diagnosed failure',
  qa_retest: 'Retesting repaired game',
  finalizing: 'Finalising result',
  ready: 'Final result',
  unverified: 'Final result · unverified',
  failed: 'Workflow stopped',
}

function stageStatus(status) {
  return {
    started: 'Running',
    completed: 'Passed',
    failed: 'Failed',
    skipped: 'Not tested',
  }[status] || 'Pending'
}

function stageClass(status) {
  return {
    started: 'running',
    completed: 'passed',
    failed: 'failed',
    skipped: 'not-tested',
  }[status] || 'pending'
}

function compactStages(stages) {
  return stages.reduce((items, stage) => {
    const previous = items.at(-1)
    if (previous?.name === stage.name && previous.status === 'started') {
      items[items.length - 1] = stage
    } else {
      items.push(stage)
    }
    return items
  }, [])
}

function GoogleLogo(props) {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true" {...props}>
      <path fill="#EA4335" d="M12 10.2v3.9h5.4c-.2 1.3-.9 2.4-1.9 3.1l3.1 2.4c1.8-1.7 2.8-4.2 2.8-7.2 0-.7-.1-1.3-.2-1.9H12Z" />
      <path fill="#34A853" d="M12 21.2c2.6 0 4.8-.9 6.4-2.3l-3.1-2.4c-.8.6-1.9 1-3.3 1-2.5 0-4.6-1.7-5.3-4h-3.5v2.5A9.2 9.2 0 0 0 12 21.2Z" />
      <path fill="#FBBC05" d="M6.7 17.6A5.7 5.7 0 0 1 6.3 15v-2.5H2.8A9.2 9.2 0 0 0 2.2 12c0 1.5.4 2.9 1.1 4.1l3.4-2.5Z" />
      <path fill="#4285F4" d="M12 4.8c1.4 0 2.6.5 3.6 1.3l2.7-2.7A9.2 9.2 0 0 0 12 2a9.2 9.2 0 0 0-8.2 4.6l3.5 2.5c.7-2.3 2.8-4 4.7-4Z" />
    </svg>
  )
}

function GitHubLogo(props) {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true" {...props}>
      <path fill="currentColor" d="M12 .5a12 12 0 0 0-3.8 23.4c.6.1.8-.2.8-.6v-2.2c-3.3.7-4-1.4-4-1.4-.5-1.3-1.2-1.7-1.2-1.7-1-.7.1-.7.1-.7 1.1.1 1.7 1.1 1.7 1.1 1 .1 2.1-.7 2.7-1.4.1-.9.5-1.5 1-1.9-2.6-.3-5.4-1.3-5.4-5.9 0-1.3.5-2.4 1.2-3.3-.1-.3-.5-1.6.1-3.2 0 0 1-.3 3.3 1.2a11.5 11.5 0 0 1 6 0c2.2-1.5 3.3-1.2 3.3-1.2.6 1.6.2 2.9.1 3.2.8.9 1.2 2 1.2 3.3 0 4.6-2.8 5.5-5.5 5.9.5.5.8 1.2.8 2.3v3.1c0 .5.2.8.8.6A12 12 0 0 0 12 .5Z" />
    </svg>
  )
}

function effectiveStageStatus(stage, playtestReport) {
  if (stage.name === 'qa_playtest') {
    if (playtestReport?.status === 'fail') return 'failed'
    if (playtestReport?.status === 'pass') return 'completed'
    if (playtestReport?.status === 'unverified' || stage.message.toLowerCase().includes('unavailable')) {
      return 'skipped'
    }
  }
  return stage.status
}

function apiErrorMessage(detail, fallback) {
  if (typeof detail === 'string') return detail
  if (Array.isArray(detail)) {
    return detail
      .map((issue) => {
        const location = Array.isArray(issue.loc) ? issue.loc.slice(1).join('.') : ''
        return location ? `${location}: ${issue.msg}` : issue.msg
      })
      .filter(Boolean)
      .join(' ')
  }
  return fallback
}

function isGenerationRetryableError(response, body) {
  return (
    response?.status === 429 ||
    response?.status === 503 ||
    body?.code === 'llm_unavailable'
  )
}

function getControlHint(gameType, fireControl) {
  if (gameType === 'flappy_dodge') return 'Space / ↑ to flap · avoid the pipes'
  if (gameType === 'space_shooter') {
    const fireKey = { space: 'Space', enter: 'Enter', x: 'X' }[fireControl] || 'Space'
    return `Arrow keys / WASD to move · ${fireKey} to fire`
  }
  if (gameType === 'racing') return '← / → to steer · avoid traffic'
  if (gameType === 'platform_adventure') return '← / → to move · W / ↑ / Space to jump'
  return 'Arrow keys / WASD to move'
}

function formatDate(value) {
  if (!value) return '—'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  return new Intl.DateTimeFormat(undefined, {
    month: 'short',
    day: 'numeric',
    year: 'numeric',
  }).format(date)
}

function projectStatusLabel(status) {
  if (status === 'ready') return 'Verified'
  if (status === 'failed') return 'Failed'
  return 'Unverified'
}

function projectStatusVariant(status) {
  if (status === 'ready') return 'success'
  if (status === 'failed') return 'danger'
  return 'warning'
}

function SpecSummary({ spec, provider, qaReport, gameDefinition }) {
  const level = spec.levels[0]
  return (
    <div className="spec-summary glass-card">
      <div className="spec-heading">
        <div>
          <p className="eyebrow">Validated GameSpec v{spec.schema_version}</p>
          <h3>{spec.title}</h3>
        </div>
        <span className="provider-badge">{provider}</span>
      </div>
      <p>{spec.description}</p>
      <div className={`qa-panel qa-${qaReport?.status || 'unverified'}`}>
        <strong>Structural QA: {qaReport?.structural_status || qaReport?.status || 'Unverified'}</strong>
        <p>{qaReport?.summary || 'No QA summary returned yet.'}</p>
      </div>
      <div className="spec-grid">
        <span>Levels<strong>{spec.levels.length}</strong></span>
        <span>Platforms<strong>{level?.platforms?.length || 0}</strong></span>
        <span>Collectibles<strong>{level?.collectibles?.length || 0}</strong></span>
        <span>Enemies<strong>{level?.enemies?.length || 0}</strong></span>
        <span>Max health<strong>{spec.player?.max_health || 0}</strong></span>
        <span>Game type<strong>{(gameDefinition?.genre || spec.game_type).replaceAll('_', ' ')}</strong></span>
        <span>Win goal<strong>{spec.win_condition?.type || 'Objective'}</strong></span>
      </div>
      <details>
        <summary>View validated JSON</summary>
        <pre>{JSON.stringify(spec, null, 2)}</pre>
      </details>
    </div>
  )
}

function GamePreview({
  spec,
  gameDefinition,
  status,
  qaStatus,
  previewKey,
  frameRef,
  onRestart,
  onFullscreen,
}) {
  const controlHint = gameDefinition?.ui?.instructions || getControlHint(spec.game_type, spec.runtime_config?.fire_control)
  return (
    <section className="preview-card glass-card">
      <div className="preview-header">
        <div>
          <span className="eyebrow">Playable preview</span>
          <h3>{spec.title}</h3>
        </div>
        <span className="control-hint">{controlHint}</span>
      </div>
      <div className="preview-toolbar">
        <span className={`status-pill ${status === 'ready' ? 'success' : status === 'error' ? 'danger' : 'warning'}`}>
          {status === 'ready' ? 'Gameplay verified' : status === 'error' ? qaStatus === 'fail' ? 'Gameplay checks failed' : 'Workflow failed' : 'Gameplay unverified'}
        </span>
        <div className="inline-actions">
          <button type="button" className="secondary-button" onClick={onRestart}>Restart</button>
          <button type="button" className="secondary-button" onClick={onFullscreen}>Fullscreen</button>
        </div>
      </div>
      <div className="preview-frame" ref={frameRef}>
        <PhaserPreview key={previewKey} spec={spec} gameDefinition={gameDefinition} />
      </div>
      {status === 'unverified' && (
        <p className="inline-note" role="status">
          The game is playable in this preview, but QA and browser checks may still be pending.
        </p>
      )}
      {status === 'ready' && (
        <p className="inline-success" role="status">
          Backend workflow confirms required runtime verification.
        </p>
      )}
    </section>
  )
}

function App() {
  const [mode] = useState('beginner')
  const [difficulty, setDifficulty] = useState('Intermediate')
  const [gameStyle, setGameStyle] = useState('platformer')
  const [prompt, setPrompt] = useState('A neon platform adventure where I collect gems, avoid enemies, and reach the goal.')
  const [spec, setSpec] = useState(null)
  const [gameDefinition, setGameDefinition] = useState(null)
  const [provider, setProvider] = useState(null)
  const [qaReport, setQaReport] = useState(null)
  const [status, setStatus] = useState('idle')
  const [error, setError] = useState('')
  const [stages, setStages] = useState([])
  const [workflowState, setWorkflowState] = useState(null)
  const [user, setUser] = useState(null)
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [authMode, setAuthMode] = useState('login')
  const [authChecked, setAuthChecked] = useState(false)
  const [showPassword, setShowPassword] = useState(false)
  const [authError, setAuthError] = useState('')
  const [fieldErrors, setFieldErrors] = useState({ email: '', password: '' })
  const [authBusy, setAuthBusy] = useState(false)
  const [accountError, setAccountError] = useState('')
  const [savedGames, setSavedGames] = useState([])
  const [activePage, setActivePage] = useState('home')
  const [generationElapsed, setGenerationElapsed] = useState(0)
  const [generationStartedAt, setGenerationStartedAt] = useState(null)
  const [projectId, setProjectId] = useState(null)
  const [saveBusy, setSaveBusy] = useState(false)
  const [projectBusy, setProjectBusy] = useState(false)
  const [previewKey, setPreviewKey] = useState(0)
  const [projectActionError, setProjectActionError] = useState('')
  const [generationRetryable, setGenerationRetryable] = useState(false)
  const [apiHealth, setApiHealth] = useState({ status: 'checking', provider: null })
  const [searchTerm, setSearchTerm] = useState('')
  const [sortBy, setSortBy] = useState('updated')
  const [projectFilter, setProjectFilter] = useState('all')
  const [templatesSearch, setTemplatesSearch] = useState('')
  const previewFrameRef = useRef(null)

  const NAV_ICONS = {
    home: Home,
    create: LayoutGrid,
    'my-games': FolderKanban,
    templates: Sparkles,
    agents: Bot,
    'time-machine': History,
    community: Users,
    analytics: BarChart3,
    settings: Settings,
  }

  async function loadAccount() {
    const response = await fetch(`${API_BASE_URL}/api/auth/me`, { credentials: 'include' })
    if (response.status === 401) {
      setUser(null)
      setSavedGames([])
      return
    }
    if (!response.ok) throw new Error('Your account could not be loaded.')
    const account = await response.json()
    setUser(account)
    const projectsResponse = await fetch(`${API_BASE_URL}/api/projects`, { credentials: 'include' })
    if (!projectsResponse.ok) {
      throw new Error('Your saved projects could not be loaded. Please try again.')
    }
    setSavedGames(await projectsResponse.json())
  }

  useEffect(() => {
    loadAccount().catch((requestError) => {
      setAccountError(requestError.message || 'Unable to reach GameionX.')
    }).finally(() => setAuthChecked(true))
    fetch(`${API_BASE_URL}/api/health`)
      .then(async (response) => {
        const body = await response.json().catch(() => ({}))
        if (!response.ok) throw new Error(body.detail || 'API health check failed.')
        setApiHealth({ status: 'connected', provider: body.provider })
      })
      .catch(() => setApiHealth({ status: 'unavailable', provider: null }))
  }, [])

  useEffect(() => {
    if (status !== 'loading' || !generationStartedAt) return undefined
    const timerId = window.setInterval(() => {
      setGenerationElapsed(Math.floor((Date.now() - generationStartedAt) / 1000))
    }, 1000)
    return () => window.clearInterval(timerId)
  }, [status, generationStartedAt])

  async function generateGame(event, { bypassCache = false } = {}) {
    event?.preventDefault?.()
    if (status === 'loading') return
    const normalizedPrompt = prompt.trim()
    if (normalizedPrompt.length < 3) {
      setError('Describe your game in at least 3 characters.')
      setGenerationRetryable(false)
      setStatus('error')
      return
    }
    setStatus('loading')
    setGenerationRetryable(false)
    setActivePage('create')
    const startedAt = Date.now()
    setGenerationStartedAt(startedAt)
    setGenerationElapsed(0)
    setError('')
    setSpec(null)
    setGameDefinition(null)
    setProvider(null)
    setQaReport(null)
    setWorkflowState(null)
    setProjectId(null)
    setStages([
      {
        name: 'request_pending',
        status: 'started',
        message: 'Generation request submitted. The backend does not stream internal stage updates.',
      },
    ])
    const controller = new AbortController()
    const timeoutId = setTimeout(() => controller.abort(), GENERATION_TIMEOUT_MS)
    try {
      const response = await fetch(`${API_BASE_URL}/api/generate`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'include',
        body: JSON.stringify({ prompt: normalizedPrompt, mode, bypass_cache: bypassCache }),
        signal: controller.signal,
      })
      const body = await response.json().catch(() => ({}))
      if (!response.ok) {
        const retryableError = isGenerationRetryableError(response, body)
        setStages(body.workflow?.stage_history || [])
        setWorkflowState(body.workflow || null)
        const partial = body.partial_result
        if (partial?.spec && partial?.runtime_config) {
          const generated = {
            ...partial,
            spec: { ...partial.spec, runtime_config: partial.runtime_config },
          }
          setSpec(generated.spec)
          setGameDefinition(generated.game_definition || null)
          setProvider(generated.provider || null)
          setQaReport(generated.qa_report || null)
          setWorkflowState(generated.workflow || body.workflow || null)
          setPrompt(normalizedPrompt)
          await persistProject(generated, user)
        }
        if (retryableError) {
          const friendlyMessage = 'The AI is busy right now. Please try again in a minute.'
          setError(friendlyMessage)
          setGenerationRetryable(true)
          setStatus('error')
          return
        }
        throw new Error(apiErrorMessage(body.message ?? body.detail, 'Game generation failed.'))
      }
      setSpec({ ...body.spec, runtime_config: body.runtime_config })
      setGameDefinition(body.game_definition || null)
      setProvider(body.provider)
      setQaReport(body.qa_report)
      setStages(body.stages || [])
      setWorkflowState(body.workflow || null)
      setPrompt(normalizedPrompt)
      setGenerationElapsed(Math.floor((Date.now() - startedAt) / 1000))
      setStatus(
        body.workflow?.final_status === 'ready' && body.workflow?.runtime_verified ? 'ready' : 'unverified',
      )
      await persistProject({
        provider: body.provider,
        prompt: normalizedPrompt,
        spec: { ...body.spec, runtime_config: body.runtime_config },
        runtime_config: body.runtime_config,
        game_definition: body.game_definition || null,
        qa_report: body.qa_report,
      }, user)
    } catch (requestError) {
      setStatus('error')
      if (requestError.name === 'AbortError') {
        setGenerationRetryable(false)
        setError('Game generation timed out after 150 seconds. The configured AI provider may be busy; please try again.')
      } else {
        setGenerationRetryable(false)
        setError(requestError.message || `Game generation failed at ${API_BASE_URL}.`)
      }
    } finally {
      clearTimeout(timeoutId)
      setStatus((currentStatus) => (currentStatus === 'loading' ? 'idle' : currentStatus))
    }
  }

  function retryGeneration() {
    setGenerationRetryable(false)
    setError('')
    generateGame()
  }

  function validateAuthFields(nextEmail = email, nextPassword = password) {
    const nextErrors = { email: '', password: '' }
    const normalizedEmail = nextEmail.trim()

    if (!normalizedEmail) {
      nextErrors.email = 'Email is required.'
    } else if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(normalizedEmail)) {
      nextErrors.email = 'Enter a valid email address.'
    }

    if (!nextPassword) {
      nextErrors.password = 'Password is required.'
    } else if (nextPassword.length < 8) {
      nextErrors.password = 'Password must be at least 8 characters.'
    }

    setFieldErrors(nextErrors)
    return !nextErrors.email && !nextErrors.password
  }

  async function authenticate(event) {
    event.preventDefault()
    setAuthError('')
    if (!validateAuthFields()) {
      return
    }
    setAuthBusy(true)
    try {
      const response = await fetch(`${API_BASE_URL}/api/auth/${authMode}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'include',
        body: JSON.stringify({ email: email.trim(), password }),
      })
      const body = await response.json()
      if (!response.ok) {
        throw new Error(apiErrorMessage(body.detail, 'We could not verify those details. Please try again.'))
      }
      setUser(body.user)
      setPassword('')
      setAuthError('')
      await loadAccount()
      if (spec && !projectId) {
        await persistProject({
          provider,
          prompt,
          spec,
          runtime_config: spec.runtime_config,
          game_definition: gameDefinition,
          qa_report: qaReport,
        }, body.user)
      }
    } catch (requestError) {
      setAuthError(requestError.message || 'Unable to reach GameionX. Check your connection.')
    } finally {
      setAuthBusy(false)
    }
  }

  async function persistProject(project, owner = user) {
    if (!owner || !project?.spec || saveBusy) return
    setSaveBusy(true)
    setAccountError('')
    try {
      const specForStorage = { ...project.spec }
      delete specForStorage.runtime_config
      const projectPayload = {
        provider: project.provider,
        prompt: project.prompt,
        spec: specForStorage,
        runtime_config: project.runtime_config || project.spec.runtime_config,
        game_definition: project.game_definition || null,
        qa_report: project.qa_report,
      }
      const response = await fetch(`${API_BASE_URL}/api/projects`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'include',
        body: JSON.stringify(projectPayload),
      })
      const body = await response.json().catch(() => ({}))
      if (!response.ok) throw new Error(apiErrorMessage(body.detail, 'The generated project could not be saved.'))
      setProjectId(body.id)
      await loadAccount()
    } catch (requestError) {
      setAccountError(requestError.message || 'Unable to reach GameionX.')
    } finally {
      setSaveBusy(false)
    }
  }

  async function openProject(project) {
    setProjectBusy(true)
    setProjectActionError('')
    try {
      const response = await fetch(`${API_BASE_URL}/api/projects/${project.id}`, { credentials: 'include' })
      const body = await response.json().catch(() => ({}))
      if (!response.ok) throw new Error(apiErrorMessage(body.detail, 'This project could not be opened.'))
      setPrompt(body.original_prompt || body.prompt)
      setSpec({ ...body.spec, runtime_config: body.runtime_config })
      setGameDefinition(body.game_definition || null)
      setQaReport(body.qa_report || { status: 'unverified', summary: 'Saved QA details are unavailable.', issues: [] })
      setProjectId(body.id)
      setStatus(body.status === 'ready' ? 'ready' : body.status === 'failed' ? 'error' : 'unverified')
      setStages([])
      setActivePage('create')
    } catch (requestError) {
      setProjectActionError(requestError.message || 'Unable to load this project.')
    } finally {
      setProjectBusy(false)
    }
  }

  async function updateProjectTitle(project) {
    const title = window.prompt('New game title', project.title)
    if (!title?.trim()) return
    try {
      const response = await fetch(`${API_BASE_URL}/api/projects/${project.id}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'include',
        body: JSON.stringify({ title: title.trim() }),
      })
      const body = await response.json().catch(() => ({}))
      if (!response.ok) throw new Error(apiErrorMessage(body.detail, 'The project title could not be updated.'))
      await loadAccount()
    } catch (requestError) {
      setProjectActionError(requestError.message || 'Unable to update this project.')
    }
  }

  async function deleteProject(project) {
    if (!window.confirm(`Delete "${project.title}"? This cannot be undone.`)) return
    try {
      const response = await fetch(`${API_BASE_URL}/api/projects/${project.id}`, {
        method: 'DELETE',
        credentials: 'include',
      })
      if (!response.ok) {
        const body = await response.json().catch(() => ({}))
        throw new Error(apiErrorMessage(body.detail, 'The project could not be deleted.'))
      }
      if (projectId === project.id) {
        setProjectId(null)
        setSpec(null)
        setQaReport(null)
        setGameDefinition(null)
      }
      await loadAccount()
    } catch (requestError) {
      setProjectActionError(requestError.message || 'Unable to delete this project.')
    }
  }

  async function logout() {
    setAccountError('')
    try {
      const response = await fetch(`${API_BASE_URL}/api/auth/logout`, {
        method: 'POST',
        credentials: 'include',
      })
      if (!response.ok) throw new Error('You could not be signed out. Please try again.')
      setUser(null)
      setSavedGames([])
      setAuthMode('login')
      setPassword('')
    } catch (requestError) {
      setAccountError(requestError.message || 'Unable to reach GameionX. Check your connection.')
    }
  }

  const activeTitle = NAV_ITEMS.find((item) => item.id === activePage)?.label || 'Home'
  const formatElapsed = (seconds) => `${Math.floor(seconds / 60)}m ${seconds % 60}s`
  const playtestReport = qaReport?.playtest_report || workflowState?.agent_outputs?.find((output) => output.agent === 'BrowserPlaytestRunner')?.output
  const analytics = getAnalyticsSnapshot(savedGames)

  const filteredProjects = savedGames
    .filter((project) => {
      const matchesSearch = !searchTerm || `${project.title} ${project.original_prompt || ''}`.toLowerCase().includes(searchTerm.toLowerCase())
      if (projectFilter === 'verified') return matchesSearch && project.status === 'ready'
      if (projectFilter === 'unverified') return matchesSearch && ['warning', 'unverified'].includes(project.status)
      if (projectFilter === 'failed') return matchesSearch && project.status === 'failed'
      return matchesSearch
    })
    .sort((left, right) => {
      if (sortBy === 'title') {
        return left.title.localeCompare(right.title)
      }
      if (sortBy === 'created') {
        return new Date(right.created_at) - new Date(left.created_at)
      }
      return new Date(right.updated_at) - new Date(left.updated_at)
    })

  const templateList = STARTER_TEMPLATES.filter((template) => {
    const query = templatesSearch.toLowerCase()
    return query.length === 0 || `${template.title} ${template.category} ${template.style}`.toLowerCase().includes(query)
  })

  const timeMachineEntries = (() => {
    if (savedGames.length > 0) {
      return savedGames.slice(0, 4).map((game, index) => ({
        id: game.id,
        title: game.title,
        version: `v${index + 1}.${Math.min(9, game.id % 10)}`,
        summary: game.original_prompt || game.prompt || 'AI-generated gameplay revision',
        created_at: game.updated_at || game.created_at,
      }))
    }
    return TIME_MACHINE_SNAPSHOTS.map((snapshot, index) => ({
      id: index,
      title: `Autosave ${snapshot.version}`,
      version: snapshot.version,
      summary: snapshot.label,
      created_at: snapshot.timestamp,
    }))
  })()

  const showFullscreen = () => {
    const frame = previewFrameRef.current
    if (frame?.requestFullscreen) {
      frame.requestFullscreen().catch(() => setProjectActionError('Fullscreen is unavailable in this browser.'))
    }
  }

  const applyTemplate = (template) => {
    setPrompt(template.description)
    setDifficulty(template.difficulty)
    setGameStyle(template.style.toLowerCase().replace(/\s+/g, '-'))
    setActivePage('create')
  }

    const renderHomePage = () => {
      const homeSearch = searchTerm.trim().toLowerCase()
      const filteredTemplates = HOME_TEMPLATE_CARDS.filter((template) => {
        if (!homeSearch) return true
        return `${template.title} ${template.category} ${template.description}`.toLowerCase().includes(homeSearch)
      })
      const filteredRecentGames = savedGames.filter((project) => {
        if (!homeSearch) return true
        return `${project.title || 'Untitled game'} ${project.original_prompt || ''}`.toLowerCase().includes(homeSearch)
      })

      return (
        <section className="home-page-shell">
          <div className="home-hero-panel">
            <div className="home-hero-copy">
              <p className="eyebrow hero-kicker">Welcome to GameionX</p>
              <h2>
                <span>Describe Your Idea.</span>
                <span className="hero-highlight">Get a Playable Game.</span>
              </h2>
              <p>Turn your imagination into interactive games using AI agents.</p>
          </div>
            <div className="home-hero-visual" aria-hidden="true">
              <img
                src="/assets/home-hero.png"
                alt=""
                onError={(event) => {
                  event.currentTarget.style.display = 'none'
                  event.currentTarget.parentElement.classList.add('hero-fallback')
                }}
              />
            </div>
          </div>

          <div className="home-prompt-card">
            <div className="prompt-card-header">
              <div className="prompt-label"><Sparkles size={16} /> Describe your game idea</div>
            </div>

            <textarea
              value={prompt}
              maxLength={500}
              disabled={status === 'loading'}
              onChange={(event) => setPrompt(event.target.value)}
              placeholder="e.g. Create a platform game with five coins, moving platforms and a treasure chest..."
            />

            <div className="prompt-hint-row">
              <span className="coming-soon-wrap" title="Coming soon">
                <button type="button" className="reference-button" disabled aria-label="Add reference (coming soon)">
                  + Add reference (optional)
                </button>
              </span>
              <button type="button" className="primary-button prompt-button" onClick={generateGame} disabled={status === 'loading'}>
                {status === 'loading' ? 'Generating...' : 'Generate Game'}
                <ArrowRight size={16} />
              </button>
            </div>

            {status === 'error' && (
              <div className="prompt-error-block">
                <p className="error-message">{error}</p>
                {generationRetryable && (
                  <button type="button" className="secondary-button retry-button" onClick={retryGeneration}>
                    Try again
                  </button>
                )}
              </div>
            )}
          </div>

          {spec && (
            <section className="results-grid">
              <GamePreview
                spec={spec}
                gameDefinition={gameDefinition}
                status={status}
                qaStatus={qaReport?.status}
                previewKey={previewKey}
                frameRef={previewFrameRef}
                onRestart={() => setPreviewKey((key) => key + 1)}
                onFullscreen={showFullscreen}
              />
              <SpecSummary spec={spec} provider={provider} qaReport={qaReport} gameDefinition={gameDefinition} />
            </section>
          )}

          <div className="section-header-row">
            <div className="section-heading-inline">
              <Gamepad2 size={18} />
              <h3>Try a Template</h3>
            </div>
            <button type="button" className="section-link" onClick={() => setActivePage('templates')}>
              View All <ArrowRight size={14} />
            </button>
          </div>

          <div className="template-gallery">
            {filteredTemplates.length === 0 ? (
              <div className="empty-game-state compact-empty"><p>No templates match your search.</p></div>
            ) : (
              filteredTemplates.map((template) => (
                <button
                  key={template.id}
                  type="button"
                  className={`template-card-item template-${template.theme}`}
                  onClick={() => applyTemplate({ ...template, difficulty: 'Intermediate', style: template.category })}
                >
                  <div
                    className="template-card-art"
                    aria-hidden="true"
                    style={{
                      backgroundImage: `linear-gradient(180deg, rgba(15,23,42,0.08), rgba(15,23,42,0.74)), url(${template.image || ''})`,
                      backgroundSize: 'cover',
                      backgroundPosition: 'center',
                      backgroundRepeat: 'no-repeat',
                    }}
                    onError={(event) => {
                      event.currentTarget.style.backgroundImage = 'linear-gradient(135deg, rgba(29, 78, 216, 0.2), rgba(124, 58, 237, 0.35))'
                    }}
                  />
                  <div className="template-card-overlay">
                    <span>{template.category}</span>
                    <strong>{template.title}</strong>
                  </div>
                </button>
              ))
            )}
          </div>

          <div className="section-header-row">
            <div className="section-heading-inline">
              <Clock3 size={18} />
              <h3>Recent Games</h3>
            </div>
            <button type="button" className="section-link" onClick={() => setActivePage('my-games')}>
              View All <ArrowRight size={14} />
            </button>
          </div>

          {filteredRecentGames.length === 0 ? (
            <div className="empty-game-state">
              <p>{savedGames.length > 0 ? 'No games match your search.' : 'No games yet - describe your first idea above.'}</p>
            </div>
          ) : (
            <div className="recent-games-grid">
              {filteredRecentGames.slice(0, 4).map((project) => (
                <button key={project.id} type="button" className="recent-game-card" onClick={() => openProject(project)}>
                  <div className="recent-game-thumb" aria-hidden="true">
                    <span>{project.title?.slice(0, 2).toUpperCase() || 'GX'}</span>
                  </div>
                  <div className="recent-game-body">
                    <div className="recent-game-meta">
                      <strong>{project.title || 'Untitled game'}</strong>
                    </div>
                    <small>{project.spec?.game_type ? project.spec.game_type.replaceAll('_', ' ') : 'Game'}</small>
                  </div>
                </button>
              ))}
            </div>
          )}
        </section>
      )
    }
  const renderCreatePage = () => (
    <section className="home-page-shell create-page-shell">
      <div className="home-hero-panel">
        <div className="home-hero-copy">
          <p className="eyebrow hero-kicker">Welcome to GameionX</p>
          <h2>
            <span>Describe Your Idea.</span>
            <span className="hero-highlight">Get a Playable Game.</span>
          </h2>
          <p>Turn your imagination into interactive games using AI agents.</p>
        </div>
        <div className="home-hero-visual" aria-hidden="true">
          <img
            src="/assets/home-hero.png"
            alt=""
            onError={(event) => {
              event.currentTarget.style.display = 'none'
              event.currentTarget.parentElement.classList.add('hero-fallback')
            }}
          />
        </div>
      </div>

      <div className="home-prompt-card">
        <div className="prompt-card-header">
          <div className="prompt-label"><Sparkles size={16} /> Describe your game idea</div>
        </div>

        <textarea
          value={prompt}
          maxLength={500}
          minLength={3}
          disabled={status === 'loading'}
          onChange={(event) => setPrompt(event.target.value)}
          placeholder="e.g. Create a platform game with five coins, moving platforms and a treasure chest..."
        />

        <div className="prompt-hint-row">
          <span className="coming-soon-wrap" title="Coming soon">
            <button type="button" className="reference-button" disabled aria-label="Add reference (coming soon)">
              + Add reference (optional)
            </button>
          </span>
          <button type="button" className="primary-button prompt-button" onClick={generateGame} disabled={status === 'loading'}>
            {status === 'loading' ? 'Generating...' : 'Generate Game'}
            <ArrowRight size={16} />
          </button>
          {spec && (
            <button
              type="button"
              className="secondary-button prompt-button"
              onClick={() => generateGame(undefined, { bypassCache: true })}
              disabled={status === 'loading'}
            >
              Regenerate
            </button>
          )}
        </div>

        {status === 'error' && (
          <div className="prompt-error-block">
            <p className="error-message">{error}</p>
            {generationRetryable && (
              <button type="button" className="secondary-button retry-button" onClick={retryGeneration}>
                Try again
              </button>
            )}
          </div>
        )}
      </div>

      <div className="panel-block glass-card ai-panel">
        <div className="section-heading">
          <div>
            <p className="eyebrow">AI workflow</p>
            <h3>Generation feedback</h3>
          </div>
        </div>
        <div className="agent-summary">
          <div className="agent-bubble blue">AI</div>
          <div>
            <strong>Game Designer Agent</strong>
            <p>Prompt analysis, creative framing, and game structure generation.</p>
          </div>
        </div>
        <div className="agent-summary">
          <div className="agent-bubble purple">QA</div>
          <div>
            <strong>Game QA Agent</strong>
            <p>Structural checks, browser validation, and repair loops.</p>
          </div>
        </div>
        <div className="workflow-panel">
          <div className="section-heading compact-heading">
            <div>
              <p className="eyebrow">Live workflow</p>
              <h4>{status === 'loading' ? 'Working on your game' : status === 'error' ? 'Workflow stopped' : 'Workflow result'}</h4>
            </div>
            {generationStartedAt && <span className="elapsed-time">{formatElapsed(generationElapsed)}</span>}
          </div>
          <ol className="workflow-timeline">
            {compactStages(stages).map((stage, index) => {
              const resultStatus = effectiveStageStatus(stage, playtestReport)
              return (
                <li key={`${stage.name}-${index}`} className={`workflow-step ${stageClass(resultStatus)}`}>
                  <span className="step-marker">{resultStatus === 'completed' ? '✓' : resultStatus === 'failed' ? '!' : resultStatus === 'started' ? '•' : '·'}</span>
                  <div>
                    <div className="step-heading">
                      <strong>{STAGE_LABELS[stage.name] || stage.name.replaceAll('_', ' ')}</strong>
                      <span className={`status-pill ${stageClass(resultStatus)}`}>{stageStatus(resultStatus)}</span>
                    </div>
                    <p>{stage.message}</p>
                  </div>
                </li>
              )
            })}
          </ol>
        </div>
      </div>

      {spec && (
        <div className="results-grid create-results">
          <GamePreview
            spec={spec}
            gameDefinition={gameDefinition}
            status={status}
            qaStatus={qaReport?.status}
            previewKey={previewKey}
            frameRef={previewFrameRef}
            onRestart={() => setPreviewKey((key) => key + 1)}
            onFullscreen={showFullscreen}
          />
          <SpecSummary spec={spec} provider={provider} qaReport={qaReport} gameDefinition={gameDefinition} />
        </div>
      )}
    </section>
  )

  const renderMyGamesPage = () => (
    <section className="page-grid">
      <div className="panel-block glass-card">
        <div className="section-heading">
          <div>
            <p className="eyebrow">Your library</p>
            <h3>My Games</h3>
          </div>
        </div>
        <div className="toolbar-row">
          <div className="search-wrap">
            <input
              aria-label="Search your games"
              value={searchTerm}
              onChange={(event) => setSearchTerm(event.target.value)}
              placeholder="Search games or prompts"
            />
          </div>
          <select value={sortBy} onChange={(event) => setSortBy(event.target.value)}>
            <option value="updated">Sort: Updated</option>
            <option value="created">Sort: Created</option>
            <option value="title">Sort: Title</option>
          </select>
        </div>
        <div className="segmented-control pills-control">
          {['all', 'verified', 'unverified', 'failed'].map((tab) => (
            <button
              key={tab}
              type="button"
              className={projectFilter === tab ? 'active' : ''}
              onClick={() => setProjectFilter(tab)}
            >
              {tab === 'all' ? 'All Games' : tab === 'verified' ? 'Verified' : tab === 'unverified' ? 'Unverified' : 'Failed'}
            </button>
          ))}
        </div>
        {projectBusy ? (
          <div className="loading-panel">Loading project…</div>
        ) : filteredProjects.length === 0 ? (
          <div className="empty-state">
            <h4>No matching projects</h4>
            <p>Try another search or create a new game.</p>
            <button className="primary-button" type="button" onClick={() => setActivePage('create')}>Create a game</button>
          </div>
        ) : (
          <div className="game-grid">
            {filteredProjects.map((project) => {
              return (
                <article key={project.id} className="game-card project-tile">
                  <div className="game-thumb gradient-orb">
                    <span>✦</span>
                  </div>
                  <div className="game-card-head">
                    <strong>{project.title}</strong>
                    <span className={`status-pill ${projectStatusVariant(project.status)}`}>
                      {projectStatusLabel(project.status)}
                    </span>
                  </div>
                  <div className="meta-row">
                    <span>{project.spec?.game_type?.replaceAll('_', ' ') || 'Adventure'}</span>
                  </div>
                  <p>{project.original_prompt || project.prompt}</p>
                  <div className="meta-row muted-row">
                    <span>{formatDate(project.created_at)}</span>
                    <span>{project.qa_report?.gameplay_status || 'QA pending'}</span>
                  </div>
                  <div className="action-row">
                    <button type="button" className="primary-button" onClick={() => openProject(project)}>Play</button>
                    <button type="button" className="secondary-button" onClick={() => openProject(project)}>Continue</button>
                    <button type="button" className="secondary-button" onClick={() => updateProjectTitle(project)}>Edit</button>
                  </div>
                  <div className="action-row secondary-actions">
                    <button type="button" className="link-button danger" onClick={() => deleteProject(project)}>Delete</button>
                  </div>
                </article>
              )
            })}
          </div>
        )}
      </div>

      <aside className="panel-block glass-card side-panel">
        <div className="section-heading">
          <div>
            <p className="eyebrow">Studio stats</p>
            <h3>Quick actions</h3>
          </div>
        </div>
        <div className="mini-stat-grid">
          <div className="mini-card">
            <small>Total games</small>
            <strong>{analytics.totalGames}</strong>
          </div>
          <div className="mini-card">
            <small>Verified</small>
            <strong>{analytics.publishedGames}</strong>
          </div>
          <div className="mini-card">
            <small>Unverified</small>
            <strong>{analytics.inProgressGames}</strong>
          </div>
          <div className="mini-card">
            <small>Storage</small>
            <strong>{analytics.storageUsed}%</strong>
          </div>
        </div>
        <div className="storage-meter">
          <span style={{ width: `${analytics.storageUsed}%` }} />
        </div>
        <button className="primary-button full-width" type="button" onClick={() => setActivePage('create')}>Generate new game</button>
        <button className="secondary-button full-width" type="button" onClick={() => setActivePage('templates')}>Browse templates</button>
      </aside>
    </section>
  )

  const renderTemplatesPage = () => (
    <section className="page-grid centered-stack">
      <div className="panel-block glass-card wide-panel">
        <div className="section-heading">
          <div>
            <p className="eyebrow">Template gallery</p>
            <h3>Build faster with a proven setup</h3>
          </div>
        </div>
        <div className="toolbar-row">
          <div className="search-wrap">
            <input
              value={templatesSearch}
              onChange={(event) => setTemplatesSearch(event.target.value)}
              placeholder="Search templates"
            />
          </div>
          <select aria-label="Template category filter">
            <option>All categories</option>
            <option>Arcade</option>
            <option>Adventure</option>
            <option>Shooter</option>
            <option>Racing</option>
          </select>
        </div>
        <div className="template-grid">
          {templateList.map((template) => (
            <article key={template.id} className="template-card glass-card">
              <div className="template-visual gradient-orb"><span>{template.style.slice(0, 1)}</span></div>
              <div className="template-meta">
                <div className="meta-row">
                  <span className="tag">{template.category}</span>
                  <span className="tag muted-tag">{template.difficulty}</span>
                </div>
                <h4>{template.title}</h4>
                <p>{template.description}</p>
                <div className="template-actions">
                  <button type="button" className="secondary-button" onClick={() => applyTemplate(template)}>
                    Use Template
                  </button>
                </div>
              </div>
            </article>
          ))}
        </div>
      </div>
    </section>
  )

  const renderAgentsPage = () => (
    <section className="page-grid">
      <div className="agent-grid">
        {AGENT_CARDS.map((agent) => (
          <article key={agent.name} className="glass-card agent-card">
            <div className="agent-card-head">
              <div className="agent-bubble purple">AI</div>
              <div>
                <h3>{agent.name}</h3>
                <span className="status-pill success">{agent.status}</span>
              </div>
            </div>
            <p>{agent.responsibility}</p>
            <div className="agent-workflow">
              <small>Workflow</small>
              <strong>{agent.workflow}</strong>
            </div>
          </article>
        ))}
      </div>
    </section>
  )

  const renderTimeMachinePage = () => (
    <section className="page-grid">
      <div className="panel-block glass-card wide-panel">
        <div className="section-heading">
          <div>
            <p className="eyebrow">Version history</p>
            <h3>Game Time Machine</h3>
          </div>
        </div>
        <div className="timeline-grid">
          {timeMachineEntries.map((entry) => (
            <article key={entry.id} className="timeline-card glass-card">
              <div className="timeline-header">
                <span className="tag">{entry.version}</span>
                <span className="coming-soon-wrap" title="Coming soon">
                  <button type="button" className="secondary-button small-button" disabled title="Coming soon">Restore</button>
                </span>
              </div>
              <h4>{entry.title}</h4>
              <p>{entry.summary}</p>
              <small>{formatDate(entry.created_at)}</small>
            </article>
          ))}
        </div>
      </div>
    </section>
  )

  const renderCommunityPage = () => (
    <section className="page-grid">
      <div className="section-heading">
        <div>
          <p className="eyebrow">Demo data</p>
          <h3>Community games</h3>
        </div>
      </div>
      <div className="community-grid">
        {COMMUNITY_GAMES.map((game) => (
          <article key={`${game.title}-${game.creator}`} className="glass-card community-card">
            <div className="community-art gradient-orb">
              <span>✦</span>
            </div>
            <div className="community-meta">
              <div className="meta-row">
                <span className="tag">{game.genre}</span>
                <span className="tag muted-tag">{game.likes} likes</span>
              </div>
              <h3>{game.title}</h3>
              <small>{game.creator}</small>
            </div>
            <span className="coming-soon-wrap" title="Coming soon">
              <button type="button" className="secondary-button full-width" disabled title="Coming soon">Preview</button>
            </span>
          </article>
        ))}
      </div>
    </section>
  )

  const renderAnalyticsPage = () => {
    const metricCards = [
      { label: 'Total games', value: analytics.totalGames },
      { label: 'Verified games', value: analytics.publishedGames },
      { label: 'Unverified games', value: analytics.inProgressGames },
      { label: 'Generation rate', value: `${analytics.generationRate}%` },
    ]
    const chartValues = [
      analytics.totalGames || 0,
      analytics.publishedGames || 0,
      analytics.inProgressGames || 0,
      analytics.generationRate || 0,
    ]
    const maxValue = Math.max(...chartValues, 1)

    return (
      <section className="page-grid">
        <div className="metrics-grid">
          {metricCards.map((metric) => (
            <article key={metric.label} className="glass-card metric-card">
              <small>{metric.label}</small>
              <strong>{metric.value}</strong>
            </article>
          ))}
        </div>
        <div className="panel-block glass-card wide-panel">
          <div className="section-heading">
            <div>
              <p className="eyebrow">Demo data · Performance</p>
              <h3>Generation statistics</h3>
            </div>
          </div>
          <div className="chart-bars" aria-label="Generation statistics chart">
            {chartValues.map((value, index) => (
              <div key={`${index}-${value}`} className="chart-column">
                <div className="chart-bar-wrap">
                  <span style={{ height: `${Math.max((value / maxValue) * 100, 12)}%` }} />
                </div>
                <small>{index === 0 ? 'Total' : index === 1 ? 'Verified' : index === 2 ? 'Unverified' : 'Rate'}</small>
              </div>
            ))}
          </div>
        </div>
      </section>
    )
  }

  const renderSettingsPage = () => (
    <section className="page-grid settings-grid">
      <div className="panel-block glass-card">
        <div className="section-heading">
          <div>
            <p className="eyebrow">Account</p>
            <h3>Profile settings</h3>
          </div>
        </div>
        <div className="profile-summary">
          <div className="profile-avatar large-avatar">{(user.email || user.username).slice(0, 1).toUpperCase()}</div>
          <div>
            <strong>{user.username}</strong>
            <small>{user.email}</small>
          </div>
        </div>
        <div className="settings-stack">
          <div className="setting-row">
            <span>Studio name</span>
            <strong>{user.username}</strong>
          </div>
          <div className="setting-row">
            <span>Account type</span>
            <strong>Authenticated</strong>
          </div>
          <div className="setting-row">
            <span>Theme</span>
            <strong>Futuristic dark</strong>
          </div>
          <div className="setting-row">
            <span>Notifications</span>
            <strong>Enabled</strong>
          </div>
        </div>
      </div>

      <div className="panel-block glass-card">
        <div className="section-heading">
          <div>
            <p className="eyebrow">Application</p>
            <h3>Preferences</h3>
          </div>
        </div>
        <div className="settings-stack">
          <div className="setting-row">
            <span>Generation mode</span>
            <strong>{mode}</strong>
          </div>
          <div className="setting-row">
            <span>Preferred difficulty</span>
            <strong>{difficulty}</strong>
          </div>
          <div className="setting-row">
            <span>Default game style</span>
            <strong>{GAME_STYLE_OPTIONS.find((style) => style.id === gameStyle)?.name || gameStyle}</strong>
          </div>
          <div className="setting-row">
            <span>Workspace sync</span>
            <strong>Connected</strong>
          </div>
        </div>
        <button className="primary-button" type="button" onClick={logout}>Logout</button>
      </div>
    </section>
  )

  const renderAuthScreen = () => (
    <div className="auth-page-shell">
      <div className="auth-visual-panel">
        <div className="brand-lockup">
          <span className="brand-mark auth-mark">GX</span>
          <span>Gameion<span className="brand-x">X</span></span>
        </div>

        <div className="auth-hero-copy">
          <p className="eyebrow auth-eyebrow">AI game studio</p>
          <h1>
            <span>Turn Your</span>
            <span>Ideas into</span>
            <span className="main-gradient-text">Playable Games</span>
          </h1>
          <p>
            Describe your game idea, and let AI bring it to life. Create. Play. Iterate. Instantly.
          </p>
        </div>

        <div className="feature-strip" aria-label="GameionX features">
          <div className="feature-pill">
            <span className="feature-icon"><Sparkles size={16} /></span>
            <span>AI-Powered<br />Game Creation</span>
          </div>
          <div className="feature-pill">
            <span className="feature-icon"><Gamepad2 size={16} /></span>
            <span>Multiple Game<br />Genres</span>
          </div>
          <div className="feature-pill">
            <span className="feature-icon"><Zap size={16} /></span>
            <span>Play Instantly<br />in Browser</span>
          </div>
        </div>

        <div className="auth-visual-scene" aria-hidden="true">
          <img
            className="auth-controller-art"
            src="/assets/login-controller.png"
            alt=""
            onError={(event) => {
              event.currentTarget.style.display = 'none'
              event.currentTarget.parentElement.classList.add('controller-fallback')
            }}
          />
          <div className="controller-fallback-scene" aria-hidden="true">
            <div className="fallback-cloud cloud-one" />
            <div className="fallback-cloud cloud-two" />
            <div className="fallback-island island-back" />
            <div className="fallback-island island-front" />
            <div className="fallback-castle" />
            <div className="fallback-controller">
              <div className="fallback-controller-pad pad-left" />
              <div className="fallback-controller-pad pad-right" />
              <div className="fallback-dpad">
                <span className="dpad-up" />
                <span className="dpad-left" />
                <span className="dpad-center" />
                <span className="dpad-right" />
                <span className="dpad-down" />
              </div>
              <div className="fallback-buttons">
                <span className="button button-a" />
                <span className="button button-b" />
              </div>
            </div>
          </div>
        </div>
      </div>

      <div className="auth-form-panel">
        <div className="auth-card">
          <h2>{authMode === 'login' ? 'Welcome Back' : 'Create your account'}</h2>
          <p className="auth-subtitle">
            {authMode === 'login' ? 'Sign in to continue to GameionX' : 'Start building your next playable experience.'}
          </p>

          <form onSubmit={authenticate} className="auth-form" noValidate>
            <div className="field-group">
              <label className="field-label" htmlFor="account-email">Email</label>
              <div className={`input-shell ${fieldErrors.email ? 'has-error' : ''}`}>
                <span aria-hidden="true"><Mail size={18} /></span>
                <input
                  id="account-email"
                  type="email"
                  autoComplete="email"
                  value={email}
                  onChange={(event) => {
                    const nextEmail = event.target.value
                    setEmail(nextEmail)
                    if (fieldErrors.email) {
                      const normalizedEmail = nextEmail.trim()
                      setFieldErrors((prev) => ({
                        ...prev,
                        email: !normalizedEmail ? 'Email is required.' : !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(normalizedEmail) ? 'Enter a valid email address.' : '',
                      }))
                    }
                  }}
                  placeholder="Enter your email"
                  aria-invalid={Boolean(fieldErrors.email)}
                  required
                />
              </div>
              {fieldErrors.email && <span className="input-error">{fieldErrors.email}</span>}
            </div>

            <div className="field-group">
              <label className="field-label" htmlFor="account-password">Password</label>
              <div className={`input-shell password-shell ${fieldErrors.password ? 'has-error' : ''}`}>
                <span aria-hidden="true"><LockKeyhole size={18} /></span>
                <input
                  id="account-password"
                  type={showPassword ? 'text' : 'password'}
                  autoComplete={authMode === 'login' ? 'current-password' : 'new-password'}
                  value={password}
                  onChange={(event) => {
                    const nextPassword = event.target.value
                    setPassword(nextPassword)
                    if (fieldErrors.password) {
                      setFieldErrors((prev) => ({
                        ...prev,
                        password: !nextPassword ? 'Password is required.' : nextPassword.length < 8 ? 'Password must be at least 8 characters.' : '',
                      }))
                    }
                  }}
                  placeholder="Enter your password"
                  aria-invalid={Boolean(fieldErrors.password)}
                  required
                />
                <button type="button" className="inline-toggle" aria-label={showPassword ? 'Hide password' : 'Show password'} onClick={() => setShowPassword((value) => !value)}>
                  {showPassword ? <EyeOff size={18} /> : <Eye size={18} />}
                </button>
              </div>
              {fieldErrors.password && <span className="input-error">{fieldErrors.password}</span>}
            </div>

            <div className="auth-row">
              <button type="button" className="link-button right-align" onClick={() => setAuthError('Password recovery is not enabled in this deployment.')}>
                Forgot password?
              </button>
            </div>

            <button type="submit" className="primary-button auth-submit" disabled={authBusy}>
              {authBusy ? 'Please wait…' : authMode === 'login' ? 'Sign In →' : 'Create Account'}
            </button>

            <div className="divider"><span>OR</span></div>

            <div className="social-auth-row">
              <button type="button" className="social-auth-button" disabled title="Coming soon">
                <span className="social-icon social-google"><GoogleLogo width={18} height={18} /></span>
                Continue with Google
              </button>
              <button type="button" className="social-auth-button" disabled title="Coming soon">
                <span className="social-icon social-github"><GitHubLogo width={18} height={18} /></span>
                Continue with GitHub
              </button>
            </div>

            <p className="signup-prompt">
              {authMode === 'login' ? 'Don’t have an account? ' : 'Already have an account? '}
              <button
                type="button"
                className="inline-link"
                onClick={() => {
                  setAuthMode(authMode === 'login' ? 'register' : 'login')
                  setAuthError('')
                  setFieldErrors({ email: '', password: '' })
                }}
              >
                {authMode === 'login' ? 'Sign up' : 'Sign in'}
              </button>
            </p>

            {authError && <p className="auth-error" role="alert">{authError}</p>}
          </form>
        </div>
      </div>
    </div>
  )

  const renderPage = () => {
    if (activePage === 'create') return renderCreatePage()
    if (activePage === 'my-games') return renderMyGamesPage()
    if (activePage === 'templates') return renderTemplatesPage()
    if (activePage === 'agents') return renderAgentsPage()
    if (activePage === 'time-machine') return renderTimeMachinePage()
    if (activePage === 'community') return renderCommunityPage()
    if (activePage === 'analytics') return renderAnalyticsPage()
    if (activePage === 'settings') return renderSettingsPage()
    return renderHomePage()
  }

  if (!authChecked) {
    return (
      <div className="auth-loading-splash" role="status" aria-live="polite">
        <span className="brand-mark">GX</span>
        <strong>Loading GameionX…</strong>
      </div>
    )
  }
  if (!user) return renderAuthScreen()

  return (
    <div className="app-shell">
      <aside className="sidebar" aria-label="Main navigation">
        <button className="brand-button" type="button" onClick={() => setActivePage('home')}>
          <span className="brand-mark">GX</span>
          <span>
            Gameion<span className="brand-x">X</span>
          </span>
        </button>

        <p className="nav-label">Workspace</p>
        <nav className="main-nav">
          {NAV_ITEMS.map((item) => {
            const Icon = NAV_ICONS[item.id] || Sparkles
            return (
              <button
                key={item.id}
                type="button"
                className={activePage === item.id ? 'nav-item active' : 'nav-item'}
                onClick={() => setActivePage(item.id)}
                aria-current={activePage === item.id ? 'page' : undefined}
              >
                <span className="nav-icon" aria-hidden="true"><Icon size={16} /></span>
                {item.label}
                {item.id === 'my-games' && savedGames.length > 0 && <span className="nav-count">{savedGames.length}</span>}
              </button>
            )
          })}
        </nav>

        <div className="sidebar-bottom">
          <div className="sidebar-account">
            <span className="account-avatar">{(user.email || user.username).slice(0, 1).toUpperCase()}</span>
            <span>
              <strong>{user.username}</strong>
              <small>{user.email}</small>
            </span>
          </div>

          <button className="nav-item logout-item" type="button" onClick={logout}>
            <span className="nav-icon" aria-hidden="true"><LogOut size={16} /></span>
            Log out
          </button>

          <div className="sidebar-upgrade">
            <span className="upgrade-label">Upgrade</span>
            <div className="upgrade-header">
              <h4>Pro Studio</h4>
              <Crown size={16} />
            </div>
            <p>Unlimited generations, advanced QA, and multi-agent orchestration.</p>
            <button type="button" className="primary-button full-width">Upgrade</button>
          </div>
        </div>
      </aside>

      <div className="app-main">
        <header className="topbar">
          <div className="topbar-titles">
            <p className="topbar-kicker">GAMEIONX STUDIO</p>
            <h1>{activeTitle}</h1>
          </div>

          <div className="topbar-search">
            <Search size={16} />
            <input
              type="text"
              value={searchTerm}
              onChange={(event) => setSearchTerm(event.target.value)}
              placeholder="Search games, templates, or ideas..."
              aria-label="Search this studio"
            />
          </div>

          <div className="topbar-actions">
            <button type="button" className="topbar-icon-button" aria-label="Notifications">
              <Bell size={18} />
            </button>
            <span className={`connection-indicator ${apiHealth.status}`} role="status">
              <i />
              {apiHealth.status === 'checking' ? 'Checking API' : apiHealth.status === 'connected' ? `${apiHealth.provider} API` : 'API unavailable'}
            </span>
            <button type="button" className="profile-chip" onClick={() => setActivePage('settings')}>
              <span className="account-avatar">{(user.email || user.username).slice(0, 1).toUpperCase()}</span>
              <span>{user.username}</span>
              <ChevronDown size={14} />
            </button>
          </div>
        </header>

        <main className="page-content">
          {accountError && <p className="inline-error">{accountError}</p>}
          {projectActionError && <p className="inline-error">{projectActionError}</p>}
          {renderPage()}
        </main>
      </div>

    </div>
  )
}

export default App
