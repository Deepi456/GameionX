from pathlib import Path

app = '''
import { useEffect, useRef, useState } from 'react'
import PhaserPreview from './components/PhaserPreview'
import {
  AGENT_CARDS,
  COMMUNITY_GAMES,
  GAME_STYLE_OPTIONS,
  NAV_ITEMS,
  STARTER_TEMPLATES,
  TIME_MACHINE_SNAPSHOTS,
  getAnalyticsSnapshot,
} from './lib/platformData'
import './App.css'
import './theme.css'

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000'
const GENERATION_TIMEOUT_MS = 90_000
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

const DIFFICULTY_OPTIONS = ['Beginner', 'Intermediate', 'Advanced']

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
  if (status === 'ready') return 'Published'
  if (status === 'warning') return 'In Progress'
  if (status === 'failed') return 'Archived'
  if (status === 'unverified') return 'In Progress'
  return 'Draft'
}

function projectStatusVariant(status) {
  if (status === 'ready') return 'success'
  if (status === 'failed') return 'danger'
  if (status === 'warning' || status === 'unverified') return 'warning'
  return 'neutral'
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
  const [mode, setMode] = useState('beginner')
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
  const [authError, setAuthError] = useState('')
  const [authBusy, setAuthBusy] = useState(false)
  const [accountError, setAccountError] = useState('')
  const [savedGames, setSavedGames] = useState([])
  const [activity, setActivity] = useState([])
  const [showAuth, setShowAuth] = useState(false)
  const [activePage, setActivePage] = useState('home')
  const [generationElapsed, setGenerationElapsed] = useState(0)
  const [generationStartedAt, setGenerationStartedAt] = useState(null)
  const [projectId, setProjectId] = useState(null)
  const [saveBusy, setSaveBusy] = useState(false)
  const [projectBusy, setProjectBusy] = useState(false)
  const [previewKey, setPreviewKey] = useState(0)
  const [projectActionError, setProjectActionError] = useState('')
  const [apiHealth, setApiHealth] = useState({ status: 'checking', provider: null })
  const [searchTerm, setSearchTerm] = useState('')
  const [sortBy, setSortBy] = useState('updated')
  const [projectFilter, setProjectFilter] = useState('all')
  const [archivedProjectIds, setArchivedProjectIds] = useState([])
  const [templatesSearch, setTemplatesSearch] = useState('')
  const previewFrameRef = useRef(null)

  async function loadAccount() {
    const response = await fetch(`${API_BASE_URL}/api/auth/me`, { credentials: 'include' })
    if (response.status === 401) {
      setUser(null)
      setSavedGames([])
      setActivity([])
      return
    }
    if (!response.ok) throw new Error('Your account could not be loaded.')
    const account = await response.json()
    setUser(account)
    const [projectsResponse, activityResponse] = await Promise.all([
      fetch(`${API_BASE_URL}/api/projects`, { credentials: 'include' }),
      fetch(`${API_BASE_URL}/api/activity`, { credentials: 'include' }),
    ])
    if (!projectsResponse.ok || !activityResponse.ok) {
      throw new Error('Your saved projects could not be loaded. Please try again.')
    }
    setSavedGames(await projectsResponse.json())
    setActivity(await activityResponse.json())
  }

  useEffect(() => {
    loadAccount().catch((requestError) => {
      setAccountError(requestError.message || 'Unable to reach GameionX.')
    })
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

  async function generateGame(event) {
    event.preventDefault()
    if (status === 'loading') return
    const normalizedPrompt = prompt.trim()
    if (normalizedPrompt.length < 3) {
      setError('Describe your game in at least 3 characters.')
      setStatus('error')
      return
    }
    setStatus('loading')
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
        body: JSON.stringify({ prompt: normalizedPrompt, mode }),
        signal: controller.signal,
      })
      const body = await response.json().catch(() => ({}))
      if (!response.ok) {
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
          if (user) await persistProject(generated, user)
        }
        throw new Error(apiErrorMessage(body.detail, 'Game generation failed.'))
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
      if (user) {
        await persistProject({
          provider: body.provider,
          prompt: normalizedPrompt,
          spec: { ...body.spec, runtime_config: body.runtime_config },
          runtime_config: body.runtime_config,
          game_definition: body.game_definition || null,
          qa_report: body.qa_report,
        }, user)
      }
    } catch (requestError) {
      setStatus('error')
      if (requestError.name === 'AbortError') {
        setError('Game generation timed out after 90 seconds. The configured AI provider may be busy; please try again.')
      } else {
        setError(requestError.message || `Game generation failed at ${API_BASE_URL}.`)
      }
    } finally {
      clearTimeout(timeoutId)
      setStatus((currentStatus) => (currentStatus === 'loading' ? 'idle' : currentStatus))
    }
  }

  async function authenticate(event) {
    event.preventDefault()
    setAuthError('')
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
      setShowAuth(false)
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

  async function saveGame() {
    if (!spec || !user) {
      setShowAuth(true)
      return
    }
    await persistProject({
      provider,
      prompt,
      spec,
      runtime_config: spec.runtime_config,
      game_definition: gameDefinition,
      qa_report: qaReport,
    })
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
      setActivePage('my-games')
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
      setActivity([])
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
      const isArchived = archivedProjectIds.includes(project.id)
      if (projectFilter === 'published') return matchesSearch && project.status === 'ready'
      if (projectFilter === 'in-progress') return matchesSearch && ['warning', 'unverified'].includes(project.status) && !isArchived
      if (projectFilter === 'archived') return isArchived && matchesSearch
      return matchesSearch && !isArchived
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

  const renderHomePage = () => (
    <section className="page-grid">
      <div className="welcome-panel glass-card">
        <div>
          <p className="eyebrow">Your creative workspace</p>
          <h2>Turn a simple idea into a world you can play.</h2>
          <p>
            Describe a game in your own words. GameionX plans, builds, validates, and prepares it
            in the existing Phaser runtime.
          </p>
          <div className="hero-actions">
            <button className="primary-button" type="button" onClick={() => setActivePage('create')}>
              Create a game <span aria-hidden="true">→</span>
            </button>
            <button className="secondary-button" type="button" onClick={() => setActivePage('templates')}>
              Browse templates
            </button>
          </div>
        </div>
        <div className="welcome-art" aria-hidden="true">
          <span>✦</span>
          <i />
          <b />
        </div>
      </div>

      <div className="stats-grid">
        <article className="stat-card glass-card">
          <span className="stat-icon gradient-indigo">▣</span>
          <div>
            <small>Saved projects</small>
            <strong>{user ? savedGames.length : '—'}</strong>
          </div>
        </article>
        <article className="stat-card glass-card">
          <span className="stat-icon gradient-blue">◈</span>
          <div>
            <small>Current game</small>
            <strong>{spec ? 'Ready to play' : 'No active game'}</strong>
          </div>
        </article>
        <article className="stat-card glass-card">
          <span className="stat-icon gradient-violet">✓</span>
          <div>
            <small>Latest QA</small>
            <strong>{qaReport ? qaReport.status || 'Unverified' : 'Not run'}</strong>
          </div>
        </article>
      </div>

      <div className="panel-row">
        <div className="panel-block glass-card">
          <div className="section-heading">
            <div>
              <p className="eyebrow">Launch prompt</p>
              <h3>What would you like to play?</h3>
            </div>
            <div className="mode-toggle" role="group" aria-label="Experience mode">
              {['beginner', 'developer'].map((option) => (
                <button
                  type="button"
                  key={option}
                  className={mode === option ? 'active' : ''}
                  onClick={() => setMode(option)}
                  aria-pressed={mode === option}
                >
                  {option === 'beginner' ? 'Beginner' : 'Developer'}
                </button>
              ))}
            </div>
          </div>
          <div className="prompt-shell">
            <textarea
              value={prompt}
              maxLength={500}
              disabled={status === 'loading'}
              onChange={(event) => setPrompt(event.target.value)}
              placeholder="Describe the world, objective, and how it should feel..."
            />
            <div className="field-row">
              <div className="field-group">
                <label>Difficulty</label>
                <div className="segmented-control">
                  {DIFFICULTY_OPTIONS.map((option) => (
                    <button
                      key={option}
                      type="button"
                      className={difficulty === option ? 'active' : ''}
                      onClick={() => setDifficulty(option)}
                    >
                      {option}
                    </button>
                  ))}
                </div>
              </div>
              <div className="field-group compact-field">
                <label>Game style</label>
                <select value={gameStyle} onChange={(event) => setGameStyle(event.target.value)}>
                  {GAME_STYLE_OPTIONS.map((style) => (
                    <option key={style.id} value={style.id}>{style.name}</option>
                  ))}
                </select>
              </div>
            </div>
            <div className="example-prompts">
              {[
                'A neon maze where I find the exit while avoiding drones.',
                'A top-down adventure where I collect gems in a forest.',
                'A dodge arena where I survive waves of enemies.',
              ].map((example) => (
                <button type="button" key={example} onClick={() => setPrompt(example)}>
                  {example}
                </button>
              ))}
            </div>
            <div className="prompt-footer">
              <span>{mode === 'beginner' ? 'Guided controls and friendly defaults' : 'More control over game mechanics'}</span>
              <button className="primary-button" type="button" onClick={generateGame} disabled={status === 'loading'}>
                {status === 'loading' ? 'Generation running…' : 'Generate Game'}
              </button>
            </div>
          </div>
          {status === 'error' && <p className="error-message">{error}</p>}
        </div>

        <div className="panel-block glass-card">
          <div className="section-heading">
            <div>
              <p className="eyebrow">Quick templates</p>
              <h3>Launch from a proven concept</h3>
            </div>
          </div>
          <div className="template-stack">
            {STARTER_TEMPLATES.slice(0, 3).map((template) => (
              <button key={template.id} type="button" className="template-mini" onClick={() => applyTemplate(template)}>
                <span className="template-badge">{template.style}</span>
                <strong>{template.title}</strong>
                <small>{template.description}</small>
              </button>
            ))}
          </div>
        </div>
      </div>

      <div className="panel-row">
        <div className="panel-block glass-card wide-panel">
          <div className="section-heading">
            <div>
              <p className="eyebrow">Recent games</p>
              <h3>Your latest creations</h3>
            </div>
            <button className="secondary-button" type="button" onClick={() => setActivePage('my-games')}>
              Open library
            </button>
          </div>
          {!user ? (
            <div className="empty-state compact-empty">
              <h4>Your projects, all in one place</h4>
              <p>Sign in to save and reopen generated games.</p>
              <button className="secondary-button" type="button" onClick={() => setShowAuth(true)}>
                Sign in or create account
              </button>
            </div>
          ) : savedGames.length === 0 ? (
            <div className="empty-state compact-empty">
              <h4>No projects yet</h4>
              <p>Your saved games will appear here after generation.</p>
            </div>
          ) : (
            <div className="game-grid compact-grid">
              {savedGames.slice(0, 3).map((project) => (
                <button key={project.id} type="button" className="game-card" onClick={() => openProject(project)}>
                  <div className="game-thumb gradient-orb">
                    <span>✦</span>
                  </div>
                  <div className="game-card-head">
                    <strong>{project.title}</strong>
                    <span className={`status-pill ${projectStatusVariant(project.status)}`}>
                      {projectStatusLabel(project.status)}
                    </span>
                  </div>
                  <small>{project.original_prompt || project.prompt}</small>
                </button>
              ))}
            </div>
          )}
        </div>
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
    </section>
  )

  const renderCreatePage = () => (
    <section className="page-grid">
      <div className="panel-block glass-card">
        <div className="section-heading">
          <div>
            <p className="eyebrow">AI game generation</p>
            <h3>What should we build?</h3>
          </div>
        </div>
        <div className="prompt-shell large-shell">
          <textarea
            value={prompt}
            maxLength={500}
            minLength={3}
            rows="7"
            disabled={status === 'loading'}
            onChange={(event) => setPrompt(event.target.value)}
            placeholder="Describe your world, objective, mechanics, atmosphere, and win condition..."
          />
          <div className="field-row stacked-fields">
            <div className="field-group">
              <label>Difficulty</label>
              <div className="segmented-control">
                {DIFFICULTY_OPTIONS.map((option) => (
                  <button
                    key={option}
                    type="button"
                    className={difficulty === option ? 'active' : ''}
                    onClick={() => setDifficulty(option)}
                  >
                    {option}
                  </button>
                ))}
              </div>
            </div>
            <div className="field-group">
              <label>Style</label>
              <div className="tile-grid">
                {GAME_STYLE_OPTIONS.map((style) => (
                  <button
                    key={style.id}
                    type="button"
                    className={gameStyle === style.id ? 'tile active' : 'tile'}
                    onClick={() => setGameStyle(style.id)}
                  >
                    <span>{style.icon}</span>
                    {style.name}
                  </button>
                ))}
              </div>
            </div>
          </div>
          <div className="field-row">
            <label className="upload-field">
              Optional reference upload
              <input type="file" aria-label="Optional reference upload" />
            </label>
          </div>
          <div className="prompt-footer">
            <span>{mode === 'beginner' ? 'Guided controls and friendly defaults' : 'Advanced runtime configuration'}</span>
            <button className="primary-button" type="button" onClick={generateGame} disabled={status === 'loading'}>
              {status === 'loading' ? 'Generation running…' : 'Generate Game'}
            </button>
          </div>
          {status === 'error' && <p className="error-message">{error}</p>}
        </div>
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
          {['all', 'in-progress', 'published', 'archived'].map((tab) => (
            <button
              key={tab}
              type="button"
              className={projectFilter === tab ? 'active' : ''}
              onClick={() => setProjectFilter(tab)}
            >
              {tab === 'all' ? 'All Games' : tab === 'in-progress' ? 'In Progress' : tab === 'published' ? 'Published' : 'Archived'}
            </button>
          ))}
        </div>
        {!user ? (
          <div className="empty-state">
            <h4>Sign in to view your projects</h4>
            <p>Authentication is required for private project storage.</p>
            <button className="primary-button" type="button" onClick={() => setShowAuth(true)}>Sign in</button>
          </div>
        ) : projectBusy ? (
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
              const difficultyText = project.spec?.levels?.length >= 2 ? 'Advanced' : project.spec?.player?.max_health > 100 ? 'Intermediate' : 'Beginner'
              const isArchived = archivedProjectIds.includes(project.id)
              return (
                <article key={project.id} className="game-card project-tile">
                  <div className="game-thumb gradient-orb">
                    <span>✦</span>
                  </div>
                  <div className="game-card-head">
                    <strong>{project.title}</strong>
                    <span className={`status-pill ${projectStatusVariant(project.status)}`}>
                      {isArchived ? 'Archived' : projectStatusLabel(project.status)}
                    </span>
                  </div>
                  <div className="meta-row">
                    <span className="difficulty-tag">{difficultyText}</span>
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
                    <button type="button" className="link-button" onClick={() => setArchivedProjectIds((ids) => (ids.includes(project.id) ? ids.filter((id) => id !== project.id) : [...ids, project.id]))}>
                      {isArchived ? 'Restore' : 'Archive'}
                    </button>
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
            <small>Published</small>
            <strong>{analytics.publishedGames}</strong>
          </div>
          <div className="mini-card">
            <small>In progress</small>
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
                <button type="button" className="secondary-button small-button">Restore</button>
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
            <button type="button" className="secondary-button full-width">Preview</button>
          </article>
        ))}
      </div>
    </section>
  )

  const renderAnalyticsPage = () => {
    const metricCards = [
      { label: 'Total games', value: analytics.totalGames },
      { label: 'Published games', value: analytics.publishedGames },
      { label: 'In progress', value: analytics.inProgressGames },
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
              <p className="eyebrow">Performance</p>
              <h3>Generation statistics</h3>
            </div>
          </div>
          <div className="chart-bars" aria-label="Generation statistics chart">
            {chartValues.map((value, index) => (
              <div key={`${index}-${value}`} className="chart-column">
                <div className="chart-bar-wrap">
                  <span style={{ height: `${Math.max((value / maxValue) * 100, 12)}%` }} />
                </div>
                <small>{index === 0 ? 'Total' : index === 1 ? 'Published' : index === 2 ? 'Active' : 'Rate'}</small>
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
          <div className="profile-avatar large-avatar">{user ? (user.email || user.username).slice(0, 1).toUpperCase() : 'G'}</div>
          <div>
            <strong>{user ? user.username : 'Guest User'}</strong>
            <small>{user ? user.email : 'Sign in to unlock project syncing'}</small>
          </div>
        </div>
        <div className="settings-stack">
          <div className="setting-row">
            <span>Studio name</span>
            <strong>{user ? user.username : 'Guest'}</strong>
          </div>
          <div className="setting-row">
            <span>Account type</span>
            <strong>{user ? 'Authenticated' : 'Guest session'}</strong>
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
            <strong>{user ? 'Connected' : 'Unavailable'}</strong>
          </div>
        </div>
        <button className="primary-button" type="button" onClick={logout} disabled={!user}>Logout</button>
      </div>
    </section>
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
          {NAV_ITEMS.map((item) => (
            <button
              key={item.id}
              type="button"
              className={activePage === item.id ? 'nav-item active' : 'nav-item'}
              onClick={() => setActivePage(item.id)}
              aria-current={activePage === item.id ? 'page' : undefined}
            >
              <span className="nav-icon" aria-hidden="true">{item.icon}</span>
              {item.label}
              {item.id === 'my-games' && savedGames.length > 0 && <span className="nav-count">{savedGames.length}</span>}
            </button>
          ))}
        </nav>

        <div className="sidebar-bottom">
          <div className="sidebar-account">
            <span className="account-avatar">{user ? (user.email || user.username).slice(0, 1).toUpperCase() : 'G'}</span>
            <span>
              <strong>{user ? user.username : 'Guest session'}</strong>
              <small>{user ? user.email : 'Sign in to save projects'}</small>
            </span>
          </div>

          <button className="nav-item logout-item" type="button" onClick={user ? logout : () => setShowAuth(true)}>
            <span className="nav-icon" aria-hidden="true">↪</span>
            {user ? 'Log out' : 'Sign in'}
          </button>

          <div className="sidebar-upgrade">
            <span className="upgrade-label">Upgrade</span>
            <h4>Pro Studio</h4>
            <p>Unlimited generations, advanced QA, and multi-agent orchestration.</p>
            <button type="button" className="primary-button full-width">Upgrade</button>
          </div>
        </div>
      </aside>

      <div className="app-main">
        <header className="topbar">
          <div>
            <p className="topbar-kicker">GAMEIONX STUDIO</p>
            <h1>{activeTitle}</h1>
          </div>
          <div className="topbar-actions">
            <span className={`connection-indicator ${apiHealth.status}`} role="status">
              <i />
              {apiHealth.status === 'checking' ? 'Checking API' : apiHealth.status === 'connected' ? `${apiHealth.provider} API` : 'API unavailable'}
            </span>
            {user ? (
              <button type="button" className="profile-chip" onClick={() => setActivePage('settings')}>
                <span className="account-avatar">{(user.email || user.username).slice(0, 1).toUpperCase()}</span>
                <span>{user.username}</span>
              </button>
            ) : (
              <button type="button" className="primary-button compact-button" onClick={() => setShowAuth(true)}>
                Sign in
              </button>
            )}
          </div>
        </header>

        <main className="page-content">
          {accountError && <p className="inline-error">{accountError}</p>}
          {projectActionError && <p className="inline-error">{projectActionError}</p>}
          {renderPage()}
        </main>
      </div>

      {!user && showAuth && (
        <div className="auth-overlay">
          <form className="auth-card" onSubmit={authenticate} role="dialog" aria-modal="true" aria-labelledby="auth-title">
            <button className="auth-close" type="button" aria-label="Close account form" onClick={() => { setShowAuth(false); setAuthError('') }}>
              ×
            </button>
            <div className="auth-mark">GX</div>
            <p className="eyebrow">{authMode === 'login' ? 'Welcome back' : 'Your next world awaits'}</p>
            <h2 id="auth-title">{authMode === 'login' ? 'Sign in to GameionX' : 'Create your account'}</h2>
            <p className="auth-description">
              {authMode === 'login'
                ? 'Pick up where your next playable idea begins.'
                : 'Save your games and keep your creations close.'}
            </p>

            <label htmlFor="account-email">Email address</label>
            <input
              id="account-email"
              type="email"
              autoComplete="email"
              value={email}
              onChange={(event) => setEmail(event.target.value)}
              placeholder="you@example.com"
              required
            />

            <label htmlFor="account-password">Password</label>
            <input
              id="account-password"
              type="password"
              autoComplete={authMode === 'login' ? 'current-password' : 'new-password'}
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              placeholder="••••••••"
              required
            />

            <div className="auth-links">
              <button type="button" className="link-button" onClick={() => setAuthMode('login')}>Forgot password?</button>
              <button type="button" className="link-button" onClick={() => setAuthMode(authMode === 'login' ? 'register' : 'login')}>
                {authMode === 'login' ? 'Create account' : 'Back to login'}
              </button>
            </div>

            <button type="submit" className="primary-button width-full" disabled={authBusy}>
              {authBusy ? 'Please wait…' : authMode === 'login' ? 'Sign in' : 'Create account'}
            </button>

            {authError && <p className="auth-error" role="alert">{authError}</p>}
          </form>
        </div>
      )}
    </div>
  )
}

export default App
'''

