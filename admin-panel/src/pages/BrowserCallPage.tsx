import { useCallback, useEffect, useMemo, useRef, useState } from 'react'

import { useAuth } from '../auth/AuthContext'
import { ApiError, apiFetch } from '../lib/api'

type TranscriptEntry = {
  id: string
  role: string
  text: string
  created_at: string
}

type BrowserCallStartResponse = {
  call_id: string
  status: string
  session_id: string
  agent_profile_id?: string | null
  browser_token: string
  websocket_url: string
  status_url: string
  stop_url: string
  voice_strategy: string
  active_voice_path: string
  fallback_voice_path?: string | null
}

type BrowserCallRead = {
  call_id: string
  status: string
  label: string
  agent_profile_id?: string | null
  created_at: string
  completed_at?: string | null
  transcript_entries: TranscriptEntry[]
  debug: {
    session_id?: string | null
    voice_strategy?: string | null
    active_voice_path?: string | null
    primary_voice_path?: string | null
    fallback_voice_path?: string | null
    fallback_used: boolean
    session_mode?: string | null
    websocket_connected: boolean
    bridge_open: boolean
    inbound_chunks_received: number
    inbound_chunks_sent_to_model: number
    outbound_chunks_played: number
    model_response_latency_ms_last?: number | null
    tts_latency_ms_last?: number | null
    outbound_playback_latency_ms_last?: number | null
    last_error?: string | null
    last_failure_stage?: string | null
    last_disconnect_reason?: string | null
  }
}

type BrowserCallDebugActionResponse = {
  ok: boolean
  action: string
  message: string
  chunks_enqueued: number
}

type AgentListItem = {
  id: string
  name: string
  is_active: boolean
}

type AgentListResponse = {
  items: AgentListItem[]
  total: number
}

type AudioRuntime = {
  context: AudioContext
  stream: MediaStream
  source: MediaStreamAudioSourceNode
  processor: ScriptProcessorNode
  sink?: GainNode | null
  playbackGain?: GainNode | null
  playbackDestination?: MediaStreamAudioDestinationNode | null
  playbackElement?: HTMLAudioElement | null
}

type OutputDeviceOption = {
  deviceId: string
  label: string
}

type LocalAudioDebug = {
  micPermission: 'idle' | 'requested' | 'granted' | 'denied'
  websocketConnected: boolean
  audioContextState: string
  outboundChunkCount: number
  inboundAudioChunkCount: number
  playbackStarts: number
  lastPlaybackError: string | null
  lastTransportError: string | null
  inputSampleRate: number | null
  targetSampleRate: number
  inputChannelMode: string
  estimatedOutputLatencyMs: number | null
  playbackNodesCreated: number
  playbackEndedCount: number
  playbackGainValue: number
  playbackSampleRate: number | null
  playbackChannels: number
  playbackBitDepth: number
  playbackBufferLength: number
  playbackFormatMismatch: boolean
  playbackDiagnostic: 'PLAYBACK_FAILURE_LIKELY' | null
  lastRms: number | null
  lastPeak: number | null
  audioTooQuiet: boolean
  currentOutputDeviceId: string | null
  outputSelectionSupported: boolean
  outputDeviceError: string | null
}

const INITIAL_LOCAL_AUDIO_DEBUG: LocalAudioDebug = {
  micPermission: 'idle',
  websocketConnected: false,
  audioContextState: 'idle',
  outboundChunkCount: 0,
  inboundAudioChunkCount: 0,
  playbackStarts: 0,
  lastPlaybackError: null,
  lastTransportError: null,
  inputSampleRate: null,
  targetSampleRate: 16000,
  inputChannelMode: 'mono',
  estimatedOutputLatencyMs: null,
  playbackNodesCreated: 0,
  playbackEndedCount: 0,
  playbackGainValue: 1,
  playbackSampleRate: null,
  playbackChannels: 1,
  playbackBitDepth: 16,
  playbackBufferLength: 0,
  playbackFormatMismatch: false,
  playbackDiagnostic: null,
  lastRms: null,
  lastPeak: null,
  audioTooQuiet: false,
  currentOutputDeviceId: null,
  outputSelectionSupported: false,
  outputDeviceError: null,
}

type LoopbackDebug = {
  active: boolean
  starting: boolean
  micChunks: number
  playbackChunks: number
  estimatedLatencyMs: number | null
  lastError: string | null
}

const INITIAL_LOOPBACK_DEBUG: LoopbackDebug = {
  active: false,
  starting: false,
  micChunks: 0,
  playbackChunks: 0,
  estimatedLatencyMs: null,
  lastError: null,
}

const TARGET_PCM_SAMPLE_RATE = 16000
const TARGET_PCM_CHANNELS = 1
const TARGET_PCM_BIT_DEPTH = 16
const MIN_PLAYBACK_BUFFER_SAMPLES = 2048
const MIN_PLAYBACK_BUFFER_BYTES = MIN_PLAYBACK_BUFFER_SAMPLES * 2
const PLAYBACK_FLUSH_DELAY_MS = 120
const AUDIO_TOO_QUIET_RMS_THRESHOLD = 0.01

function downsampleBuffer(buffer: Float32Array<ArrayBufferLike>, inputSampleRate: number, outputSampleRate: number) {
  if (outputSampleRate === inputSampleRate) {
    return buffer
  }
  const ratio = inputSampleRate / outputSampleRate
  const newLength = Math.round(buffer.length / ratio)
  const result = new Float32Array(newLength)
  let offsetResult = 0
  let offsetBuffer = 0
  while (offsetResult < result.length) {
    const nextOffsetBuffer = Math.round((offsetResult + 1) * ratio)
    let accum = 0
    let count = 0
    for (let index = offsetBuffer; index < nextOffsetBuffer && index < buffer.length; index += 1) {
      accum += buffer[index]
      count += 1
    }
    result[offsetResult] = count ? accum / count : 0
    offsetResult += 1
    offsetBuffer = nextOffsetBuffer
  }
  return result
}

function floatTo16BitPCM(floatBuffer: Float32Array<ArrayBufferLike>) {
  const result = new Int16Array(floatBuffer.length)
  for (let index = 0; index < floatBuffer.length; index += 1) {
    const sample = Math.max(-1, Math.min(1, floatBuffer[index]))
    result[index] = sample < 0 ? sample * 0x8000 : sample * 0x7fff
  }
  return result
}

function int16ToFloat32(buffer: ArrayBuffer) {
  const int16 = new Int16Array(buffer)
  const float32 = new Float32Array(int16.length)
  for (let index = 0; index < int16.length; index += 1) {
    float32[index] = int16[index] / 0x7fff
  }
  return float32
}

function resampleFloat32Linear(
  buffer: Float32Array<ArrayBufferLike>,
  inputSampleRate: number,
  outputSampleRate: number,
) {
  if (inputSampleRate === outputSampleRate) {
    return buffer
  }
  const sampleRateRatio = inputSampleRate / outputSampleRate
  const outputLength = Math.max(1, Math.round(buffer.length / sampleRateRatio))
  const output = new Float32Array(outputLength)
  for (let index = 0; index < outputLength; index += 1) {
    const position = index * sampleRateRatio
    const left = Math.floor(position)
    const right = Math.min(left + 1, buffer.length - 1)
    const fraction = position - left
    const leftSample = buffer[left] ?? 0
    const rightSample = buffer[right] ?? leftSample
    output[index] = leftSample + (rightSample - leftSample) * fraction
  }
  return output
}

function concatArrayBuffers(chunks: ArrayBuffer[]) {
  const totalLength = chunks.reduce((sum, chunk) => sum + chunk.byteLength, 0)
  const output = new Uint8Array(totalLength)
  let offset = 0
  for (const chunk of chunks) {
    output.set(new Uint8Array(chunk), offset)
    offset += chunk.byteLength
  }
  return output
}

