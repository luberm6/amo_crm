import { act, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { AuthProvider } from '../auth/AuthContext'
import ProtectedRoute from '../components/ProtectedRoute'
import AdminLayout from '../layout/AdminLayout'
import BrowserCallPage from '../pages/BrowserCallPage'

class FakeTrack {
  enabled = true
  muted = false
  readyState: MediaStreamTrackState = 'live'
  stop = vi.fn()
}

class FakeMediaStream {
  track = new FakeTrack()

  getAudioTracks() {
    return [this.track] as unknown as MediaStreamTrack[]
  }

  getTracks() {
    return [this.track] as unknown as MediaStreamTrack[]
  }
}

class FakeAudioContext {
  static instances: FakeAudioContext[] = []

  state: AudioContextState = 'suspended'
  sampleRate = 48000
  currentTime = 0
  destination = {} as AudioDestinationNode
  processor: ScriptProcessorNode | null = null
  source: MediaStreamAudioSourceNode | null = null
  gainNode: GainNode | null = null
  bufferSourceStarts: number[] = []
  resume = vi.fn(async () => {
    this.state = 'running'
  })
  close = vi.fn(async () => {
    this.state = 'closed'
  })

  constructor() {
    FakeAudioContext.instances.push(this)
  }

  createMediaStreamSource() {
    this.source = {
      connect: vi.fn(),
      disconnect: vi.fn(),
    } as unknown as MediaStreamAudioSourceNode
    return this.source
  }

  createScriptProcessor() {
    this.processor = {
      connect: vi.fn(),
      disconnect: vi.fn(),
      onaudioprocess: null,
    } as unknown as ScriptProcessorNode
    return this.processor
  }

  createGain() {
    this.gainNode = {
      gain: { value: 1 },
      connect: vi.fn(),
      disconnect: vi.fn(),
    } as unknown as GainNode
    return this.gainNode
  }

  createBuffer(_channels: number, length: number, sampleRate: number) {
    return {
      duration: length / sampleRate,
      numberOfChannels: 1,
      length,
      copyToChannel: vi.fn(),
    } as unknown as AudioBuffer
  }

  createBufferSource() {
    return {
      buffer: null,
      connect: vi.fn(),
      start: vi.fn((time: number) => {
        this.bufferSourceStarts.push(time)
        this.currentTime = time
      }),
    } as unknown as AudioBufferSourceNode
  }
}

class FakeWebSocket {
  static CONNECTING = 0
  static OPEN = 1
  static CLOSING = 2
  static CLOSED = 3
  static instances: FakeWebSocket[] = []

  url: string
  readyState = FakeWebSocket.CONNECTING
  binaryType: BinaryType = 'blob'
  onopen: ((event: Event) => void) | null = null
  onclose: ((event: CloseEvent) => void) | null = null
  onerror: ((event: Event) => void) | null = null
  onmessage: ((event: MessageEvent) => void) | null = null
  send = vi.fn()
  close = vi.fn(() => {
    this.readyState = FakeWebSocket.CLOSED
    this.onclose?.({} as CloseEvent)
  })

  constructor(url: string) {
    this.url = url
    FakeWebSocket.instances.push(this)
  }

  open() {
    this.readyState = FakeWebSocket.OPEN
    this.onopen?.(new Event('open'))
  }

  receiveBytes(buffer: ArrayBuffer) {
    this.onmessage?.({ data: buffer } as MessageEvent)
  }
}

function renderBrowserCallPage() {
  return render(
    <MemoryRouter initialEntries={['/browser-call']}>
      <AuthProvider>
        <Routes>
          <Route element={<ProtectedRoute />}>
            <Route element={<AdminLayout />}>
              <Route path="/browser-call" element={<BrowserCallPage />} />
            </Route>
          </Route>
        </Routes>
      </AuthProvider>
    </MemoryRouter>,
  )
}

function setupCommonFetch() {
  return vi.spyOn(window, 'fetch').mockImplementation(async (input, init) => {
    const path = typeof input === 'string' ? input : input.toString()
    if (path.includes('/v1/admin/auth/me')) {
      return new Response(JSON.stringify({ email: 'admin@example.com', role: 'admin' }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      })
    }
    if (path.includes('/v1/agents?active_only=true')) {
      return new Response(JSON.stringify({
        items: [
          { id: 'agent-1', name: 'Sales Alpha', is_active: true },
        ],
        total: 1,
      }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      })
    }
    if (path.endsWith('/v1/browser-calls') && init?.method === 'POST') {
      return new Response(JSON.stringify({
        call_id: 'call-1',
        status: 'IN_PROGRESS',
        session_id: 'call-1-direct',
        agent_profile_id: 'agent-1',
        browser_token: 'browser-token',
        websocket_url: 'ws://localhost/v1/browser-calls/call-1/ws?token=browser-token',
        status_url: '/v1/browser-calls/call-1',
        stop_url: '/v1/browser-calls/call-1/stop',
        voice_strategy: 'tts_primary',
        active_voice_path: 'tts_primary',
        fallback_voice_path: null,
      }), {
        status: 201,
        headers: { 'Content-Type': 'application/json' },
      })
    }
    if (path.endsWith('/v1/browser-calls/call-1') && (!init || !init.method || init.method === 'GET')) {
      return new Response(JSON.stringify({
        call_id: 'call-1',
        status: 'IN_PROGRESS',
        label: 'sandbox',
        agent_profile_id: 'agent-1',
        created_at: '2026-01-01T00:00:00Z',
        completed_at: null,
        transcript_entries: [],
        debug: {
          session_id: 'call-1-direct',
          voice_strategy: 'tts_primary',
          active_voice_path: 'tts_primary',
          primary_voice_path: 'tts_primary',
          fallback_voice_path: null,
          fallback_used: false,
          session_mode: 'full_duplex',
          websocket_connected: true,
          bridge_open: true,
          inbound_chunks_received: 0,
          inbound_chunks_sent_to_model: 0,
          outbound_chunks_played: 0,
          model_response_latency_ms_last: null,
          tts_latency_ms_last: null,
          outbound_playback_latency_ms_last: null,
          last_error: null,
          last_failure_stage: null,
          last_disconnect_reason: null,
        },
      }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      })
    }
    if (path.endsWith('/v1/browser-calls/call-1/stop') && init?.method === 'POST') {
      return new Response(JSON.stringify({
        call_id: 'call-1',
        status: 'STOPPED',
        label: 'sandbox',
        agent_profile_id: 'agent-1',
        created_at: '2026-01-01T00:00:00Z',
        completed_at: '2026-01-01T00:00:05Z',
        transcript_entries: [],
        debug: {
          session_id: 'call-1-direct',
          voice_strategy: 'tts_primary',
          active_voice_path: 'tts_primary',
          primary_voice_path: 'tts_primary',
          fallback_voice_path: null,
          fallback_used: false,
          session_mode: 'full_duplex',
          websocket_connected: false,
          bridge_open: false,
          inbound_chunks_received: 0,
          inbound_chunks_sent_to_model: 0,
          outbound_chunks_played: 0,
          model_response_latency_ms_last: null,
          tts_latency_ms_last: null,
          outbound_playback_latency_ms_last: null,
          last_error: null,
          last_failure_stage: null,
          last_disconnect_reason: 'browser_stop',
        },
      }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      })
    }
    if (path.endsWith('/v1/browser-calls/call-1/debug/test-tone') && init?.method === 'POST') {
      return new Response(JSON.stringify({
        ok: true,
        action: 'test_tone',
        message: 'Backend test tone enqueued for browser playback',
        chunks_enqueued: 75,
      }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      })
    }
    if (path.endsWith('/v1/browser-calls/call-1/debug/test-tts') && init?.method === 'POST') {
      return new Response(JSON.stringify({
        ok: true,
        action: 'test_tts',
        message: 'TTS debug playback enqueued for browser playback',
        chunks_enqueued: 4,
      }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      })
    }
    throw new Error(`Unexpected fetch path: ${path}`)
  })
}

describe('browser call page smoke', () => {
  beforeEach(() => {
    window.localStorage.setItem('amo_admin_token', 'token-123')
    vi.restoreAllMocks()
    FakeAudioContext.instances = []
    FakeWebSocket.instances = []
    Object.defineProperty(URL, 'createObjectURL', {
      value: vi.fn(() => 'blob:test-audio'),
      configurable: true,
    })
    Object.defineProperty(URL, 'revokeObjectURL', {
      value: vi.fn(),
      configurable: true,
    })
    Object.defineProperty(HTMLCanvasElement.prototype, 'getContext', {
      value: vi.fn(() => ({
        clearRect: vi.fn(),
        fillRect: vi.fn(),
        beginPath: vi.fn(),
        moveTo: vi.fn(),
        lineTo: vi.fn(),
        stroke: vi.fn(),
        fillStyle: '#f5f1e8',
        strokeStyle: '#1d5c4d',
        lineWidth: 2,
      })),
      configurable: true,
    })
    Object.defineProperty(window, 'AudioContext', {
      value: FakeAudioContext,
      configurable: true,
    })
    Object.defineProperty(globalThis, 'AudioContext', {
      value: FakeAudioContext,
      configurable: true,
    })
    Object.defineProperty(window, 'WebSocket', {
      value: FakeWebSocket,
      configurable: true,
    })
    Object.defineProperty(globalThis, 'WebSocket', {
      value: FakeWebSocket,
      configurable: true,
    })
    Object.defineProperty(navigator, 'mediaDevices', {
      value: {
        getUserMedia: vi.fn(async () => new FakeMediaStream()),
        enumerateDevices: vi.fn(async () => [
          { kind: 'audiooutput', deviceId: 'default', label: 'System default' },
          { kind: 'audiooutput', deviceId: 'speaker-1', label: 'Built-in Speakers' },
        ]),
        addEventListener: vi.fn(),
        removeEventListener: vi.fn(),
      },
      configurable: true,
    })
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  it('renders agent picker and debug surface', async () => {
    setupCommonFetch()

    renderBrowserCallPage()

    await waitFor(() => {
      expect(screen.getAllByText(/Браузерный звонок|Browser Call/i).length).toBeGreaterThan(0)
    })
    expect(screen.getByLabelText(/Профиль агента|Agent profile/i)).toBeInTheDocument()
    expect(screen.getByLabelText(/Устройство вывода|Output device/i)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Начать тестовый звонок|Start Test Call/i })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Завершить звонок|Stop Test Call/i })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Тест микрофона.*аудиопетля/i })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Тестовый тон с backend|Play Test Tone from Backend/i })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Тест TTS|Test TTS/i })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Громкий тестовый тон|Play Loud Test Tone/i })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Скачать последнее аудио|Download last audio/i })).toBeInTheDocument()
    expect(screen.getByText(/Живой разговор|Live conversation/i)).toBeInTheDocument()
    expect(screen.getByText(/Состояние сессии|Session state/i)).toBeInTheDocument()
  })

  it('starts browser audio runtime, resumes AudioContext, streams microphone PCM and plays inbound audio', async () => {
    const user = userEvent.setup()
    setupCommonFetch()

    renderBrowserCallPage()

    await waitFor(() => {
      expect(screen.getByRole('button', { name: /Начать тестовый звонок|Start Test Call/i })).toBeInTheDocument()
    })

    await user.click(screen.getByRole('button', { name: /Начать тестовый звонок|Start Test Call/i }))

    await waitFor(() => {
      expect(FakeWebSocket.instances).toHaveLength(1)
    })
    const socket = FakeWebSocket.instances[0]
    await act(async () => {
      socket.open()
    })

    await waitFor(() => {
      expect(FakeAudioContext.instances).toHaveLength(1)
    })
    const context = FakeAudioContext.instances[0]
    expect(context.resume).toHaveBeenCalled()

    const processor = context.processor
    expect(processor).not.toBeNull()
    const onAudioProcess = processor?.onaudioprocess as unknown as ((event: AudioProcessingEvent) => void) | null
    expect(onAudioProcess).toBeTruthy()
    await act(async () => {
      onAudioProcess?.({
        inputBuffer: {
          getChannelData: () => new Float32Array([0, 0.1, -0.1, 0.25]),
        },
      } as unknown as AudioProcessingEvent)
    })
    expect(socket.send).toHaveBeenCalledTimes(1)

    await act(async () => {
      socket.receiveBytes(new Int16Array([0, 1024, -1024, 2048]).buffer)
    })
    await waitFor(() => {
      expect(context.bufferSourceStarts.length).toBeGreaterThan(0)
    })

    expect(screen.getByText(/веб-сокет/i)).toBeInTheDocument()
    expect(screen.getByText(/исходящих/i)).toBeInTheDocument()
    expect(screen.getByText(/входящих/i)).toBeInTheDocument()
    expect(screen.getByText(/стартов playback/i)).toBeInTheDocument()
    expect(screen.getByText(/RMS/i)).toBeInTheDocument()
    expect(screen.getByLabelText(/Устройство вывода|Output device/i)).toBeInTheDocument()
    expect(screen.getByText(/вход SR/i)).toBeInTheDocument()
    expect(screen.getAllByText(/здоровье/i).length).toBeGreaterThan(0)
    expect(screen.getByText(/wav валиден/i)).toBeInTheDocument()
    expect(screen.getByLabelText(/Форма входящего аудио/i)).toBeInTheDocument()
  })

  it('reassembles odd-length PCM websocket frames before playback', async () => {
    const user = userEvent.setup()
    setupCommonFetch()

    renderBrowserCallPage()

    await waitFor(() => {
      expect(screen.getByRole('button', { name: /Начать тестовый звонок|Start Test Call/i })).toBeInTheDocument()
    })

    await user.click(screen.getByRole('button', { name: /Начать тестовый звонок|Start Test Call/i }))
    await waitFor(() => {
      expect(FakeWebSocket.instances).toHaveLength(1)
    })
    const socket = FakeWebSocket.instances[0]
    await act(async () => {
      socket.open()
    })

    await waitFor(() => {
      expect(FakeAudioContext.instances).toHaveLength(1)
    })
    const context = FakeAudioContext.instances[0]

    await act(async () => {
      socket.receiveBytes(new Uint8Array([0x01]).buffer)
      socket.receiveBytes(new Uint8Array([0x02, 0x03, 0x04]).buffer)
    })
    await waitFor(() => {
      expect(context.bufferSourceStarts.length).toBeGreaterThan(0)
    })
  })

  it('starts playback from the first voiced chunk and ignores startup silence frames', async () => {
    const user = userEvent.setup()
    setupCommonFetch()

    renderBrowserCallPage()

    await waitFor(() => {
      expect(screen.getByRole('button', { name: /Начать тестовый звонок|Start Test Call/i })).toBeInTheDocument()
    })

    await user.click(screen.getByRole('button', { name: /Начать тестовый звонок|Start Test Call/i }))
    await waitFor(() => {
      expect(FakeWebSocket.instances).toHaveLength(1)
    })
    const socket = FakeWebSocket.instances[0]
    await act(async () => {
      socket.open()
    })

    await waitFor(() => {
      expect(FakeAudioContext.instances).toHaveLength(1)
    })
    const context = FakeAudioContext.instances[0]

    await act(async () => {
      socket.onmessage?.({
        data: JSON.stringify({
          type: 'tts_turn_metrics',
          phase: 'started',
          turn_id: 'turn-1',
          tts_first_chunk_sent_to_bridge_ms: 180,
          tts_first_non_silent_chunk_sent_ms: 220,
          tts_provider_leading_silence_ms: 60,
          tts_backend_leading_silence_ms: 60,
        }),
      } as MessageEvent)
    })

    await act(async () => {
      socket.receiveBytes(new Int16Array(320).buffer)
    })
    await act(async () => {
      await new Promise((resolve) => window.setTimeout(resolve, 70))
    })
    expect(context.bufferSourceStarts).toHaveLength(0)

    const voiced = new Int16Array(320)
    voiced.fill(9000)
    await act(async () => {
      socket.receiveBytes(voiced.buffer)
    })
    await waitFor(() => {
      expect(context.bufferSourceStarts.length).toBeGreaterThan(0)
    })
  })

  it('does not create a backend session when microphone permission is denied', async () => {
    const user = userEvent.setup()
    const fetchSpy = setupCommonFetch()
    Object.defineProperty(navigator, 'mediaDevices', {
      value: {
        getUserMedia: vi.fn(async () => {
          throw new DOMException('Permission denied', 'NotAllowedError')
        }),
        enumerateDevices: vi.fn(async () => [{ kind: 'audiooutput', deviceId: 'default', label: 'System default' }]),
        addEventListener: vi.fn(),
        removeEventListener: vi.fn(),
      },
      configurable: true,
    })

    renderBrowserCallPage()

    await waitFor(() => {
      expect(screen.getByRole('button', { name: /Начать тестовый звонок|Start Test Call/i })).toBeInTheDocument()
    })

    await user.click(screen.getByRole('button', { name: /Начать тестовый звонок|Start Test Call/i }))

    await waitFor(() => {
      expect(screen.getAllByText(/Permission denied|Не удалось начать браузерный звонок/i).length).toBeGreaterThan(0)
    })
    const browserCreateCalls = fetchSpy.mock.calls.filter(([input, init]) => {
      const path = typeof input === 'string' ? input : input.toString()
      return path.includes('/v1/browser-calls') && init?.method === 'POST'
    })
    expect(browserCreateCalls).toHaveLength(0)
  })

  it('stops backend session if websocket reports a transport error before connect', async () => {
    const user = userEvent.setup()
    const fetchSpy = setupCommonFetch()

    renderBrowserCallPage()

    await waitFor(() => {
      expect(screen.getByRole('button', { name: /Начать тестовый звонок|Start Test Call/i })).toBeInTheDocument()
    })

    await user.click(screen.getByRole('button', { name: /Начать тестовый звонок|Start Test Call/i }))

    await waitFor(() => {
      expect(FakeWebSocket.instances).toHaveLength(1)
    })
    FakeWebSocket.instances[0].onerror?.(new Event('error'))

    await waitFor(() => {
      const stopCalls = fetchSpy.mock.calls.filter(([input, init]) => {
        const path = typeof input === 'string' ? input : input.toString()
        return path.includes('/v1/browser-calls/call-1/stop') && init?.method === 'POST'
      })
      expect(stopCalls.length).toBeGreaterThan(0)
    })
  })

  it('runs local loopback without creating a backend session', async () => {
    const user = userEvent.setup()
    const fetchSpy = setupCommonFetch()

    renderBrowserCallPage()

    await waitFor(() => {
      expect(screen.getByRole('button', { name: /Тест микрофона.*аудиопетля/i })).toBeInTheDocument()
    })

    await user.click(screen.getByRole('button', { name: /Тест микрофона.*аудиопетля/i }))

    await waitFor(() => {
      expect(FakeAudioContext.instances.length).toBeGreaterThan(0)
    })

    const browserCreateCalls = fetchSpy.mock.calls.filter(([input, init]) => {
      const path = typeof input === 'string' ? input : input.toString()
      return path.includes('/v1/browser-calls') && init?.method === 'POST'
    })
    expect(browserCreateCalls).toHaveLength(0)
    expect(screen.getByRole('button', { name: /Остановить аудиопетлю/i })).toBeEnabled()
  })

  it('calls backend test tone and test tts actions on active session', async () => {
    const user = userEvent.setup()
    const fetchSpy = setupCommonFetch()

    renderBrowserCallPage()

    await waitFor(() => {
      expect(screen.getByRole('button', { name: /Начать тестовый звонок|Start Test Call/i })).toBeInTheDocument()
    })

    await user.click(screen.getByRole('button', { name: /Начать тестовый звонок|Start Test Call/i }))
    await waitFor(() => {
      expect(FakeWebSocket.instances).toHaveLength(1)
    })
    FakeWebSocket.instances[0].open()

    await user.click(screen.getByRole('button', { name: /Тестовый тон с backend|Play Test Tone from Backend/i }))
    await user.click(screen.getByRole('button', { name: /Тест TTS|Test TTS/i }))

    const debugCalls = fetchSpy.mock.calls.filter(([input, init]) => {
      const path = typeof input === 'string' ? input : input.toString()
      return (
        init?.method === 'POST'
        && (path.includes('/debug/test-tone') || path.includes('/debug/test-tts'))
      )
    })
    expect(debugCalls.length).toBeGreaterThanOrEqual(2)
  })

  it('plays hardcoded local audio without backend debug endpoint', async () => {
    const user = userEvent.setup()
    setupCommonFetch()

    renderBrowserCallPage()

    await waitFor(() => {
      expect(screen.getByRole('button', { name: /Громкий тестовый тон|Play Loud Test Tone/i })).toBeInTheDocument()
    })

    await user.click(screen.getByRole('button', { name: /Громкий тестовый тон|Play Loud Test Tone/i }))

    await waitFor(() => {
      expect(FakeAudioContext.instances.length).toBeGreaterThan(0)
    })
  })
})