css = '''
:root {
  --page: #07111d;
  --page-alt: #0b1528;
  --surface: rgba(14, 22, 37, 0.82);
  --surface-strong: #0f1d31;
  --panel: rgba(17, 25, 41, 0.88);
  --panel-soft: rgba(19, 28, 42, 0.82);
  --panel-border: rgba(120, 137, 182, 0.26);
  --text: #edf4ff;
  --text-soft: #a2b4d8;
  --accent: #7d8cff;
  --accent-strong: #5ba1ff;
  --accent-warm: #b38cff;
  --success: #70e0b5;
  --warning: #ffd166;
  --danger: #ff6b7d;
  --shadow: 0 18px 60px rgba(4, 8, 18, 0.54);
}

* {
  box-sizing: border-box;
}

body {
  margin: 0;
  min-height: 100vh;
  background:
    radial-gradient(circle at top left, rgba(104, 116, 255, 0.14), transparent 24%),
    radial-gradient(circle at top right, rgba(74, 188, 255, 0.12), transparent 18%),
    var(--page);
  color: var(--text);
  font-family: Inter, 'Segoe UI', sans-serif;
}

button,
input,
select,
textarea {
  font: inherit;
}

button {
  cursor: pointer;
}

input,
select,
textarea {
  width: 100%;
  border: 1px solid rgba(128, 146, 201, 0.25);
  border-radius: 12px;
  background: rgba(9, 18, 31, 0.82);
  color: var(--text);
  padding: 0.9rem 1rem;
}

input::placeholder,
textarea::placeholder {
  color: rgba(169, 181, 208, 0.8);
}

button:disabled {
  opacity: 0.55;
  cursor: wait;
}

.app-shell {
  display: grid;
  grid-template-columns: 260px minmax(0, 1fr);
  min-height: 100vh;
  background: rgba(6, 12, 23, 0.7);
}

.sidebar {
  position: sticky;
  top: 0;
  display: flex;
  flex-direction: column;
  gap: 1.2rem;
  height: 100vh;
  padding: 1.5rem 1rem 1rem;
  border-right: 1px solid rgba(137, 155, 214, 0.16);
  background: rgba(9, 15, 26, 0.85);
  backdrop-filter: blur(18px);
}

.brand-button {
  display: flex;
  align-items: center;
  gap: 0.7rem;
  width: 100%;
  padding: 0.6rem 0.4rem;
  border: 0;
  border-radius: 14px;
  background: transparent;
  color: var(--text);
  font-size: 1.2rem;
  font-weight: 800;
  letter-spacing: -0.06em;
  text-align: left;
}

.brand-mark {
  display: grid;
  place-items: center;
  width: 2.25rem;
  height: 2.25rem;
  border-radius: 0.85rem;
  background: linear-gradient(135deg, #678dff, #8f72ff);
  color: white;
  font-size: 0.72rem;
  box-shadow: 0 10px 24px rgba(104, 128, 255, 0.5);
}

.brand-x {
  color: #84b7ff;
}

.nav-label,
.topbar-kicker,
.eyebrow {
  margin: 0;
  color: #8797c4;
  font-size: 0.68rem;
  font-weight: 800;
  letter-spacing: 0.18rem;
  text-transform: uppercase;
}

.main-nav {
  display: grid;
  gap: 0.5rem;
}

.nav-item {
  display: flex;
  align-items: center;
  gap: 0.8rem;
  width: 100%;
  min-height: 2.8rem;
  padding: 0.7rem 0.85rem;
  border: 1px solid transparent;
  border-radius: 12px;
  background: transparent;
  color: var(--text-soft);
  font-size: 0.92rem;
  font-weight: 600;
  text-align: left;
  transition: transform 0.18s ease, border-color 0.18s ease, background 0.18s ease;
}

.nav-item:hover {
  border-color: rgba(132, 143, 200, 0.18);
  background: rgba(17, 29, 46, 0.7);
  transform: translateX(1px);
}

.nav-item.active {
  border-color: rgba(125, 140, 255, 0.3);
  background: linear-gradient(135deg, rgba(103, 111, 251, 0.18), rgba(83, 147, 255, 0.1));
  color: #edf4ff;
}

.nav-icon {
  display: grid;
  place-items: center;
  width: 1.1rem;
  color: #9bacd8;
  font-size: 0.95rem;
}

.nav-item.active .nav-icon {
  color: #d9d6ff;
}

.nav-count {
  display: grid;
  place-items: center;
  min-width: 1.4rem;
  height: 1.4rem;
  margin-left: auto;
  border-radius: 999px;
  background: rgba(123, 108, 255, 0.18);
  color: #d6d3ff;
  font-size: 0.68rem;
  font-weight: 700;
}

.sidebar-bottom {
  margin-top: auto;
  display: grid;
  gap: 0.8rem;
  padding-top: 1rem;
  border-top: 1px solid rgba(137, 155, 214, 0.16);
}

.sidebar-account {
  display: flex;
  align-items: center;
  gap: 0.65rem;
  padding: 0.2rem 0.4rem;
}

.sidebar-account span:last-child {
  display: grid;
  gap: 0.1rem;
  min-width: 0;
}

.sidebar-account strong,
.sidebar-account small {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.sidebar-account strong {
  color: var(--text);
  font-size: 0.75rem;
}

.sidebar-account small {
  color: var(--text-soft);
  font-size: 0.65rem;
}

.account-avatar,
.profile-avatar {
  display: grid;
  place-items: center;
  width: 2rem;
  height: 2rem;
  border-radius: 0.7rem;
  background: rgba(96, 131, 236, 0.18);
  border: 1px solid rgba(116, 156, 255, 0.22);
  color: #b5d1ff;
  font-size: 0.7rem;
  font-weight: 800;
}

.logout-item {
  color: var(--text-soft);
}

.sidebar-upgrade {
  display: grid;
  gap: 0.65rem;
  padding: 1rem 0.9rem 0.9rem;
  border: 1px solid rgba(134, 151, 211, 0.2);
  border-radius: 18px;
  background: linear-gradient(180deg, rgba(17, 28, 45, 0.9), rgba(12, 19, 31, 0.9));
  box-shadow: var(--shadow);
}

.upgrade-label {
  color: #9ebeff;
  font-size: 0.62rem;
  letter-spacing: 0.18rem;
  text-transform: uppercase;
  font-weight: 800;
}

.sidebar-upgrade h4 {
  margin: 0;
  font-size: 1.1rem;
}

.sidebar-upgrade p {
  margin: 0;
  color: var(--text-soft);
  font-size: 0.72rem;
  line-height: 1.5;
}

.app-main {
  min-width: 0;
}

.topbar {
  position: sticky;
  z-index: 10;
  top: 0;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 1rem;
  min-height: 5rem;
  padding: 1rem 2.2rem;
  border-bottom: 1px solid rgba(137, 155, 214, 0.16);
  background: rgba(6, 12, 20, 0.8);
  backdrop-filter: blur(16px);
}

.topbar h1 {
  margin: 0.3rem 0 0;
  color: var(--text);
  font-size: clamp(1.15rem, 1.8vw, 1.8rem);
  letter-spacing: -0.06em;
}

.topbar-actions {
  display: flex;
  align-items: center;
  gap: 1rem;
}

.connection-indicator {
  display: inline-flex;
  align-items: center;
  gap: 0.55rem;
  color: var(--text-soft);
  font-size: 0.72rem;
  font-weight: 700;
}

.connection-indicator i {
  width: 0.45rem;
  height: 0.45rem;
  border-radius: 50%;
  background: var(--success);
  box-shadow: 0 0 18px rgba(112, 224, 181, 0.7);
}

.connection-indicator.unavailable i {
  background: var(--danger);
}

.profile-chip {
  display: inline-flex;
  align-items: center;
  gap: 0.6rem;
  padding: 0.3rem 0.65rem 0.3rem 0.25rem;
  border: 1px solid rgba(129, 147, 204, 0.25);
  border-radius: 12px;
  background: rgba(14, 22, 36, 0.9);
  color: var(--text);
  font-size: 0.82rem;
  font-weight: 600;
}

.page-content {
  padding: 2rem 2.2rem 3rem;
}

.page-grid {
  display: grid;
  gap: 1.3rem;
}

.panel-row {
  display: grid;
  grid-template-columns: minmax(0, 1.45fr) minmax(260px, 0.65fr);
  gap: 1.3rem;
}

.glass-card,
.panel-block,
.welcome-panel,
.template-card,
.agent-card,
.timeline-card,
.community-card,
.metric-card,
.spec-summary,
.preview-card {
  border: 1px solid var(--panel-border);
  border-radius: 18px;
  background: linear-gradient(180deg, rgba(17, 26, 42, 0.96), rgba(12, 18, 30, 0.96));
  box-shadow: var(--shadow);
}

.welcome-panel {
  position: relative;
  display: flex;
  align-items: center;
  min-height: 14rem;
  overflow: hidden;
  padding: clamp(1.3rem, 3vw, 2.4rem);
}

.welcome-panel h2 {
  max-width: 19ch;
  margin: 0.5rem 0 0.8rem;
  font-size: clamp(2rem, 3vw, 3.25rem);
  line-height: 1.04;
  letter-spacing: -0.08em;
}

.welcome-panel p:not(.eyebrow) {
  max-width: 42rem;
  margin: 0;
  color: var(--text-soft);
  font-size: 0.95rem;
  line-height: 1.7;
}

.hero-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 0.8rem;
  margin-top: 1.4rem;
}

.welcome-art {
  position: absolute;
  right: 1.8rem;
  width: 20rem;
  height: 100%;
  opacity: 0.75;
}

.welcome-art span {
  position: absolute;
  right: 3.2rem;
  bottom: 2rem;
  font-size: 7rem;
  color: rgba(155, 167, 255, 0.9);
  text-shadow: 0 0 28px rgba(128, 132, 255, 0.8);
}

.welcome-art i,
.welcome-art b {
  position: absolute;
  inset: auto 2rem 1rem auto;
  display: block;
  width: 10.5rem;
  height: 10.5rem;
  border: 1px solid rgba(126, 143, 255, 0.3);
  border-radius: 50%;
}

.welcome-art b {
  width: 14.5rem;
  height: 14.5rem;
  right: 0.5rem;
  bottom: 0.5rem;
  border-color: rgba(92, 152, 255, 0.2);
}

.stats-grid,
.metrics-grid,
.agent-grid,
.community-grid,
.timeline-grid,
.template-grid,
.game-grid {
  display: grid;
  gap: 1rem;
}

.stats-grid {
  grid-template-columns: repeat(3, minmax(0, 1fr));
}

.stat-card {
  display: flex;
  align-items: center;
  gap: 0.9rem;
  padding: 1rem 1.2rem;
}

.stat-icon {
  display: grid;
  place-items: center;
  width: 2.6rem;
  height: 2.6rem;
  border-radius: 0.9rem;
  font-size: 1rem;
}

.gradient-indigo {
  background: linear-gradient(135deg, rgba(83, 108, 255, 0.26), rgba(166, 120, 255, 0.18));
}

.gradient-blue {
  background: linear-gradient(135deg, rgba(85, 170, 255, 0.24), rgba(96, 123, 255, 0.16));
}

.gradient-violet {
  background: linear-gradient(135deg, rgba(145, 120, 255, 0.24), rgba(188, 127, 255, 0.18));
}

.stat-card div {
  display: grid;
  gap: 0.2rem;
}

.stat-card small,
.metric-card small,
.mini-card small {
  color: var(--text-soft);
  font-size: 0.68rem;
  text-transform: uppercase;
  letter-spacing: 0.12rem;
}

.stat-card strong,
.metric-card strong,
.mini-card strong {
  font-size: 1.05rem;
  letter-spacing: -0.04em;
}

.panel-block {
  padding: 1.2rem 1.1rem 1.1rem;
}

.section-heading {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 1rem;
  margin-bottom: 1rem;
}

.section-heading h3,
.section-heading h4,
.agent-card h3,
.timeline-card h4,
.template-meta h4,
.community-meta h3 {
  margin: 0.35rem 0 0;
  letter-spacing: -0.05em;
}

.mode-toggle,
.segmented-control {
  display: inline-flex;
  flex-wrap: wrap;
  gap: 0.4rem;
  padding: 0.28rem;
  border: 1px solid rgba(136, 152, 199, 0.22);
  border-radius: 12px;
  background: rgba(9, 16, 27, 0.9);
}

.mode-toggle button,
.segmented-control button,
.tile {
  border: 0;
  border-radius: 10px;
  background: transparent;
  color: var(--text-soft);
  padding: 0.55rem 0.8rem;
  font-size: 0.76rem;
  font-weight: 700;
}

.mode-toggle button.active,
.segmented-control button.active,
.tile.active {
  background: linear-gradient(135deg, rgba(113, 126, 255, 0.9), rgba(99, 163, 255, 0.8));
  color: white;
  box-shadow: 0 8px 18px rgba(99, 94, 255, 0.32);
}

.prompt-shell {
  display: grid;
  gap: 1rem;
}

textarea {
  min-height: 8rem;
  resize: vertical;
  border-radius: 14px;
  line-height: 1.7;
}

.field-row {
  display: flex;
  gap: 1rem;
  align-items: end;
  flex-wrap: wrap;
}

.field-group {
  flex: 1 1 0;
  min-width: 0;
}

.field-group label,
.upload-field {
  display: block;
  margin-bottom: 0.5rem;
  color: var(--text-soft);
  font-size: 0.74rem;
  font-weight: 700;
  letter-spacing: 0.08em;
  text-transform: uppercase;
}

.compact-field {
  max-width: 12rem;
}

select {
  min-height: 3rem;
}

.example-prompts {
  display: flex;
  flex-wrap: wrap;
  gap: 0.6rem;
}

.example-prompts button {
  border: 1px solid rgba(145, 161, 211, 0.18);
  border-radius: 999px;
  background: rgba(16, 24, 39, 0.85);
  color: var(--text-soft);
  padding: 0.58rem 0.82rem;
  font-size: 0.72rem;
}

.prompt-footer {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 1rem;
  color: var(--text-soft);
  font-size: 0.78rem;
}

.primary-button,
.secondary-button,
.link-button {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-height: 2.7rem;
  padding: 0.7rem 1.05rem;
  border-radius: 12px;
  font-weight: 700;
  transition: transform 0.18s ease, filter 0.18s ease;
}

.primary-button {
  border: 0;
  background: linear-gradient(135deg, #7d8cff, #6d9bfd 50%, #7fb9ff 100%);
  color: white;
  box-shadow: 0 12px 24px rgba(103, 122, 255, 0.35);
}

.secondary-button {
  border: 1px solid rgba(130, 152, 212, 0.22);
  background: rgba(18, 28, 43, 0.9);
  color: var(--text);
}

.link-button {
  min-height: auto;
  padding: 0;
  border: 0;
  background: transparent;
  color: var(--text-soft);
}

.primary-button:hover,
.secondary-button:hover,
.link-button:hover {
  transform: translateY(-1px);
  filter: brightness(1.04);
}

.template-stack {
  display: grid;
  gap: 0.7rem;
}

.template-mini {
  display: grid;
  gap: 0.35rem;
  text-align: left;
  width: 100%;
  padding: 0.9rem 0.95rem;
  border: 1px solid rgba(136, 152, 199, 0.18);
  border-radius: 14px;
  background: rgba(13, 20, 32, 0.8);
  color: var(--text);
}

.template-badge,
.tag,
.difficulty-tag {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: fit-content;
  padding: 0.25rem 0.55rem;
  border-radius: 999px;
  background: rgba(100, 118, 255, 0.12);
  color: #c6d5ff;
  font-size: 0.62rem;
  font-weight: 700;
  letter-spacing: 0.06em;
  text-transform: uppercase;
}

.muted-tag {
  background: rgba(147, 164, 218, 0.12);
  color: var(--text-soft);
}

.template-mini strong {
  font-size: 0.92rem;
}

.template-mini small {
  color: var(--text-soft);
  line-height: 1.5;
}

.game-grid {
  grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
}

.game-card {
  display: grid;
  gap: 0.8rem;
  width: 100%;
  padding: 0.85rem;
  border: 1px solid rgba(131, 147, 203, 0.18);
  border-radius: 16px;
  background: rgba(12, 20, 32, 0.9);
  color: var(--text);
  text-align: left;
}

.project-tile {
  min-height: 100%;
}

.game-thumb,
.template-visual,
.community-art {
  display: grid;
  place-items: center;
  min-height: 8rem;
  border-radius: 14px;
  overflow: hidden;
}

.gradient-orb {
  background: linear-gradient(135deg, rgba(80, 113, 255, 0.8), rgba(164, 126, 255, 0.65), rgba(65, 187, 255, 0.7));
}

.game-thumb span,
.template-visual span,
.community-art span {
  font-size: 2.1rem;
  opacity: 0.92;
}

.game-card-head,
.meta-row,
.timeline-header,
.agent-card-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 0.5rem;
}

.game-card strong {
  font-size: 1rem;
  letter-spacing: -0.04em;
}

.game-card p,
.template-meta p,
.timeline-card p,
.agent-card p,
.community-meta small {
  margin: 0;
  color: var(--text-soft);
  line-height: 1.6;
}

.meta-row {
  flex-wrap: wrap;
  color: var(--text-soft);
  font-size: 0.72rem;
}

.muted-row {
  color: var(--text-soft);
}

.action-row {
  display: flex;
  flex-wrap: wrap;
  gap: 0.55rem;
}

.secondary-actions {
  padding-top: 0.35rem;
  border-top: 1px solid rgba(136, 153, 211, 0.12);
}

.link-button.danger {
  color: var(--danger);
}

.side-panel {
  display: grid;
  gap: 1rem;
  align-content: start;
}

.mini-stat-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 0.7rem;
}

.mini-card {
  display: grid;
  gap: 0.45rem;
  padding: 0.8rem 0.7rem;
  border: 1px solid rgba(137, 155, 214, 0.18);
  border-radius: 14px;
  background: rgba(13, 20, 33, 0.8);
}

.storage-meter {
  width: 100%;
  height: 0.7rem;
  overflow: hidden;
  border-radius: 999px;
  background: rgba(120, 138, 192, 0.14);
}

.storage-meter span {
  display: block;
  height: 100%;
  border-radius: inherit;
  background: linear-gradient(90deg, #70acff, #8d78ff, #6ee0cc);
}

.full-width,
.width-full {
  width: 100%;
}

.centered-stack {
  max-width: 1100px;
}

.template-grid {
  grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));
}

.template-card {
  overflow: hidden;
  padding: 0;
}

.template-visual {
  min-height: 8.8rem;
}

.template-meta {
  display: grid;
  gap: 0.8rem;
  padding: 1rem;
}

.agent-grid {
  grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));
}

.agent-card {
  display: grid;
  gap: 1rem;
  padding: 1rem;
}

.agent-bubble {
  display: grid;
  place-items: center;
  width: 2.5rem;
  height: 2.5rem;
  border-radius: 12px;
  font-size: 0.7rem;
  font-weight: 800;
}

.agent-bubble.blue {
  background: rgba(80, 116, 255, 0.18);
  color: #dfe7ff;
}

.agent-bubble.purple {
  background: rgba(162, 114, 255, 0.18);
  color: #ebdfff;
}

.agent-workflow {
  display: grid;
  gap: 0.35rem;
  padding-top: 0.85rem;
  border-top: 1px solid rgba(138, 155, 211, 0.15);
}

.agent-workflow small {
  color: var(--text-soft);
  font-size: 0.66rem;
  text-transform: uppercase;
  letter-spacing: 0.12rem;
}

.timeline-grid,
.community-grid {
  grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
}

.timeline-card,
.community-card {
  display: grid;
  gap: 0.9rem;
  padding: 1rem;
}

.small-button {
  min-height: 2.2rem;
  padding: 0.45rem 0.7rem;
  font-size: 0.72rem;
}

.community-card {
  min-width: 0;
}

.community-art {
  min-height: 7rem;
}

.community-meta {
  display: grid;
  gap: 0.5rem;
}

.metrics-grid {
  grid-template-columns: repeat(4, minmax(0, 1fr));
}

.metric-card {
  display: grid;
  gap: 0.4rem;
  padding: 1rem;
}

.chart-bars {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  align-items: end;
  gap: 1rem;
  min-height: 14rem;
  padding-top: 1rem;
}

.chart-column {
  display: grid;
  justify-items: center;
  gap: 0.45rem;
  min-height: 100%;
}

.chart-bar-wrap {
  display: flex;
  align-items: end;
  width: 100%;
  height: 10rem;
  padding: 0.2rem;
  border-radius: 14px;
  background: rgba(12, 18, 32, 0.9);
  border: 1px solid rgba(132, 149, 202, 0.18);
}

.chart-bar-wrap span {
  display: block;
  width: 100%;
  border-radius: 10px 10px 0 0;
  background: linear-gradient(180deg, rgba(122, 142, 255, 0.95), rgba(122, 224, 255, 0.6));
}

.chart-column small {
  color: var(--text-soft);
  font-size: 0.67rem;
}

.settings-grid {
  grid-template-columns: repeat(2, minmax(0, 1fr));
}

.profile-summary {
  display: flex;
  align-items: center;
  gap: 1rem;
  margin-bottom: 1.2rem;
}

.profile-avatar.large-avatar {
  width: 3rem;
  height: 3rem;
  border-radius: 1rem;
  font-size: 1rem;
}

.profile-summary strong,
.setting-row strong {
  display: block;
  font-size: 1rem;
}

.profile-summary small {
  display: block;
  margin-top: 0.2rem;
  color: var(--text-soft);
}

.settings-stack {
  display: grid;
  gap: 0.8rem;
}

.setting-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 1rem;
  padding: 0.85rem 0.9rem;
  border: 1px solid rgba(136, 152, 199, 0.18);
  border-radius: 12px;
  background: rgba(10, 17, 28, 0.9);
}

.setting-row span {
  color: var(--text-soft);
}

.spec-summary {
  display: grid;
  gap: 1rem;
  padding: 1.15rem;
}

.spec-heading {
  display: flex;
  justify-content: space-between;
  gap: 1rem;
  align-items: center;
}

.spec-heading h3 {
  margin: 0.3rem 0 0;
  font-size: 1.5rem;
  letter-spacing: -0.07em;
}

.provider-badge {
  display: inline-flex;
  align-items: center;
  padding: 0.3rem 0.55rem;
  border-radius: 999px;
  background: rgba(114, 136, 255, 0.12);
  color: #d5deff;
  font-size: 0.62rem;
  font-weight: 800;
  letter-spacing: 0.12em;
  text-transform: uppercase;
}

.qa-panel {
  display: grid;
  gap: 0.4rem;
  padding: 0.8rem 0.9rem;
  border: 1px solid rgba(139, 155, 210, 0.16);
  border-radius: 12px;
  background: rgba(11, 18, 29, 0.82);
}

.qa-panel strong {
  font-size: 0.78rem;
  letter-spacing: 0.02em;
}

.qa-panel p {
  margin: 0;
  color: var(--text-soft);
  line-height: 1.6;
}

.spec-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 0.75rem;
}

.spec-grid span {
  display: flex;
  justify-content: space-between;
  gap: 0.5rem;
  padding: 0.7rem 0.8rem;
  border: 1px solid rgba(138, 154, 211, 0.15);
  border-radius: 10px;
  background: rgba(11, 17, 29, 0.8);
  color: var(--text-soft);
  font-size: 0.74rem;
}

.spec-grid strong {
  color: var(--text);
  font-size: 0.8rem;
}

details {
  border: 1px solid rgba(138, 154, 211, 0.15);
  border-radius: 12px;
  background: rgba(8, 15, 27, 0.8);
}

details summary {
  list-style: none;
  cursor: pointer;
  padding: 0.8rem 0.9rem;
  font-weight: 700;
}

details summary::-webkit-details-marker {
  display: none;
}

details pre {
  margin: 0;
  max-height: 13rem;
  overflow: auto;
  padding: 0 0.9rem 0.9rem;
  color: #d6dff8;
  font-size: 0.68rem;
  line-height: 1.6;
}

.preview-card {
  padding: 1rem;
}

.preview-header,
.preview-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 1rem;
}

.preview-header {
  margin-bottom: 0.8rem;
}

.preview-header h3 {
  margin: 0.3rem 0 0;
  font-size: 1.2rem;
}

.control-hint {
  max-width: 15rem;
  color: var(--text-soft);
  font-size: 0.72rem;
  line-height: 1.5;
  text-align: right;
}

.preview-toolbar {
  margin-bottom: 0.9rem;
}

.inline-actions {
  display: flex;
  gap: 0.5rem;
}

.preview-frame {
  display: block;
  width: 100%;
  min-height: 22rem;
  border: 1px solid rgba(131, 147, 203, 0.18);
  border-radius: 14px;
  overflow: hidden;
  background: rgba(6, 11, 20, 0.9);
}

.preview-frame canvas {
  display: block;
  width: 100% !important;
  height: 100% !important;
  max-width: 100%;
}

.inline-note,
.inline-success,
.error-message,
.inline-error,
.auth-error {
  margin: 0.8rem 0 0;
  font-size: 0.8rem;
  line-height: 1.5;
}

.inline-note,
.auth-error,
.error-message,
.inline-error {
  color: var(--danger);
}

.inline-success {
  color: var(--success);
}

.status-pill {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-height: 1.7rem;
  padding: 0.2rem 0.6rem;
  border-radius: 999px;
  font-size: 0.62rem;
  font-weight: 800;
  letter-spacing: 0.08em;
  text-transform: uppercase;
}

.status-pill.success {
  background: rgba(112, 224, 181, 0.12);
  color: #baf5df;
}

.status-pill.warning {
  background: rgba(255, 209, 102, 0.12);
  color: #ffe8a4;
}

.status-pill.danger {
  background: rgba(255, 107, 125, 0.12);
  color: #ffc2cb;
}

.status-pill.neutral,
.status-pill.pending,
.status-pill.running,
.status-pill.not-tested {
  background: rgba(133, 151, 196, 0.12);
  color: #dfe6ff;
}

.status-pill.passed,
.status-pill.completed {
  background: rgba(112, 224, 181, 0.12);
  color: #baf5df;
}

.status-pill.failed {
  background: rgba(255, 107, 125, 0.12);
  color: #ffc2cb;
}

.workflow-panel {
  display: grid;
  gap: 0.8rem;
  margin-top: 1rem;
  padding: 0.9rem;
  border: 1px solid rgba(138, 155, 211, 0.15);
  border-radius: 14px;
  background: rgba(8, 14, 24, 0.8);
}

.compact-heading {
  margin-bottom: 0;
}

.elapsed-time {
  color: var(--text-soft);
  font-size: 0.7rem;
  font-weight: 700;
}

.workflow-timeline {
  list-style: none;
  margin: 0;
  padding: 0;
  display: grid;
  gap: 0.7rem;
}

.workflow-step {
  display: grid;
  grid-template-columns: 1.5rem minmax(0, 1fr);
  gap: 0.7rem;
  align-items: start;
  padding: 0.65rem 0.2rem;
  border-bottom: 1px solid rgba(141, 156, 211, 0.12);
}

.workflow-step:last-child {
  border-bottom: 0;
}

.step-marker {
  display: grid;
  place-items: center;
  width: 1.5rem;
  height: 1.5rem;
  border-radius: 50%;
  background: rgba(122, 138, 200, 0.14);
  color: var(--text-soft);
  font-size: 0.8rem;
}

.workflow-step.completed .step-marker {
  background: rgba(112, 224, 181, 0.14);
  color: #bff7e0;
}

.workflow-step.failed .step-marker {
  background: rgba(255, 107, 125, 0.12);
  color: #ffc2cb;
}

.step-heading {
  display: flex;
  justify-content: space-between;
  gap: 0.8rem;
  align-items: center;
  margin-bottom: 0.3rem;
}

.step-heading strong {
  font-size: 0.76rem;
}

.workflow-step p {
  margin: 0;
  color: var(--text-soft);
  font-size: 0.72rem;
  line-height: 1.55;
}

.ai-panel {
  display: grid;
  align-content: start;
}

.agent-summary {
  display: flex;
  align-items: center;
  gap: 0.8rem;
  padding: 0.8rem 0.7rem;
  border: 1px solid rgba(135, 153, 211, 0.14);
  border-radius: 12px;
  background: rgba(11, 18, 29, 0.8);
}

.agent-summary strong {
  display: block;
  margin-bottom: 0.2rem;
}

.agent-summary p {
  margin: 0;
  color: var(--text-soft);
  line-height: 1.6;
  font-size: 0.76rem;
}

.auth-overlay {
  position: fixed;
  inset: 0;
  z-index: 25;
  display: grid;
  place-items: center;
  padding: 2rem 1rem;
  background: rgba(2, 5, 13, 0.68);
  backdrop-filter: blur(14px);
}

.auth-card {
  position: relative;
  width: min(100%, 28rem);
  display: grid;
  gap: 0.9rem;
  padding: 1.5rem 1.3rem 1.3rem;
  border: 1px solid rgba(133, 150, 206, 0.25);
  border-radius: 22px;
  background: linear-gradient(180deg, rgba(18, 25, 39, 0.96), rgba(10, 15, 26, 0.98));
  box-shadow: var(--shadow);
}

.auth-mark {
  display: grid;
  place-items: center;
  width: 2.6rem;
  height: 2.6rem;
  border-radius: 0.9rem;
  background: linear-gradient(135deg, #7d8cff, #67a2ff);
  color: white;
  font-weight: 800;
}

.auth-card h2 {
  margin: 0.2rem 0 0;
  font-size: clamp(1.7rem, 4vw, 2.3rem);
  letter-spacing: -0.08em;
}

.auth-description {
  margin: -0.2rem 0 0.2rem;
  color: var(--text-soft);
  font-size: 0.8rem;
  line-height: 1.6;
}

.auth-links {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 0.7rem;
  margin-top: -0.1rem;
}

.auth-close {
  position: absolute;
  top: 0.8rem;
  right: 0.8rem;
  display: grid;
  place-items: center;
  width: 2.1rem;
  height: 2.1rem;
  border: 1px solid rgba(142, 158, 210, 0.2);
  border-radius: 10px;
  background: rgba(12, 16, 24, 0.65);
  color: var(--text-soft);
  font-size: 1.6rem;
}

.empty-state {
  display: grid;
  place-items: center;
  gap: 0.6rem;
  min-height: 11rem;
  padding: 1.5rem;
  text-align: center;
  border: 1px dashed rgba(136, 151, 203, 0.2);
  border-radius: 16px;
  background: rgba(12, 18, 29, 0.72);
}

.empty-state h4 {
  margin: 0;
  font-size: 1.15rem;
}

.empty-state p {
  margin: 0;
  color: var(--text-soft);
  line-height: 1.6;
}

.compact-empty {
  min-height: 8rem;
}

.loading-panel {
  display: inline-flex;
  align-items: center;
  gap: 0.6rem;
  padding: 0.8rem 1rem;
  border: 1px solid rgba(131, 147, 201, 0.2);
  border-radius: 10px;
  background: rgba(10, 18, 29, 0.8);
  color: var(--text-soft);
}

@media (max-width: 1200px) {
  .panel-row,
  .settings-grid {
    grid-template-columns: 1fr;
  }

  .stats-grid {
    grid-template-columns: 1fr;
  }
}

@media (max-width: 980px) {
  .app-shell {
    grid-template-columns: 1fr;
  }

  .sidebar {
    position: static;
    height: auto;
    border-right: 0;
    border-bottom: 1px solid rgba(137, 155, 214, 0.16);
  }

  .main-nav {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .page-content,
  .topbar {
    padding-left: 1rem;
    padding-right: 1rem;
  }
}

@media (max-width: 640px) {
  .main-nav {
    grid-template-columns: 1fr;
  }

  .metrics-grid,
  .game-grid,
  .template-grid,
  .agent-grid,
  .community-grid,
  .timeline-grid {
    grid-template-columns: 1fr;
  }

  .field-row,
  .prompt-footer,
  .toolbar-row,
  .auth-links,
  .topbar,
  .preview-header,
  .preview-toolbar,
  .section-heading {
    flex-direction: column;
    align-items: stretch;
  }

  .hero-actions {
    flex-direction: column;
    align-items: stretch;
  }
}
'''

