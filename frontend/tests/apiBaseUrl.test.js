import assert from 'node:assert/strict'
import test from 'node:test'

import { resolveApiBaseUrl } from '../src/lib/apiBaseUrl.js'

test('configured API base URL takes precedence over hostname detection', () => {
  assert.equal(
    resolveApiBaseUrl('https://api.example.test', '127.0.0.1'),
    'https://api.example.test',
  )
})

test('loopback frontend hosts use a same-site API host for session cookies', () => {
  assert.equal(
    resolveApiBaseUrl(undefined, '127.0.0.1'),
    'http://127.0.0.1:8000',
  )
  assert.equal(
    resolveApiBaseUrl(undefined, 'localhost'),
    'http://localhost:8000',
  )
})