function pcm16ToWav(pcmChunks: ArrayBuffer[], sampleRate: number, channels: number, bitDepth: number) {
  const pcmData = concatArrayBuffers(pcmChunks)
  const wavHeader = new ArrayBuffer(44)
  const view = new DataView(wavHeader)
  const blockAlign = channels * (bitDepth / 8)
  const byteRate = sampleRate * blockAlign
  const dataSize = pcmData.byteLength

  const writeString = (offset: number, value: string) => {
    for (let index = 0; index < value.length; index += 1) {
      view.setUint8(offset + index, value.charCodeAt(index))
    }
  }

  writeString(0, 'RIFF')
  view.setUint32(4, 36 + dataSize, true)
  writeString(8, 'WAVE')
  writeString(12, 'fmt ')
  view.setUint32(16, 16, true)
  view.setUint16(20, 1, true)
  view.setUint16(22, channels, true)
  view.setUint32(24, sampleRate, true)
  view.setUint32(28, byteRate, true)
  view.setUint16(32, blockAlign, true)
  view.setUint16(34, bitDepth, true)
  writeString(36, 'data')
  view.setUint32(40, dataSize, true)

  return new Blob([wavHeader, pcmData], { type: 'audio/wav' })
}

function generateBeepPcm16({
  frequencyHz = 440,
  durationMs = 1000,
  sampleRate = TARGET_PCM_SAMPLE_RATE,
  amplitude = 0.68,
}: {
  frequencyHz?: number
  durationMs?: number
  sampleRate?: number
  amplitude?: number
}) {
  const totalSamples = Math.max(1, Math.round((durationMs / 1000) * sampleRate))
  const output = new Int16Array(totalSamples)
  for (let index = 0; index < totalSamples; index += 1) {
    const sample = Math.sin((2 * Math.PI * frequencyHz * index) / sampleRate)
    output[index] = Math.round(sample * 0x7fff * amplitude)
  }
  return output.buffer.slice(0)
}

function computeAudioStats(buffer: Float32Array<ArrayBufferLike>) {
  if (buffer.length === 0) {
    return { rms: 0, peak: 0 }
  }
  let sumSquares = 0
  let peak = 0
  for (let index = 0; index < buffer.length; index += 1) {
    const value = Math.abs(buffer[index] ?? 0)
    sumSquares += value * value
    if (value > peak) {
      peak = value
    }
  }
  return {
    rms: Math.sqrt(sumSquares / buffer.length),
    peak,
  }
}

function formatMetric(value?: number | null) {
  return value == null ? '—' : `${Math.round(value)} ms`
}

function normalizeApiPath(urlOrPath: string): string {
  if (urlOrPath.startsWith('/')) {
    return urlOrPath
  }
  try {
    const parsed = new URL(urlOrPath)
    return `${parsed.pathname}${parsed.search}${parsed.hash}`
  } catch {
    return urlOrPath
  }
}

function downloadBlob(url: string, filename: string) {
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = filename
  document.body.appendChild(anchor)
  anchor.click()
  anchor.remove()
}