theme = '''
:root {
  --page: #07111d;
  --page-alt: #101b2d;
  --panel: rgba(17, 24, 37, 0.9);
  --panel-strong: rgba(11, 16, 27, 0.92);
  --panel-border: rgba(129, 150, 212, 0.22);
  --text: #edf6ff;
  --text-soft: #9bb0d6;
  --accent: #7d8cff;
  --accent-blue: #69b8ff;
  --success: #73d7b4;
  --danger: #ff6f82;
  --warning: #ffd166;
  --shadow: 0 28px 60px rgba(3, 7, 13, 0.44);
}

html, body, #root {
  min-height: 100%;
  margin: 0;
}

body {
  background: var(--page);
  color: var(--text);
}

a {
  color: inherit;
  text-decoration: none;
}

button,
input,
select,
textarea {
  font: inherit;
}

button:focus-visible,
input:focus-visible,
select:focus-visible,
textarea:focus-visible,
summary:focus-visible {
  outline: 2px solid rgba(130, 163, 255, 0.8);
  outline-offset: 3px;
}
'''

Path('C:/Projects/gameionx/frontend/src/App.jsx').write_text(app, encoding='utf-8')
Path('C:/Projects/gameionx/frontend/src/App.css').write_text(css, encoding='utf-8')
Path('C:/Projects/gameionx/frontend/src/theme.css').write_text(theme, encoding='utf-8')
print('written')
