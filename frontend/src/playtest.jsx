import { useEffect, useState } from 'react'
import { createRoot } from 'react-dom/client'
import PhaserPreview from './components/PhaserPreview'

function PlaytestPage() {
  const [fixture, setFixture] = useState(null)
  const [error, setError] = useState('')

  useEffect(() => {
    fetch('/__gameionx_playtest_spec__')
      .then((response) => {
        if (!response.ok) throw new Error(`Playtest fixture request failed (${response.status}).`)
        return response.json()
      })
      .then(setFixture)
      .catch((requestError) => setError(requestError.message))
  }, [])

  if (error) return <p role="alert">{error}</p>
  if (!fixture) return <p role="status">Loading playtest fixture...</p>

  return (
    <main data-testid="playtest-ready">
      <h1>{fixture.spec.title}</h1>
      <PhaserPreview
        spec={{ ...fixture.spec, runtime_config: fixture.runtime_config }}
        gameDefinition={fixture.game_definition}
      />
    </main>
  )
}

createRoot(document.getElementById('root')).render(<PlaytestPage />)