export default function BrowserCallPage() {
  const { token } = useAuth()
  const [label, setLabel] = useState('sandbox')
  const [agents, setAgents] = useState<AgentListItem[]>([])
  const [selectedAgentId, setSelectedAgentId] = useState('')
  const [session, setSession] = useState<BrowserCallStartResponse | null>(null)
  const [status, setStatus] = useState<BrowserCallRead | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [starting, setStarting] = useState(false)
  const [stopping, setStopping] = useState(false)
  const [micState, setMicState] = useState<'idle' | 'live'>('idle')
  const [aiState, setAiState] = useState<'silent' | 'speaking'>('silent')
  const [localAudioDebug, setLocalAudioDebug] = useState<LocalAudioDebug>(INITIAL_LOCAL_AUDIO_DEBUG)
  const [loopbackDebug, setLoopbackDebug] = useState<LoopbackDebug>(INITIAL_LOOPBACK_DEBUG)
  const [audioHealthError, setAudioHealthError] = useState<'NO_AUDIO_IN' | 'NO_AUDIO_OUT' | null>(null)
  const [testToneRunning, setTestToneRunning] = useState(false)
  const [ttsTestRunning, setTtsTestRunning] = useState(false)
  const [hardcodedPlaybackRunning, setHardcodedPlaybackRunning] = useState(false)
  const [outputDevices, setOutputDevices] = useState<OutputDeviceOption[]>([{ deviceId: 'default', label: 'Системное устройство по умолчанию' }])
  const [selectedOutputDeviceId, setSelectedOutputDeviceId] = useState('default')
  const [lastAudioDownloadUrl, setLastAudioDownloadUrl] = useState<string | null>(null)
  const [lastAudioWavSize, setLastAudioWavSize] = useState(0)
  const [lastAudioValid, setLastAudioValid] = useState(false)
  const [waveformVisible, setWaveformVisible] = useState(false)
  const [waveformVersion, setWaveformVersion] = useState(0)
  const websocketRef = useRef<WebSocket | null>(null)
  const pollTimerRef = useRef<number | null>(null)
  const statusUrlRef = useRef<string | null>(null)
  const stopUrlRef = useRef<string | null>(null)
  const closingRef = useRef(false)
  const playbackCursorRef = useRef(0)
  const lastAssistantAudioAtRef = useRef(0)
  const audioRuntimeRef = useRef<AudioRuntime | null>(null)
  const loopbackRuntimeRef = useRef<AudioRuntime | null>(null)
  const teardownOnUnmountRef = useRef<((callStop: boolean) => Promise<void>) | null>(null)
  const stopLoopbackOnUnmountRef = useRef<(() => Promise<void>) | null>(null)
  const audioHealthTimeoutsRef = useRef<number[]>([])
  const localAudioDebugRef = useRef<LocalAudioDebug>(INITIAL_LOCAL_AUDIO_DEBUG)
  const inboundPcmChunksRef = useRef<ArrayBuffer[]>([])
  const lastAudioUrlRef = useRef<string | null>(null)
  const waveformCanvasRef = useRef<HTMLCanvasElement | null>(null)
  const latestWaveformRef = useRef<Float32Array<ArrayBufferLike> | null>(null)
  const pendingPlaybackChunksRef = useRef<ArrayBuffer[]>([])
  const pendingPlaybackBytesRef = useRef(0)
  const playbackFlushTimerRef = useRef<number | null>(null)
  const playbackSilenceTimerRef = useRef<number | null>(null)

  const isActive = Boolean(session)
  const selectedAgentName = useMemo(() => {
    const targetId = status?.agent_profile_id || session?.agent_profile_id || selectedAgentId
    if (!targetId) {
      return 'Промпт по умолчанию'
    }
    const matched = agents.find((agent) => agent.id === targetId)
    return matched ? matched.name : targetId
  }, [agents, selectedAgentId, session?.agent_profile_id, status?.agent_profile_id])

  const logBrowserEvent = useCallback((
    event: string,
    details: Record<string, unknown> = {},
    level: 'info' | 'warn' | 'error' = 'info',
  ) => {
    const payload = {
      call_id: session?.call_id || status?.call_id || null,
      session_id: status?.debug.session_id || session?.session_id || null,
      agent_id: status?.agent_profile_id || session?.agent_profile_id || selectedAgentId || null,
      voice_strategy: status?.debug.voice_strategy || session?.voice_strategy || null,
      active_voice_path: status?.debug.active_voice_path || session?.active_voice_path || null,
      ...details,
    }
    const logger = level === 'error'
      ? console.error
      : level === 'warn'
        ? console.warn
        : console.info
    logger(`[browser-call] ${event}`, payload)
  }, [
    selectedAgentId,
    session?.active_voice_path,
    session?.agent_profile_id,
    session?.call_id,
    session?.session_id,
    session?.voice_strategy,
    status?.agent_profile_id,
    status?.call_id,
    status?.debug.active_voice_path,
    status?.debug.session_id,
    status?.debug.voice_strategy,
  ])

  const updateLocalAudioDebug = useCallback((patch: Partial<LocalAudioDebug>) => {
    setLocalAudioDebug((previous) => ({ ...previous, ...patch }))
  }, [])

  const bumpLocalAudioCounter = useCallback((key: 'outboundChunkCount' | 'inboundAudioChunkCount' | 'playbackStarts') => {
    setLocalAudioDebug((previous) => ({ ...previous, [key]: previous[key] + 1 }))
  }, [])

  const updateLoopbackDebug = useCallback((patch: Partial<LoopbackDebug>) => {
    setLoopbackDebug((previous) => ({ ...previous, ...patch }))
  }, [])

  const refreshOutputDevices = useCallback(async () => {
    if (!navigator.mediaDevices?.enumerateDevices) {
      setOutputDevices([{ deviceId: 'default', label: 'Системное устройство по умолчанию' }])
      updateLocalAudioDebug({
        currentOutputDeviceId: 'system-default',
        outputSelectionSupported: false,
        outputDeviceError: 'Браузер не поддерживает перечисление аудиовыходов.',
      })
      return
    }
    try {
      const devices = await navigator.mediaDevices.enumerateDevices()
      const outputs = devices
        .filter((device) => device.kind === 'audiooutput')
        .map((device, index) => ({
          deviceId: device.deviceId || 'default',
          label: device.label || `Audio output ${index + 1}`,
        }))
      const nextOutputs = outputs.length > 0 ? outputs : [{ deviceId: 'default', label: 'Системное устройство по умолчанию' }]
      setOutputDevices(nextOutputs)
      setSelectedOutputDeviceId((previous) => {
        if (nextOutputs.some((device) => device.deviceId === previous)) {
          return previous
        }
        return nextOutputs[0]?.deviceId || 'default'
      })
      updateLocalAudioDebug((typeof HTMLMediaElement !== 'undefined' && 'setSinkId' in HTMLMediaElement.prototype)
        ? {
            currentOutputDeviceId: selectedOutputDeviceId || nextOutputs[0]?.deviceId || 'default',
            outputSelectionSupported: true,
            outputDeviceError: null,
          }
        : {
            currentOutputDeviceId: 'system-default',
            outputSelectionSupported: false,
            outputDeviceError: 'Прямое воспроизведение Web Audio использует устройство вывода по умолчанию в этом браузере.',
          })
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Не удалось получить список аудиовыходов.'
      setOutputDevices([{ deviceId: 'default', label: 'Системное устройство по умолчанию' }])
      updateLocalAudioDebug({
        currentOutputDeviceId: 'system-default',
        outputSelectionSupported: false,
        outputDeviceError: message,
      })
      logBrowserEvent('audio_output_devices_failed', { error: message }, 'warn')
    }
  }, [logBrowserEvent, selectedOutputDeviceId, updateLocalAudioDebug])

  const applyOutputDeviceSelection = useCallback(async (runtime: AudioRuntime | null, deviceId: string) => {
    const element = runtime?.playbackElement as (HTMLAudioElement & {
      setSinkId?: (sinkId: string) => Promise<void>
    }) | null | undefined

    if (!element || typeof element.setSinkId !== 'function') {
      updateLocalAudioDebug({
        currentOutputDeviceId: 'system-default',
        outputSelectionSupported: false,
        outputDeviceError: 'Выбор аудиовыхода не поддерживается для текущего пути воспроизведения.',
      })
      return
    }

    const targetDeviceId = deviceId || 'default'
    try {
      await element.setSinkId(targetDeviceId)
      updateLocalAudioDebug({
        currentOutputDeviceId: targetDeviceId,
        outputSelectionSupported: true,
        outputDeviceError: null,
      })
      logBrowserEvent('audio_output_device_selected', { output_device_id: targetDeviceId })
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Не удалось переключить аудиовыход.'
      updateLocalAudioDebug({
        currentOutputDeviceId: 'system-default',
        outputSelectionSupported: true,
        outputDeviceError: message,
      })
      logBrowserEvent('audio_output_device_failed', { output_device_id: targetDeviceId, error: message }, 'warn')
    }
  }, [logBrowserEvent, updateLocalAudioDebug])

  useEffect(() => {
    localAudioDebugRef.current = localAudioDebug
  }, [localAudioDebug])

  useEffect(() => {
    const canvas = waveformCanvasRef.current
    const waveform = latestWaveformRef.current
    if (!canvas || !waveform || waveform.length === 0) {
      return
    }
    const context = canvas.getContext('2d')
    if (!context) {
      return
    }
    const width = canvas.width
    const height = canvas.height
    context.clearRect(0, 0, width, height)
    context.fillStyle = '#f5f1e8'
    context.fillRect(0, 0, width, height)
    context.strokeStyle = '#1d5c4d'
    context.lineWidth = 2
    context.beginPath()
    for (let x = 0; x < width; x += 1) {
      const sampleIndex = Math.min(
        waveform.length - 1,
        Math.floor((x / Math.max(1, width - 1)) * waveform.length),
      )
      const sample = waveform[sampleIndex] ?? 0
      const y = height / 2 - sample * (height * 0.42)
      if (x === 0) {
        context.moveTo(x, y)
      } else {
        context.lineTo(x, y)
      }
    }
    context.stroke()
    setWaveformVisible(true)
  }, [waveformVersion])

  useEffect(() => {
    return () => {
      if (lastAudioUrlRef.current) {
        URL.revokeObjectURL(lastAudioUrlRef.current)
        lastAudioUrlRef.current = null
      }
    }
  }, [])

  useEffect(() => {
    void refreshOutputDevices()
    const mediaDevices = navigator.mediaDevices
    if (!mediaDevices?.addEventListener) {
      return
    }
    const handleDeviceChange = () => {
      void refreshOutputDevices()
    }
    mediaDevices.addEventListener('devicechange', handleDeviceChange)
    return () => {
      mediaDevices.removeEventListener('devicechange', handleDeviceChange)
    }
  }, [refreshOutputDevices])

  useEffect(() => {
    if (!token) {
      return
    }
    let mounted = true
    apiFetch<AgentListResponse>('/v1/agents?active_only=true', {}, token)
      .then((response) => {
        if (mounted) {
          setAgents(response.items)
        }
      })
      .catch(() => {
        if (mounted) {
          setAgents([])
        }
      })
    return () => {
      mounted = false
    }
  }, [token])

  useEffect(() => {
    const runtime = audioRuntimeRef.current
    if (!runtime) {
      return
    }
    void applyOutputDeviceSelection(runtime, selectedOutputDeviceId)
  }, [applyOutputDeviceSelection, selectedOutputDeviceId])

  const ensureAudioContextRunning = useCallback(async (context: AudioContext) => {
    if (context.state === 'running') {
      updateLocalAudioDebug({ audioContextState: context.state })
      return
    }
    await context.resume()
    updateLocalAudioDebug({ audioContextState: context.state })
    logBrowserEvent('audio_context_resumed', { audio_context_state: context.state })
  }, [logBrowserEvent, updateLocalAudioDebug])

  const recordInboundAudioArtifact = useCallback((arrayBuffer: ArrayBuffer) => {
    inboundPcmChunksRef.current.push(arrayBuffer.slice(0))
    const wavBlob = pcm16ToWav(
      inboundPcmChunksRef.current,
      TARGET_PCM_SAMPLE_RATE,
      TARGET_PCM_CHANNELS,
      TARGET_PCM_BIT_DEPTH,
    )
    if (lastAudioUrlRef.current) {
      URL.revokeObjectURL(lastAudioUrlRef.current)
    }
    const objectUrl = URL.createObjectURL(wavBlob)
    lastAudioUrlRef.current = objectUrl
    setLastAudioDownloadUrl(objectUrl)
    setLastAudioWavSize(wavBlob.size)
    setLastAudioValid(wavBlob.size > 44)
  }, [])

  const playPcm16Buffer = useCallback(async (
    arrayBuffer: ArrayBuffer,
    options?: {
      source?: 'browser_inbound' | 'hardcoded_local'
      inputSampleRate?: number
      preserveArtifact?: boolean
    },
  ) => {
    const inputSampleRate = options?.inputSampleRate ?? TARGET_PCM_SAMPLE_RATE
    const source = options?.source ?? 'browser_inbound'
    const runtime = audioRuntimeRef.current
    if (!runtime) {
      updateLocalAudioDebug({ lastPlaybackError: 'Аудио runtime недоступен.' })
      logBrowserEvent('playback_skipped_runtime_missing', {}, 'warn')
      return
    }
    try {
      await ensureAudioContextRunning(runtime.context)
      if (options?.preserveArtifact !== false) {
        recordInboundAudioArtifact(arrayBuffer)
      }
      const pcmFloat = int16ToFloat32(arrayBuffer)
      latestWaveformRef.current = pcmFloat
      setWaveformVersion((previous) => previous + 1)
      const playbackSampleRate = runtime.context.sampleRate
      const formatMismatch = playbackSampleRate !== inputSampleRate
      const playbackFloat = formatMismatch
        ? resampleFloat32Linear(pcmFloat, inputSampleRate, playbackSampleRate)
        : pcmFloat
      const { rms, peak } = computeAudioStats(playbackFloat)
      const audioTooQuiet = rms < AUDIO_TOO_QUIET_RMS_THRESHOLD
      const buffer = runtime.context.createBuffer(1, playbackFloat.length, playbackSampleRate)
      buffer.copyToChannel(new Float32Array(playbackFloat), 0)
      const bufferSource = runtime.context.createBufferSource()
      const playbackGain = runtime.playbackGain || runtime.context.createGain()
      playbackGain.gain.value = 1.0
      runtime.playbackGain = playbackGain
      bufferSource.buffer = buffer
      bufferSource.connect(playbackGain)
      updateLocalAudioDebug({
        playbackNodesCreated: localAudioDebugRef.current.playbackNodesCreated + 1,
        playbackGainValue: playbackGain.gain.value,
        playbackSampleRate,
        playbackChannels: buffer.numberOfChannels,
        playbackBitDepth: 16,
        playbackBufferLength: buffer.length,
        playbackFormatMismatch: formatMismatch,
        lastRms: Number(rms.toFixed(4)),
        lastPeak: Number(peak.toFixed(4)),
        audioTooQuiet,
      })
      logBrowserEvent('playback_node_created', {
        source,
        expected_sample_rate: inputSampleRate,
        actual_sample_rate: playbackSampleRate,
        channels: buffer.numberOfChannels,
        bit_depth: 16,
        buffer_length: buffer.length,
        gain: playbackGain.gain.value,
        format_mismatch: formatMismatch,
        rms: Number(rms.toFixed(4)),
        max_amplitude: Number(peak.toFixed(4)),
        duration_ms: Math.round(buffer.duration * 1000),
      })
      if (audioTooQuiet) {
        logBrowserEvent('AUDIO_TOO_QUIET', {
          source,
          rms: Number(rms.toFixed(4)),
          max_amplitude: Number(peak.toFixed(4)),
          threshold: AUDIO_TOO_QUIET_RMS_THRESHOLD,
        }, 'warn')
      }
      if (runtime.playbackElement) {
        await runtime.playbackElement.play().catch((err) => {
          throw err instanceof Error ? err : new Error('Не удалось запустить элемент воспроизведения.')
        })
      }
      playbackCursorRef.current = Math.max(playbackCursorRef.current, runtime.context.currentTime + 0.02)
      bufferSource.start(playbackCursorRef.current)
      playbackCursorRef.current += buffer.duration
      lastAssistantAudioAtRef.current = Date.now()
      bumpLocalAudioCounter('playbackStarts')
      bufferSource.onended = () => {
        setLocalAudioDebug((previous) => ({
          ...previous,
          playbackEndedCount: previous.playbackEndedCount + 1,
          // Playback ended normally — clear any stale diagnostic
          playbackDiagnostic: previous.lastPlaybackError ? previous.playbackDiagnostic : null,
        }))
        logBrowserEvent('playback_ended', {
          source,
          buffer_duration_ms: Math.round(buffer.duration * 1000),
        })
      }
      updateLocalAudioDebug({
        lastPlaybackError: null,
        audioContextState: runtime.context.state,
        // Only flag as likely failure if audio is playing but silent (RMS too low)
        playbackDiagnostic: audioTooQuiet ? 'PLAYBACK_FAILURE_LIKELY' : null,
      })
      setAiState('speaking')
      logBrowserEvent('playback_started', {
        source,
        pcm_bytes: arrayBuffer.byteLength,
        playback_cursor_s: playbackCursorRef.current,
        gain: playbackGain.gain.value,
        rms: Number(rms.toFixed(4)),
        max_amplitude: Number(peak.toFixed(4)),
      })
      if (playbackSilenceTimerRef.current) {
        window.clearTimeout(playbackSilenceTimerRef.current)
      }
      playbackSilenceTimerRef.current = window.setTimeout(() => {
        if (Date.now() - lastAssistantAudioAtRef.current > 500) {
          setAiState('silent')
        }
      }, 700)
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Ошибка воспроизведения.'
      updateLocalAudioDebug({ lastPlaybackError: message })
      setError((previous) => previous || message)
      logBrowserEvent('playback_error', { error: message }, 'error')
    }
  }, [bumpLocalAudioCounter, ensureAudioContextRunning, logBrowserEvent, recordInboundAudioArtifact, updateLocalAudioDebug])

  const flushPlaybackQueue = useCallback(async (reason: 'threshold' | 'timer' | 'force') => {
    if (playbackFlushTimerRef.current) {
      window.clearTimeout(playbackFlushTimerRef.current)
      playbackFlushTimerRef.current = null
    }
    if (pendingPlaybackBytesRef.current === 0 || pendingPlaybackChunksRef.current.length === 0) {
      return
    }
    const mergedBuffer = concatArrayBuffers(pendingPlaybackChunksRef.current).buffer
    const mergedBytes = pendingPlaybackBytesRef.current
    pendingPlaybackChunksRef.current = []
    pendingPlaybackBytesRef.current = 0
    logBrowserEvent('playback_buffer_flushed', {
      reason,
      merged_bytes: mergedBytes,
      merged_samples: Math.floor(mergedBytes / 2),
      minimum_samples: MIN_PLAYBACK_BUFFER_SAMPLES,
    })
    await playPcm16Buffer(mergedBuffer, { source: 'browser_inbound' })
  }, [logBrowserEvent, playPcm16Buffer])

  const enqueuePlaybackChunk = useCallback((arrayBuffer: ArrayBuffer) => {
    pendingPlaybackChunksRef.current.push(arrayBuffer.slice(0))
    pendingPlaybackBytesRef.current += arrayBuffer.byteLength
    if (pendingPlaybackBytesRef.current >= MIN_PLAYBACK_BUFFER_BYTES) {
      void flushPlaybackQueue('threshold')
      return
    }
    if (playbackFlushTimerRef.current) {
      return
    }
    playbackFlushTimerRef.current = window.setTimeout(() => {
      void flushPlaybackQueue('timer')
    }, PLAYBACK_FLUSH_DELAY_MS)
  }, [flushPlaybackQueue])

  const playHardcodedAudio = useCallback(async () => {
    setHardcodedPlaybackRunning(true)
    try {
      const runtime = audioRuntimeRef.current
      const beep = generateBeepPcm16({
        durationMs: 1000,
        amplitude: 0.72,
      })
      logBrowserEvent('hardcoded_playback_scheduled', {
        pcm_bytes: beep.byteLength,
        delay_ms: 200,
        local_only: !runtime,
      })
      window.setTimeout(() => {
        if (runtime) {
          void playPcm16Buffer(beep, {
            source: 'hardcoded_local',
            inputSampleRate: TARGET_PCM_SAMPLE_RATE,
            preserveArtifact: false,
          })
          setHardcodedPlaybackRunning(false)
          return
        }
        const AudioContextCtor = window.AudioContext
          || (window as typeof window & { webkitAudioContext?: typeof AudioContext }).webkitAudioContext
        if (!AudioContextCtor) {
          setError('AudioContext браузера недоступен.')
          setHardcodedPlaybackRunning(false)
          return
        }
        const context = new AudioContextCtor()
        void (async () => {
          try {
            await context.resume()
            const pcmFloat = int16ToFloat32(beep)
            const playbackFloat = context.sampleRate !== TARGET_PCM_SAMPLE_RATE
              ? resampleFloat32Linear(pcmFloat, TARGET_PCM_SAMPLE_RATE, context.sampleRate)
              : pcmFloat
            const { rms, peak } = computeAudioStats(playbackFloat)
            const audioTooQuiet = rms < AUDIO_TOO_QUIET_RMS_THRESHOLD
            const buffer = context.createBuffer(1, playbackFloat.length, context.sampleRate)
            buffer.copyToChannel(new Float32Array(playbackFloat), 0)
            const gain = context.createGain()
            gain.gain.value = 1.0
            const source = context.createBufferSource()
            source.buffer = buffer
            source.connect(gain)
            gain.connect(context.destination)
            setLocalAudioDebug((previous) => ({
              ...previous,
              playbackNodesCreated: previous.playbackNodesCreated + 1,
              playbackGainValue: gain.gain.value,
              playbackSampleRate: context.sampleRate,
              playbackChannels: buffer.numberOfChannels,
              playbackBitDepth: 16,
              playbackBufferLength: buffer.length,
              playbackFormatMismatch: context.sampleRate !== TARGET_PCM_SAMPLE_RATE,
              lastRms: Number(rms.toFixed(4)),
              lastPeak: Number(peak.toFixed(4)),
              audioTooQuiet,
            }))
            logBrowserEvent('playback_node_created', {
              source: 'hardcoded_local',
              expected_sample_rate: TARGET_PCM_SAMPLE_RATE,
              actual_sample_rate: context.sampleRate,
              channels: buffer.numberOfChannels,
              bit_depth: 16,
              buffer_length: buffer.length,
              gain: gain.gain.value,
              format_mismatch: context.sampleRate !== TARGET_PCM_SAMPLE_RATE,
              rms: Number(rms.toFixed(4)),
              max_amplitude: Number(peak.toFixed(4)),
              duration_ms: Math.round(buffer.duration * 1000),
            })
            if (audioTooQuiet) {
              logBrowserEvent('AUDIO_TOO_QUIET', {
                source: 'hardcoded_local',
                rms: Number(rms.toFixed(4)),
                max_amplitude: Number(peak.toFixed(4)),
                threshold: AUDIO_TOO_QUIET_RMS_THRESHOLD,
              }, 'warn')
            }
            source.onended = () => {
              setLocalAudioDebug((previous) => ({
                ...previous,
                playbackEndedCount: previous.playbackEndedCount + 1,
                playbackDiagnostic: audioTooQuiet ? 'PLAYBACK_FAILURE_LIKELY' : null,
              }))
              logBrowserEvent('playback_ended', { source: 'hardcoded_local' })
              void context.close()
            }
            source.start()
            setLocalAudioDebug((previous) => ({
              ...previous,
              playbackStarts: previous.playbackStarts + 1,
              playbackDiagnostic: audioTooQuiet ? 'PLAYBACK_FAILURE_LIKELY' : null,
              lastPlaybackError: null,
            }))
            logBrowserEvent('playback_started', {
              source: 'hardcoded_local',
              pcm_bytes: beep.byteLength,
              rms: Number(rms.toFixed(4)),
              max_amplitude: Number(peak.toFixed(4)),
            })
          } catch (err) {
            const message = err instanceof Error ? err.message : 'Ошибка локального воспроизведения.'
            setError(message)
            logBrowserEvent('hardcoded_playback_failed', { error: message }, 'error')
          } finally {
            setHardcodedPlaybackRunning(false)
          }
        })()
      }, 200)
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Ошибка локального воспроизведения.'
      setError(message)
      setHardcodedPlaybackRunning(false)
      logBrowserEvent('hardcoded_playback_failed', { error: message }, 'error')
    }
  }, [logBrowserEvent, playPcm16Buffer])

  const stopLoopback = useCallback(async () => {
    const runtime = loopbackRuntimeRef.current
    if (!runtime) {
      setLoopbackDebug(INITIAL_LOOPBACK_DEBUG)
      return
    }
    runtime.processor.disconnect()
    runtime.processor.onaudioprocess = null
    runtime.source.disconnect()
    runtime.sink?.disconnect()
    runtime.playbackGain?.disconnect()
    runtime.stream.getTracks().forEach((track) => track.stop())
    await runtime.context.close()
    loopbackRuntimeRef.current = null
    setLoopbackDebug((previous) => ({
      ...previous,
      active: false,
      starting: false,
    }))
    logBrowserEvent('loopback_stopped')
  }, [logBrowserEvent])

  const stopAudioInput = useCallback(async () => {
    const runtime = audioRuntimeRef.current
    if (playbackFlushTimerRef.current) {
      window.clearTimeout(playbackFlushTimerRef.current)
      playbackFlushTimerRef.current = null
    }
    if (playbackSilenceTimerRef.current) {
      window.clearTimeout(playbackSilenceTimerRef.current)
      playbackSilenceTimerRef.current = null
    }
    pendingPlaybackChunksRef.current = []
    pendingPlaybackBytesRef.current = 0
    if (!runtime) {
      setMicState('idle')
      updateLocalAudioDebug({
        audioContextState: 'idle',
        websocketConnected: false,
      })
      return
    }
    runtime.processor.disconnect()
    runtime.processor.onaudioprocess = null
    runtime.source.disconnect()
    runtime.sink?.disconnect()
    runtime.playbackGain?.disconnect()
    runtime.playbackDestination?.disconnect()
    if (runtime.playbackElement) {
      runtime.playbackElement.pause()
      runtime.playbackElement.srcObject = null
    }
    runtime.stream.getTracks().forEach((track) => track.stop())
    await runtime.context.close()
    audioRuntimeRef.current = null
    playbackCursorRef.current = 0
    setMicState('idle')
    setAiState('silent')
    inboundPcmChunksRef.current = []
    latestWaveformRef.current = null
    setWaveformVisible(false)
    if (lastAudioUrlRef.current) {
      URL.revokeObjectURL(lastAudioUrlRef.current)
      lastAudioUrlRef.current = null
    }
    setLastAudioDownloadUrl(null)
    setLastAudioWavSize(0)
    setLastAudioValid(false)
    updateLocalAudioDebug({
      audioContextState: 'closed',
      websocketConnected: false,
      playbackDiagnostic: null,
    })
    setAudioHealthError(null)
    logBrowserEvent('audio_runtime_stopped')
  }, [logBrowserEvent, updateLocalAudioDebug])

  const fetchStatus = useCallback(async (statusUrl?: string) => {
    const resolvedStatusUrl = statusUrl || statusUrlRef.current
    if (!resolvedStatusUrl || !token) {
      return
    }
    const data = await apiFetch<BrowserCallRead>(normalizeApiPath(resolvedStatusUrl), {}, token)
    setStatus(data)
    if (['FAILED', 'STOPPED', 'COMPLETED'].includes(data.status)) {
      await stopAudioInput()
      if (pollTimerRef.current) {
        window.clearInterval(pollTimerRef.current)
        pollTimerRef.current = null
      }
      websocketRef.current?.close()
      websocketRef.current = null
      statusUrlRef.current = null
      stopUrlRef.current = null
      setSession(null)
    }
  }, [stopAudioInput, token])

  const teardownConnection = useCallback(async (callStop: boolean) => {
    if (audioHealthTimeoutsRef.current.length) {
      audioHealthTimeoutsRef.current.forEach((timerId) => window.clearTimeout(timerId))
      audioHealthTimeoutsRef.current = []
    }
    if (playbackSilenceTimerRef.current) {
      window.clearTimeout(playbackSilenceTimerRef.current)
      playbackSilenceTimerRef.current = null
    }
    if (pollTimerRef.current) {
      window.clearInterval(pollTimerRef.current)
      pollTimerRef.current = null
    }
    const socket = websocketRef.current
    websocketRef.current = null
    if (socket && (socket.readyState === WebSocket.OPEN || socket.readyState === WebSocket.CONNECTING)) {
      closingRef.current = true
      socket.close()
    }
    if (callStop && stopUrlRef.current && token) {
      await apiFetch<BrowserCallRead>(normalizeApiPath(stopUrlRef.current), { method: 'POST' }, token)
    }
    await stopAudioInput()
  }, [stopAudioInput, token])

  useEffect(() => {
    teardownOnUnmountRef.current = teardownConnection
  }, [teardownConnection])

  useEffect(() => {
    stopLoopbackOnUnmountRef.current = stopLoopback
  }, [stopLoopback])

  const prepareAudioRuntime = useCallback(async () => {
    if (audioRuntimeRef.current) {
      return audioRuntimeRef.current
    }
    const AudioContextCtor = window.AudioContext
      || (window as typeof window & { webkitAudioContext?: typeof AudioContext }).webkitAudioContext
    if (!AudioContextCtor) {
      throw new Error('AudioContext браузера недоступен.')
    }
    if (!navigator.mediaDevices?.getUserMedia) {
      throw new Error('Захват микрофона в браузере недоступен.')
    }

    updateLocalAudioDebug({
      micPermission: 'requested',
      lastPlaybackError: null,
      lastTransportError: null,
    })
    logBrowserEvent('mic_permission_requested')
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true })
    const track = stream.getAudioTracks()[0]
    if (!track) {
      stream.getTracks().forEach((candidate) => candidate.stop())
      throw new Error('Трек микрофона браузера недоступен.')
    }

    const context = new AudioContextCtor()
    await ensureAudioContextRunning(context)
    const source = context.createMediaStreamSource(stream)
    const processor = context.createScriptProcessor(4096, 1, 1)
    const sink = typeof context.createGain === 'function' ? context.createGain() : null
    if (sink) {
      sink.gain.value = 0
      processor.connect(sink)
      sink.connect(context.destination)
    } else {
      processor.connect(context.destination)
    }
    processor.onaudioprocess = (event) => {
      const socket = websocketRef.current
      if (!socket || socket.readyState !== WebSocket.OPEN) {
        return
      }
      const input = event.inputBuffer.getChannelData(0)
      const downsampled = downsampleBuffer(input, context.sampleRate, TARGET_PCM_SAMPLE_RATE)
      const pcm16 = floatTo16BitPCM(downsampled)
      socket.send(pcm16.buffer)
      bumpLocalAudioCounter('outboundChunkCount')
    }
    source.connect(processor)

    await refreshOutputDevices()

    const playbackGain = typeof context.createGain === 'function' ? context.createGain() : null
    let playbackDestination: MediaStreamAudioDestinationNode | null = null
    let playbackElement: HTMLAudioElement | null = null

    if (playbackGain) {
      playbackGain.gain.value = 1.0
      if (typeof context.createMediaStreamDestination === 'function') {
        playbackDestination = context.createMediaStreamDestination()
        playbackGain.connect(playbackDestination)
        playbackElement = document.createElement('audio')
        playbackElement.autoplay = true
        playbackElement.setAttribute('playsinline', 'true')
        playbackElement.srcObject = playbackDestination.stream
        try {
          await applyOutputDeviceSelection(
            { context, stream, source, processor, sink, playbackGain, playbackDestination, playbackElement },
            selectedOutputDeviceId,
          )
          await playbackElement.play()
        } catch (err) {
          const message = err instanceof Error ? err.message : 'Не удалось запустить элемент воспроизведения браузера.'
          playbackGain.disconnect()
          playbackGain.connect(context.destination)
          playbackDestination = null
          if (playbackElement) {
            playbackElement.srcObject = null
          }
          playbackElement = null
          updateLocalAudioDebug({
            currentOutputDeviceId: 'system-default',
            outputSelectionSupported: false,
            outputDeviceError: message,
          })
          logBrowserEvent('playback_element_failed', { error: message }, 'warn')
        }
      } else {
        playbackGain.connect(context.destination)
        updateLocalAudioDebug({
          currentOutputDeviceId: 'system-default',
          outputSelectionSupported: false,
          outputDeviceError: 'MediaStreamDestination недоступен; воспроизведение использует устройство браузера по умолчанию.',
        })
      }
    }

    audioRuntimeRef.current = {
      context,
      stream,
      source,
      processor,
      sink,
      playbackGain,
      playbackDestination,
      playbackElement,
    }
    setMicState('live')
    const trackSettings = typeof track.getSettings === 'function' ? track.getSettings() : {}
    updateLocalAudioDebug({
      micPermission: 'granted',
      audioContextState: context.state,
      inputSampleRate: context.sampleRate,
      inputChannelMode: `mono -> pcm16/${TARGET_PCM_SAMPLE_RATE}`,
      estimatedOutputLatencyMs: Math.round(
        ((((context as AudioContext & { baseLatency?: number }).baseLatency || 0)
          + ((context as AudioContext & { outputLatency?: number }).outputLatency || 0)) * 1000),
      ),
    })
    logBrowserEvent('mic_stream_started', {
      track_enabled: track.enabled,
      track_muted: track.muted,
      track_ready_state: track.readyState,
      sample_rate: context.sampleRate,
      channel_count: trackSettings.channelCount ?? null,
    })
    return audioRuntimeRef.current
  }, [
    applyOutputDeviceSelection,
    bumpLocalAudioCounter,
    ensureAudioContextRunning,
    logBrowserEvent,
    refreshOutputDevices,
    selectedOutputDeviceId,
    updateLocalAudioDebug,
  ])

  const startLoopback = useCallback(async () => {
    if (loopbackRuntimeRef.current || loopbackDebug.starting) {
      return
    }
    setError(null)
    setLoopbackDebug(INITIAL_LOOPBACK_DEBUG)
    updateLoopbackDebug({ starting: true, lastError: null })
    try {
      const AudioContextCtor = window.AudioContext
        || (window as typeof window & { webkitAudioContext?: typeof AudioContext }).webkitAudioContext
      if (!AudioContextCtor) {
        throw new Error('AudioContext браузера недоступен.')
      }
      if (!navigator.mediaDevices?.getUserMedia) {
        throw new Error('Захват микрофона в браузере недоступен.')
      }
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true })
      const track = stream.getAudioTracks()[0]
      if (!track) {
        stream.getTracks().forEach((candidate) => candidate.stop())
        throw new Error('Трек микрофона браузера недоступен.')
      }

      const context = new AudioContextCtor()
      await ensureAudioContextRunning(context)
      const source = context.createMediaStreamSource(stream)
      const processor = context.createScriptProcessor(4096, 1, 1)
      const sink = typeof context.createGain === 'function' ? context.createGain() : null
      if (sink) {
        sink.gain.value = 1
        processor.connect(sink)
        sink.connect(context.destination)
      } else {
        processor.connect(context.destination)
      }
      const estimatedLatencyMs = Math.round(
        ((((context as AudioContext & { baseLatency?: number }).baseLatency || 0)
          + ((context as AudioContext & { outputLatency?: number }).outputLatency || 0)) * 1000),
      )
      processor.onaudioprocess = () => {
        setLoopbackDebug((previous) => ({
          ...previous,
          micChunks: previous.micChunks + 1,
          playbackChunks: previous.playbackChunks + 1,
        }))
      }
      source.connect(processor)
      loopbackRuntimeRef.current = { context, stream, source, processor, sink }
      updateLoopbackDebug({
        active: true,
        starting: false,
        estimatedLatencyMs,
        lastError: null,
      })
      logBrowserEvent('loopback_started', {
        sample_rate: context.sampleRate,
        estimated_latency_ms: estimatedLatencyMs,
      })
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Ошибка запуска loopback.'
      updateLoopbackDebug({
        active: false,
        starting: false,
        lastError: message,
      })
      logBrowserEvent('loopback_failed', { error: message }, 'error')
    }
  }, [ensureAudioContextRunning, logBrowserEvent, loopbackDebug.starting, updateLoopbackDebug])

  const waitForWebSocketOpen = useCallback((socket: WebSocket) => new Promise<void>((resolve, reject) => {
    if (socket.readyState === WebSocket.OPEN) {
      updateLocalAudioDebug({ websocketConnected: true, lastTransportError: null })
      resolve()
      return
    }
    const timeout = window.setTimeout(() => {
      reject(new Error('WebSocket браузерного звонка не открылся вовремя.'))
    }, 5000)
    const previousOpen = socket.onopen
    const previousError = socket.onerror
    socket.onopen = (event) => {
      previousOpen?.call(socket, event)
      window.clearTimeout(timeout)
      resolve()
    }
    socket.onerror = (event) => {
      previousError?.call(socket, event)
      window.clearTimeout(timeout)
      reject(new Error('WebSocket браузерного звонка не смог подключиться.'))
    }
  }), [updateLocalAudioDebug])

  const stopSession = useCallback(async () => {
    if (!session || stopping) {
      return
    }
    setStopping(true)
    try {
      await teardownConnection(true)
      await fetchStatus(session.status_url)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Ошибка остановки сессии.')
    } finally {
      setStopping(false)
    }
  }, [fetchStatus, session, stopping, teardownConnection])

  const runBackendDebugAction = useCallback(async (
    action: 'test-tone' | 'test-tts',
    setRunning: (value: boolean) => void,
  ) => {
    if (!token || !session) {
      setError('Браузерная сессия неактивна.')
      return
    }
    setRunning(true)
    setError(null)
    try {
      const response = await apiFetch<BrowserCallDebugActionResponse>(
        `/v1/browser-calls/${session.call_id}/debug/${action}`,
        { method: 'POST' },
        token,
      )
      logBrowserEvent('backend_debug_action_sent', {
        action: response.action,
        chunks_enqueued: response.chunks_enqueued,
      })
    } catch (err) {
      const message = err instanceof Error ? err.message : `Ошибка действия ${action}.`
      setError(message)
      logBrowserEvent('backend_debug_action_failed', { action, error: message }, 'error')
    } finally {
      setRunning(false)
    }
  }, [logBrowserEvent, session, token])

  const startSession = useCallback(async () => {
    if (!token) {
      setError('Токен авторизации администратора отсутствует.')
      return
    }
    setStarting(true)
    setError(null)
    setLocalAudioDebug(INITIAL_LOCAL_AUDIO_DEBUG)
    setLastAudioDownloadUrl(null)
    setLastAudioWavSize(0)
    setLastAudioValid(false)
    inboundPcmChunksRef.current = []
    latestWaveformRef.current = null
    setWaveformVisible(false)
    let nextSession: BrowserCallStartResponse | null = null
    try {
      await prepareAudioRuntime()
      nextSession = await apiFetch<BrowserCallStartResponse>('/v1/browser-calls', {
        method: 'POST',
        body: JSON.stringify({
          label,
          agent_profile_id: selectedAgentId || null,
        }),
      }, token)
      closingRef.current = false
      setSession(nextSession)
      const statusUrl = nextSession.status_url
      const stopUrl = nextSession.stop_url
      const websocketUrl = nextSession.websocket_url
      statusUrlRef.current = statusUrl
      stopUrlRef.current = stopUrl
      const socket = new WebSocket(websocketUrl)
      websocketRef.current = socket
      socket.binaryType = 'arraybuffer'
      socket.onopen = () => {
        updateLocalAudioDebug({ websocketConnected: true, lastTransportError: null })
        logBrowserEvent('websocket_connected')
      }
      socket.onmessage = (event) => {
        if (typeof event.data === 'string') {
          logBrowserEvent('websocket_message', { payload: event.data })
          return
        }
        if (event.data instanceof Blob) {
          bumpLocalAudioCounter('inboundAudioChunkCount')
          void event.data.arrayBuffer().then((payload) => enqueuePlaybackChunk(payload))
          return
        }
        bumpLocalAudioCounter('inboundAudioChunkCount')
        enqueuePlaybackChunk(event.data)
      }
      socket.onerror = () => {
        const message = 'Ошибка транспорта WebSocket браузерного звонка.'
        updateLocalAudioDebug({ lastTransportError: message, websocketConnected: false })
        setError((previous) => previous || message)
        logBrowserEvent('websocket_error', { error: message }, 'error')
      }
      socket.onclose = () => {
        updateLocalAudioDebug({ websocketConnected: false })
        if (!closingRef.current) {
          setError((previous) => previous || 'WebSocket браузерного звонка отключился.')
          logBrowserEvent('websocket_disconnected', {}, 'warn')
          void stopAudioInput()
        }
        closingRef.current = false
      }
      await waitForWebSocketOpen(socket)
      await fetchStatus(statusUrl)
      setAudioHealthError(null)
      audioHealthTimeoutsRef.current.forEach((timerId) => window.clearTimeout(timerId))
      audioHealthTimeoutsRef.current = [
        window.setTimeout(() => {
          const current = localAudioDebugRef.current
          setAudioHealthError((previous) => previous || (current.outboundChunkCount > 0 ? previous : 'NO_AUDIO_IN'))
        }, 5000),
        window.setTimeout(() => {
          const current = localAudioDebugRef.current
          setAudioHealthError((previous) => previous || (current.inboundAudioChunkCount > 0 ? previous : 'NO_AUDIO_OUT'))
        }, 5000),
      ]
      pollTimerRef.current = window.setInterval(() => {
        void fetchStatus(statusUrl)
      }, 1000)
    } catch (err) {
      if (err instanceof ApiError) {
        setError(err.message)
      } else if (err instanceof Error) {
        setError(err.message)
      } else {
        setError('Не удалось начать браузерный звонок.')
      }
      if (err instanceof DOMException && err.name === 'NotAllowedError') {
        updateLocalAudioDebug({ micPermission: 'denied' })
      }
      logBrowserEvent('start_failed', {
        error: err instanceof Error ? err.message : 'Не удалось начать браузерный звонок.',
      }, 'error')
      await teardownConnection(Boolean(nextSession))
      setSession(null)
    } finally {
      setStarting(false)
    }
  }, [
    bumpLocalAudioCounter,
    fetchStatus,
    label,
    logBrowserEvent,
    enqueuePlaybackChunk,
    prepareAudioRuntime,
    selectedAgentId,
    stopAudioInput,
    teardownConnection,
    token,
    updateLocalAudioDebug,
    waitForWebSocketOpen,
  ])

  useEffect(() => {
    if (localAudioDebug.outboundChunkCount > 0 && audioHealthError === 'NO_AUDIO_IN') {
      setAudioHealthError(null)
    }
    if (localAudioDebug.inboundAudioChunkCount > 0 && audioHealthError === 'NO_AUDIO_OUT') {
      setAudioHealthError(null)
    }
  }, [audioHealthError, localAudioDebug.inboundAudioChunkCount, localAudioDebug.outboundChunkCount])

  useEffect(() => {
    return () => {
      void stopLoopbackOnUnmountRef.current?.()
      void teardownOnUnmountRef.current?.(false)
    }
  }, [])

  const debugItems = useMemo(() => {
    const debug = status?.debug
    return [
      ['session id', debug?.session_id || session?.session_id || '—'],
      ['selected agent', selectedAgentName],
      ['agent profile id', status?.agent_profile_id || session?.agent_profile_id || selectedAgentId || '—'],
      ['voice strategy', debug?.voice_strategy || session?.voice_strategy || '—'],
      ['active voice path', debug?.active_voice_path || session?.active_voice_path || '—'],
      ['primary voice path', debug?.primary_voice_path || '—'],
      ['fallback voice path', debug?.fallback_voice_path || session?.fallback_voice_path || '—'],
      ['fallback used', debug?.fallback_used ? 'yes' : 'no'],
      ['session status', status?.status || session?.status || 'idle'],
      ['browser websocket', localAudioDebug.websocketConnected ? 'connected' : 'disconnected'],
      ['browser mic permission', localAudioDebug.micPermission],
      ['audio context', localAudioDebug.audioContextState],
      ['input sample rate', localAudioDebug.inputSampleRate ? `${localAudioDebug.inputSampleRate} Hz` : '—'],
      ['target sample rate', `${localAudioDebug.targetSampleRate} Hz`],
      ['channel mode', localAudioDebug.inputChannelMode],
      ['estimated output latency', localAudioDebug.estimatedOutputLatencyMs != null ? `${localAudioDebug.estimatedOutputLatencyMs} ms` : '—'],
      ['browser outbound chunks', String(localAudioDebug.outboundChunkCount)],
      ['browser inbound audio chunks', String(localAudioDebug.inboundAudioChunkCount)],
      ['browser playback starts', String(localAudioDebug.playbackStarts)],
      ['playback nodes created', String(localAudioDebug.playbackNodesCreated)],
      ['playback ended', String(localAudioDebug.playbackEndedCount)],
      ['playback gain', String(localAudioDebug.playbackGainValue)],
      ['playback sample rate', localAudioDebug.playbackSampleRate ? `${localAudioDebug.playbackSampleRate} Hz` : '—'],
      ['playback channels', String(localAudioDebug.playbackChannels)],
      ['playback bit depth', `${localAudioDebug.playbackBitDepth}-bit`],
      ['playback buffer length', String(localAudioDebug.playbackBufferLength)],
      ['playback mismatch', localAudioDebug.playbackFormatMismatch ? 'yes' : 'no'],
      ['playback diagnostic', localAudioDebug.playbackDiagnostic || 'none'],
      ['last RMS', localAudioDebug.lastRms != null ? localAudioDebug.lastRms.toFixed(4) : '—'],
      ['last peak amplitude', localAudioDebug.lastPeak != null ? localAudioDebug.lastPeak.toFixed(4) : '—'],
      ['audio too quiet', localAudioDebug.audioTooQuiet ? 'yes' : 'no'],
      ['output device', localAudioDebug.currentOutputDeviceId || 'system-default'],
      ['output selection', localAudioDebug.outputSelectionSupported ? 'supported' : 'browser default only'],
      ['output device error', localAudioDebug.outputDeviceError || 'none'],
      ['audio health', audioHealthError || 'ok'],
      ['last audio wav size', lastAudioWavSize ? `${lastAudioWavSize} bytes` : '0 bytes'],
      ['last audio valid', lastAudioValid ? 'yes' : 'no'],
      ['waveform visible', waveformVisible ? 'yes' : 'no'],
      ['loopback active', loopbackDebug.active ? 'yes' : 'no'],
      ['loopback mic chunks', String(loopbackDebug.micChunks)],
      ['loopback playback chunks', String(loopbackDebug.playbackChunks)],
      ['loopback latency', loopbackDebug.estimatedLatencyMs != null ? `${loopbackDebug.estimatedLatencyMs} ms` : '—'],
      ['loopback error', loopbackDebug.lastError || 'none'],
      ['model latency', formatMetric(debug?.model_response_latency_ms_last)],
      ['tts latency', formatMetric(debug?.tts_latency_ms_last)],
      ['playback latency', formatMetric(debug?.outbound_playback_latency_ms_last)],
      ['inbound chunks', String(debug?.inbound_chunks_received ?? 0)],
      ['outbound chunks', String(debug?.outbound_chunks_played ?? 0)],
      ['browser playback error', localAudioDebug.lastPlaybackError || 'none'],
      ['browser transport error', localAudioDebug.lastTransportError || 'none'],
      ['last error', debug?.last_error || error || 'none'],
      ['last failure stage', debug?.last_failure_stage || '—'],
    ]
  }, [audioHealthError, error, lastAudioValid, lastAudioWavSize, localAudioDebug, loopbackDebug, selectedAgentId, session, status, waveformVisible])

  return (
    <section className="page-grid browser-call-page">
      <article className="hero-card browser-call-hero">
        <div>
          <p className="eyebrow">Внутреннее тестирование</p>
          <h3>Браузерный звонок</h3>
          <p>Тестовая voice session идёт мимо Mango и PSTN, но использует тот же Direct session runtime, transcript и voice strategy.</p>
        </div>
        <div className="browser-call-controls">
          <label>
            Метка сессии
            <input value={label} onChange={(event) => setLabel(event.target.value)} maxLength={40} />
          </label>
          <label>
            Профиль агента
            <select
              value={selectedAgentId}
              onChange={(event) => setSelectedAgentId(event.target.value)}
              disabled={isActive}
            >
              <option value="">Промпт по умолчанию</option>
              {agents.map((agent) => (
                <option key={agent.id} value={agent.id}>
                  {agent.name}
                </option>
              ))}
            </select>
          </label>
          <label>
            Устройство вывода
            <select
              value={selectedOutputDeviceId}
              onChange={(event) => setSelectedOutputDeviceId(event.target.value)}
            >
              {outputDevices.map((device) => (
                <option key={device.deviceId} value={device.deviceId}>
                  {device.label}
                </option>
              ))}
            </select>
          </label>
          <div className="button-row">
            <button type="button" className="primary-button" onClick={() => void startSession()} disabled={starting || isActive}>
              {starting ? 'Запуск…' : 'Начать тестовый звонок'}
            </button>
            <button type="button" className="danger-button" onClick={() => void stopSession()} disabled={!isActive || stopping}>
              {stopping ? 'Остановка…' : 'Завершить звонок'}
            </button>
          </div>
          <div className="button-row">
            <button
              type="button"
              onClick={() => void startLoopback()}
              disabled={loopbackDebug.active || loopbackDebug.starting || isActive}
            >
              {loopbackDebug.starting ? 'Запуск loopback…' : 'Тест микрофона (loopback)'}
            </button>
            <button
              type="button"
              onClick={() => void stopLoopback()}
              disabled={!loopbackDebug.active}
            >
              Остановить loopback
            </button>
          </div>
          <div className="button-row">
            <button
              type="button"
              onClick={() => void runBackendDebugAction('test-tone', setTestToneRunning)}
              disabled={!isActive || testToneRunning}
            >
              {testToneRunning ? 'Воспроизведение тона…' : 'Тестовый тон с backend'}
            </button>
            <button
              type="button"
              onClick={() => void runBackendDebugAction('test-tts', setTtsTestRunning)}
              disabled={!isActive || ttsTestRunning}
            >
              {ttsTestRunning ? 'Тест TTS…' : 'Тест TTS'}
            </button>
          </div>
          <div className="button-row">
            <button
              type="button"
              onClick={() => void playHardcodedAudio()}
              disabled={hardcodedPlaybackRunning}
            >
              {hardcodedPlaybackRunning ? 'Воспроизведение…' : 'Громкий тестовый тон'}
            </button>
            <button
              type="button"
              onClick={() => {
                if (lastAudioDownloadUrl) {
                  downloadBlob(lastAudioDownloadUrl, 'browser-last-audio.wav')
                }
              }}
              disabled={!lastAudioDownloadUrl}
            >
              Скачать последнее аудио
            </button>
          </div>
          <div className="status-strip">
            <span className={`status-pill${micState === 'live' ? ' live' : ''}`}>Микрофон: {micState}</span>
            <span className={`status-pill${status?.status === 'IN_PROGRESS' ? ' live' : ''}`}>Сессия: {status?.status || 'idle'}</span>
            <span className={`status-pill${aiState === 'speaking' ? ' live' : ''}`}>AI: {aiState}</span>
            <span className={`status-pill${error ? ' error' : ''}`}>Ошибка: {error || 'нет'}</span>
            <span className={`status-pill${audioHealthError ? ' error' : ''}`}>Аудио: {audioHealthError || 'ok'}</span>
            <span className={`status-pill${localAudioDebug.playbackDiagnostic ? ' error' : ''}`}>
              Воспроизведение: {localAudioDebug.playbackDiagnostic || 'ok'}
            </span>
          </div>
        </div>
      </article>

      <article className="panel-card transcript-panel">
        <div className="panel-header">
          <div>
            <p className="eyebrow">Транскрипт</p>
            <h4>Живой разговор</h4>
          </div>
        </div>
        <div className="transcript-list">
          {status?.transcript_entries?.length ? status.transcript_entries.map((entry) => (
            <article key={entry.id} className={`transcript-bubble${entry.role === 'assistant' ? ' assistant' : ''}`}>
              <div className="transcript-meta">{entry.role} · {new Date(entry.created_at).toLocaleTimeString()}</div>
              <div>{entry.text}</div>
            </article>
          )) : (
            <article className="transcript-bubble empty">
              <div className="transcript-meta">Транскрипт пока пуст</div>
              <div>Ждём greeting или ответ от агента.</div>
            </article>
          )}
        </div>
      </article>

      <aside className="panel-card debug-panel">
        <div className="panel-header">
          <div>
            <p className="eyebrow">Отладка</p>
            <h4>Состояние сессии</h4>
          </div>
        </div>
        <div className="waveform-block">
          <p className="eyebrow">Форма волны</p>
          <canvas ref={waveformCanvasRef} width={320} height={96} aria-label="Форма входящего аудио" />
        </div>
        <div className="debug-list">
          {debugItems.map(([labelText, value]) => (
            <div className="debug-row" key={labelText}>
              <span>{labelText}</span>
              <strong>{value}</strong>
            </div>
          ))}
        </div>
      </aside>
    </section>
  )
}
