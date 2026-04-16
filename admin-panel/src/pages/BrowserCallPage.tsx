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
    tts_first_chunk_sent_ms_last?: number | null
    tts_provider_first_non_silent_chunk_ms_last?: number | null
    tts_first_non_silent_chunk_sent_ms_last?: number | null
    tts_first_non_silent_chunk_played_ms_last?: number | null
    tts_last_chunk_received_ms_last?: number | null
    tts_audio_duration_ms_last?: number | null
    tts_provider_leading_silence_ms_last?: number | null
    tts_backend_leading_silence_ms_last?: number | null
    tts_leading_silence_trimmed_ms_last?: number | null
    tts_trailing_silence_trimmed_ms_last?: number | null
    tts_chunks_in_last?: number
    tts_chunks_out_last?: number
    tts_tiny_chunks_in_last?: number
    tts_turn_id_last?: string | null
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
  queuedPlaybackBufferMs: number
  scheduledPlaybackBacklogMs: number
  firstChunkLatencyMs: number | null
  firstNonSilentChunkLatencyMs: number | null
  firstNonSilentSampleScheduledMs: number | null
  firstAudibleLatencyMs: number | null
  leadingSilenceBufferedMs: number
  drainLatencyMs: number | null
  lastTurnId: string | null
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
  queuedPlaybackBufferMs: 0,
  scheduledPlaybackBacklogMs: 0,
  firstChunkLatencyMs: null,
  firstNonSilentChunkLatencyMs: null,
  firstNonSilentSampleScheduledMs: null,
  firstAudibleLatencyMs: null,
  leadingSilenceBufferedMs: 0,
  drainLatencyMs: null,
  lastTurnId: null,
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
const STARTUP_PLAYBACK_BUFFER_BYTES = 1024
const STEADY_PLAYBACK_BUFFER_BYTES = 3072
const STARTUP_PLAYBACK_FLUSH_DELAY_MS = 35
const STEADY_PLAYBACK_FLUSH_DELAY_MS = 70
const MAX_SCHEDULED_BACKLOG_MS = 240
const RESUME_SCHEDULED_BACKLOG_MS = 140
const AUDIO_TOO_QUIET_RMS_THRESHOLD = 0.01
const PCM16_SILENT_RMS_THRESHOLD = 0.0015
const PCM16_SILENT_PEAK_THRESHOLD = 0.01
const PCM16_NEAR_SILENT_RMS_THRESHOLD = 0.008
const PCM16_NEAR_SILENT_PEAK_THRESHOLD = 0.05
const PCM16_VOICED_SAMPLE_THRESHOLD = 800
const PCM16_STARTUP_PRESERVE_MS = 2
const PCM16_STARTUP_FADE_IN_MS = 2

type Pcm16AudibilityAnalysis = {
  silenceClass: 'silent' | 'near_silent' | 'voiced'
  firstVoicedSampleIndex: number | null
  firstVoicedOffsetMs: number | null
  rms: number
  peak: number
  sampleCount: number
}

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

function concatArrayBuffers(chunks: ArrayBufferLike[]) {
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

function analyzePcm16Audibility(buffer: ArrayBuffer): Pcm16AudibilityAnalysis {
  const samples = new Int16Array(buffer)
  if (samples.length === 0) {
    return {
      silenceClass: 'silent',
      firstVoicedSampleIndex: null,
      firstVoicedOffsetMs: null,
      rms: 0,
      peak: 0,
      sampleCount: 0,
    }
  }
  let sumSquares = 0
  let peak = 0
  let firstVoicedSampleIndex: number | null = null
  for (let index = 0; index < samples.length; index += 1) {
    const rawValue = samples[index] ?? 0
    const absoluteValue = Math.abs(rawValue)
    const normalized = absoluteValue / 0x7fff
    sumSquares += normalized * normalized
    if (normalized > peak) {
      peak = normalized
    }
    if (firstVoicedSampleIndex == null && absoluteValue >= PCM16_VOICED_SAMPLE_THRESHOLD) {
      firstVoicedSampleIndex = index
    }
  }
  const rms = Math.sqrt(sumSquares / samples.length)
  let silenceClass: 'silent' | 'near_silent' | 'voiced'
  if (firstVoicedSampleIndex == null) {
    silenceClass = rms <= PCM16_SILENT_RMS_THRESHOLD && peak <= PCM16_SILENT_PEAK_THRESHOLD
      ? 'silent'
      : 'near_silent'
  } else if (rms <= PCM16_NEAR_SILENT_RMS_THRESHOLD && peak <= PCM16_NEAR_SILENT_PEAK_THRESHOLD) {
    silenceClass = 'near_silent'
  } else {
    silenceClass = 'voiced'
  }
  return {
    silenceClass,
    firstVoicedSampleIndex,
    firstVoicedOffsetMs: firstVoicedSampleIndex == null
      ? null
      : Number(((firstVoicedSampleIndex / TARGET_PCM_SAMPLE_RATE) * 1000).toFixed(2)),
    rms,
    peak,
    sampleCount: samples.length,
  }
}

function applyPcm16FadeIn(samples: Int16Array, fadeSamples: number) {
  const effectiveFadeSamples = Math.min(samples.length, Math.max(1, fadeSamples))
  if (effectiveFadeSamples <= 1) {
    return
  }
  for (let index = 0; index < effectiveFadeSamples; index += 1) {
    const scale = index / Math.max(1, effectiveFadeSamples - 1)
    samples[index] = Math.round((samples[index] ?? 0) * scale)
  }
}

function trimPcm16ToFirstVoiced(
  buffer: ArrayBuffer,
  preserveMs = PCM16_STARTUP_PRESERVE_MS,
  fadeInMs = PCM16_STARTUP_FADE_IN_MS,
) {
  const analysis = analyzePcm16Audibility(buffer)
  if (analysis.firstVoicedSampleIndex == null) {
    return {
      trimmedBuffer: null,
      trimmedMs: pcm16DurationMs(buffer.byteLength),
      analysis,
    }
  }
  const preserveSamples = Math.max(0, Math.round((preserveMs / 1000) * TARGET_PCM_SAMPLE_RATE))
  const trimSamples = Math.max(0, analysis.firstVoicedSampleIndex - preserveSamples)
  if (trimSamples <= 0) {
    return {
      trimmedBuffer: buffer.slice(0),
      trimmedMs: 0,
      analysis,
    }
  }
  const input = new Int16Array(buffer)
  const trimmedSamples = input.slice(trimSamples)
  const fadeSamples = Math.round((fadeInMs / 1000) * TARGET_PCM_SAMPLE_RATE)
  applyPcm16FadeIn(trimmedSamples, fadeSamples)
  return {
    trimmedBuffer: trimmedSamples.buffer.slice(
      trimmedSamples.byteOffset,
      trimmedSamples.byteOffset + trimmedSamples.byteLength,
    ),
    trimmedMs: Number(((trimSamples / TARGET_PCM_SAMPLE_RATE) * 1000).toFixed(2)),
    analysis,
  }
}

function normalizePcm16Chunk(
  chunk: ArrayBuffer,
  carry: Uint8Array | null,
): {
  alignedBuffer: ArrayBuffer | null
  nextCarry: Uint8Array | null
  rawByteLength: number
  alignedByteLength: number
  hadOddBoundary: boolean
  firstBytesHex: string
} {
  const incoming = new Uint8Array(chunk)
  const carryBuffer = carry
    ? carry.slice(0).buffer
    : null
  const merged = carry && carry.byteLength > 0
    ? concatArrayBuffers([carryBuffer as ArrayBuffer, chunk])
    : incoming
  const mergedLength = merged.byteLength
  const evenLength = mergedLength - (mergedLength % 2)
  const aligned = evenLength > 0 ? merged.slice(0, evenLength) : null
  const nextCarry = mergedLength % 2 === 0 ? null : merged.slice(mergedLength - 1)
  return {
    alignedBuffer: aligned ? aligned.buffer.slice(aligned.byteOffset, aligned.byteOffset + aligned.byteLength) : null,
    nextCarry,
    rawByteLength: incoming.byteLength,
    alignedByteLength: evenLength,
    hadOddBoundary: mergedLength % 2 !== 0 || Boolean(carry && carry.byteLength > 0),
    firstBytesHex: Array.from(incoming.slice(0, 12)).map((value) => value.toString(16).padStart(2, '0')).join(''),
  }
}

function formatMetric(value?: number | null) {
  return value == null ? '—' : `${Math.round(value)} ms`
}

function pcm16DurationMs(byteLength: number) {
  if (byteLength <= 0) {
    return 0
  }
  return Number(((byteLength / (TARGET_PCM_SAMPLE_RATE * TARGET_PCM_CHANNELS * 2)) * 1000).toFixed(2))
}

function getPlaybackPolicy(isTurnStartup: boolean) {
  if (isTurnStartup) {
    return {
      minimumBytes: STARTUP_PLAYBACK_BUFFER_BYTES,
      flushDelayMs: STARTUP_PLAYBACK_FLUSH_DELAY_MS,
      mode: 'startup' as const,
    }
  }
  return {
    minimumBytes: STEADY_PLAYBACK_BUFFER_BYTES,
    flushDelayMs: STEADY_PLAYBACK_FLUSH_DELAY_MS,
    mode: 'steady' as const,
  }
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
  const inboundOddByteCarryRef = useRef<Uint8Array | null>(null)
  const playbackFlushTimerRef = useRef<number | null>(null)
  const playbackSilenceTimerRef = useRef<number | null>(null)
  const activeSourcesRef = useRef<AudioBufferSourceNode[]>([])
  const currentTurnRef = useRef<{
    turnId: string
    backendFirstChunkSentMs: number | null
    backendFirstNonSilentChunkSentMs: number | null
    backendProviderLeadingSilenceMs: number | null
    backendLeadingSilenceMs: number | null
    frontendFirstChunkReceivedAt: number | null
    frontendFirstNonSilentChunkReceivedAt: number | null
    frontendLastChunkReceivedAt: number | null
    frontendPlaybackStartedAt: number | null
    frontendFirstNonSilentSampleScheduledAt: number | null
    frontendLeadingSilenceBufferedMs: number
    backendCompleted: boolean
    backendAudioDurationMs: number | null
  } | null>(null)

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
      if (arrayBuffer.byteLength % 2 !== 0) {
        const message = 'Получен нечётный PCM16 buffer; отбрасываю последний байт перед decode.'
        logBrowserEvent('browser_playback.decode_suspicious', {
          source,
          byte_length: arrayBuffer.byteLength,
          reason: 'odd_byte_length',
        }, 'warn')
        updateLocalAudioDebug({ lastPlaybackError: message })
      }
      const pcmFloat = int16ToFloat32(arrayBuffer)
      latestWaveformRef.current = pcmFloat
      setWaveformVersion((previous) => previous + 1)
      const playbackSampleRate = runtime.context.sampleRate
      const formatMismatch = playbackSampleRate !== inputSampleRate
      logBrowserEvent('browser_playback.audio_format_detected', {
        source,
        format: 'pcm_s16le',
        expected_sample_rate: inputSampleRate,
        actual_sample_rate: playbackSampleRate,
        channels: TARGET_PCM_CHANNELS,
        sample_width_bits: TARGET_PCM_BIT_DEPTH,
        container: 'raw',
        endian: 'little',
        byte_length: arrayBuffer.byteLength,
      })
      const playbackFloat = formatMismatch
        ? resampleFloat32Linear(pcmFloat, inputSampleRate, playbackSampleRate)
        : pcmFloat
      if (formatMismatch) {
        logBrowserEvent('browser_playback.resample_applied', {
          source,
          from_sample_rate: inputSampleRate,
          to_sample_rate: playbackSampleRate,
          samples_in: pcmFloat.length,
          samples_out: playbackFloat.length,
        })
      }
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
      const scheduledBacklogMs = Math.max(0, (playbackCursorRef.current - runtime.context.currentTime) * 1000)
      playbackCursorRef.current = Math.max(playbackCursorRef.current, runtime.context.currentTime + 0.02)
      activeSourcesRef.current.push(bufferSource)
      bufferSource.start(playbackCursorRef.current)
      playbackCursorRef.current += buffer.duration
      lastAssistantAudioAtRef.current = Date.now()
      bumpLocalAudioCounter('playbackStarts')
      bufferSource.onended = () => {
        activeSourcesRef.current = activeSourcesRef.current.filter((s) => s !== bufferSource)
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
        if (
          source === 'browser_inbound'
          && activeSourcesRef.current.length === 0
          && currentTurnRef.current?.backendCompleted
          && currentTurnRef.current.frontendLastChunkReceivedAt != null
        ) {
          const drainLatencyMs = Number((performance.now() - currentTurnRef.current.frontendLastChunkReceivedAt).toFixed(2))
          updateLocalAudioDebug({ drainLatencyMs })
          logBrowserEvent('browser_playback.queue_drained', {
            turn_id: currentTurnRef.current.turnId,
            drain_latency_ms: drainLatencyMs,
          }, drainLatencyMs > 220 ? 'warn' : 'info')
        }
      }
      if (
        source === 'browser_inbound'
        && currentTurnRef.current
        && currentTurnRef.current.frontendFirstNonSilentChunkReceivedAt != null
        && currentTurnRef.current.frontendFirstNonSilentSampleScheduledAt == null
      ) {
        const now = performance.now()
        const frontendQueueStartMs = now - currentTurnRef.current.frontendFirstNonSilentChunkReceivedAt
        const estimatedFirstAudibleMs = (
          (currentTurnRef.current.backendFirstNonSilentChunkSentMs
            ?? currentTurnRef.current.backendFirstChunkSentMs
            ?? 0)
          + frontendQueueStartMs
        )
        currentTurnRef.current = {
          ...currentTurnRef.current,
          frontendPlaybackStartedAt: now,
          frontendFirstNonSilentSampleScheduledAt: now,
        }
        updateLocalAudioDebug({
          firstChunkLatencyMs: currentTurnRef.current.backendFirstChunkSentMs,
          firstNonSilentChunkLatencyMs: (
            currentTurnRef.current.backendFirstNonSilentChunkSentMs
            ?? currentTurnRef.current.backendFirstChunkSentMs
          ),
          firstNonSilentSampleScheduledMs: Number(estimatedFirstAudibleMs.toFixed(2)),
          firstAudibleLatencyMs: Number(estimatedFirstAudibleMs.toFixed(2)),
          leadingSilenceBufferedMs: Number(currentTurnRef.current.frontendLeadingSilenceBufferedMs.toFixed(2)),
        })
        logBrowserEvent('browser_playback.first_voiced_start', {
          turn_id: currentTurnRef.current.turnId,
          frontend_queue_start_ms: Number(frontendQueueStartMs.toFixed(2)),
          first_non_silent_chunk_sent_ms: currentTurnRef.current.backendFirstNonSilentChunkSentMs,
          audible_start_estimate_ms: Number(estimatedFirstAudibleMs.toFixed(2)),
          frontend_leading_silence_buffered_ms: Number(currentTurnRef.current.frontendLeadingSilenceBufferedMs.toFixed(2)),
        }, frontendQueueStartMs > 120 ? 'warn' : 'info')
      }
      updateLocalAudioDebug({
        lastPlaybackError: null,
        audioContextState: runtime.context.state,
        // Only flag as likely failure if audio is playing but silent (RMS too low)
        playbackDiagnostic: audioTooQuiet ? 'PLAYBACK_FAILURE_LIKELY' : null,
        scheduledPlaybackBacklogMs: Number(scheduledBacklogMs.toFixed(2)),
      })
      setAiState('speaking')
      logBrowserEvent('browser_playback.output_started', {
        source,
        format: 'pcm_s16le',
        pcm_bytes: arrayBuffer.byteLength,
        playback_cursor_s: playbackCursorRef.current,
        scheduled_backlog_ms: Number(scheduledBacklogMs.toFixed(2)),
        gain: playbackGain.gain.value,
        rms: Number(rms.toFixed(4)),
        max_amplitude: Number(peak.toFixed(4)),
        output_device: localAudioDebugRef.current.currentOutputDeviceId || 'system-default',
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
    const runtime = audioRuntimeRef.current
    const scheduledBacklogMs = runtime
      ? Math.max(0, (playbackCursorRef.current - runtime.context.currentTime) * 1000)
      : 0
    if (reason !== 'force' && scheduledBacklogMs > MAX_SCHEDULED_BACKLOG_MS) {
      updateLocalAudioDebug({
        scheduledPlaybackBacklogMs: Number(scheduledBacklogMs.toFixed(2)),
        queuedPlaybackBufferMs: pcm16DurationMs(pendingPlaybackBytesRef.current),
      })
      logBrowserEvent('playback_queue_too_large', {
        reason,
        queued_bytes: pendingPlaybackBytesRef.current,
        queued_audio_ms: pcm16DurationMs(pendingPlaybackBytesRef.current),
        scheduled_backlog_ms: Number(scheduledBacklogMs.toFixed(2)),
      }, 'warn')
      playbackFlushTimerRef.current = window.setTimeout(() => {
        void flushPlaybackQueue('timer')
      }, Math.min(80, Math.max(20, scheduledBacklogMs - RESUME_SCHEDULED_BACKLOG_MS)))
      return
    }
    const mergedBuffer = concatArrayBuffers(pendingPlaybackChunksRef.current).buffer
    const mergedBytes = pendingPlaybackBytesRef.current
    pendingPlaybackChunksRef.current = []
    pendingPlaybackBytesRef.current = 0
    inboundOddByteCarryRef.current = null
    const policy = getPlaybackPolicy(Boolean(
      currentTurnRef.current
      ? currentTurnRef.current.frontendPlaybackStartedAt == null
      : localAudioDebugRef.current.playbackStarts <= 0
    ))
    logBrowserEvent('playback_buffer_flushed', {
      reason,
      merged_bytes: mergedBytes,
      merged_samples: Math.floor(mergedBytes / 2),
      minimum_bytes: policy.minimumBytes,
      policy_mode: policy.mode,
      queued_audio_ms: pcm16DurationMs(mergedBytes),
      scheduled_backlog_ms: Number(scheduledBacklogMs.toFixed(2)),
    })
    await playPcm16Buffer(mergedBuffer, { source: 'browser_inbound' })
  }, [logBrowserEvent, playPcm16Buffer])

  const enqueuePlaybackChunk = useCallback((arrayBuffer: ArrayBuffer) => {
    pendingPlaybackChunksRef.current.push(arrayBuffer.slice(0))
    pendingPlaybackBytesRef.current += arrayBuffer.byteLength
    const policy = getPlaybackPolicy(Boolean(
      currentTurnRef.current
      ? currentTurnRef.current.frontendPlaybackStartedAt == null
      : localAudioDebugRef.current.playbackStarts <= 0
    ))
    updateLocalAudioDebug({
      queuedPlaybackBufferMs: pcm16DurationMs(pendingPlaybackBytesRef.current),
    })
    if (pendingPlaybackBytesRef.current >= policy.minimumBytes) {
      void flushPlaybackQueue('threshold')
      return
    }
    if (playbackFlushTimerRef.current) {
      return
    }
    playbackFlushTimerRef.current = window.setTimeout(() => {
      void flushPlaybackQueue('timer')
    }, policy.flushDelayMs)
  }, [flushPlaybackQueue, updateLocalAudioDebug])

  const handleInboundPlaybackPayload = useCallback((
    payload: ArrayBuffer,
    transport: 'websocket_blob' | 'websocket_arraybuffer',
  ) => {
    bumpLocalAudioCounter('inboundAudioChunkCount')
    const normalized = normalizePcm16Chunk(payload, inboundOddByteCarryRef.current)
    inboundOddByteCarryRef.current = normalized.nextCarry

    let playbackBuffer = normalized.alignedBuffer
    let analysis = playbackBuffer ? analyzePcm16Audibility(playbackBuffer) : null
    let leadingTrimmedMs = 0
    const turn = currentTurnRef.current

    if (turn) {
      const now = performance.now()
      if (turn.frontendFirstChunkReceivedAt == null) {
        turn.frontendFirstChunkReceivedAt = now
        logBrowserEvent('browser_playback.frontend_first_chunk_received', {
          turn_id: turn.turnId,
          backend_first_chunk_sent_ms: turn.backendFirstChunkSentMs,
        })
      }
      turn.frontendLastChunkReceivedAt = now

      if (playbackBuffer && turn.frontendFirstNonSilentChunkReceivedAt == null) {
        const startupPrepared = trimPcm16ToFirstVoiced(playbackBuffer)
        analysis = startupPrepared.analysis
        if (startupPrepared.trimmedBuffer == null) {
          turn.frontendLeadingSilenceBufferedMs = Number(
            (turn.frontendLeadingSilenceBufferedMs + startupPrepared.trimmedMs).toFixed(2),
          )
          updateLocalAudioDebug({
            leadingSilenceBufferedMs: turn.frontendLeadingSilenceBufferedMs,
          })
          logBrowserEvent('tts.leading_silence_detected', {
            turn_id: turn.turnId,
            stage: 'frontend_buffer',
            silence_class: analysis.silenceClass,
            rms: Number(analysis.rms.toFixed(6)),
            peak: Number(analysis.peak.toFixed(6)),
            byte_length: normalized.alignedByteLength,
            leading_silence_ms: turn.frontendLeadingSilenceBufferedMs,
            first_bytes_preview_hex: normalized.firstBytesHex,
          }, 'info')
          playbackBuffer = null
        } else {
          playbackBuffer = startupPrepared.trimmedBuffer
          leadingTrimmedMs = startupPrepared.trimmedMs
          turn.frontendLeadingSilenceBufferedMs = Number(
            (turn.frontendLeadingSilenceBufferedMs + leadingTrimmedMs).toFixed(2),
          )
          turn.frontendFirstNonSilentChunkReceivedAt = now
          updateLocalAudioDebug({
            firstChunkLatencyMs: turn.backendFirstChunkSentMs,
            firstNonSilentChunkLatencyMs: turn.backendFirstNonSilentChunkSentMs ?? turn.backendFirstChunkSentMs,
            leadingSilenceBufferedMs: turn.frontendLeadingSilenceBufferedMs,
          })
          if (leadingTrimmedMs > 0) {
            logBrowserEvent('tts.leading_silence_trimmed', {
              turn_id: turn.turnId,
              stage: 'frontend_playback',
              leading_trimmed_ms: leadingTrimmedMs,
              total_leading_trimmed_ms: turn.frontendLeadingSilenceBufferedMs,
              first_voiced_offset_ms: analysis.firstVoicedOffsetMs,
            })
          }
          logBrowserEvent('browser_playback.first_voiced_chunk_received', {
            turn_id: turn.turnId,
            backend_first_non_silent_chunk_sent_ms: turn.backendFirstNonSilentChunkSentMs,
            provider_leading_silence_ms: turn.backendProviderLeadingSilenceMs,
            backend_leading_silence_ms: turn.backendLeadingSilenceMs,
            frontend_leading_silence_ms: turn.frontendLeadingSilenceBufferedMs,
            byte_length: playbackBuffer.byteLength,
            silence_class: analysis.silenceClass,
            rms: Number(analysis.rms.toFixed(6)),
            peak: Number(analysis.peak.toFixed(6)),
            first_voiced_offset_ms: analysis.firstVoicedOffsetMs,
          })
        }
      }
    }

    logBrowserEvent('browser_call.audio_chunk_received', {
      transport,
      format: 'pcm_s16le',
      sample_rate: TARGET_PCM_SAMPLE_RATE,
      channels: TARGET_PCM_CHANNELS,
      sample_width_bits: TARGET_PCM_BIT_DEPTH,
      container: 'raw',
      endian: 'little',
      raw_byte_length: normalized.rawByteLength,
      aligned_byte_length: normalized.alignedByteLength,
      had_odd_boundary: normalized.hadOddBoundary,
      first_bytes_preview_hex: normalized.firstBytesHex,
      silence_class: analysis?.silenceClass ?? null,
      rms: analysis ? Number(analysis.rms.toFixed(6)) : null,
      peak: analysis ? Number(analysis.peak.toFixed(6)) : null,
      first_voiced_offset_ms: analysis?.firstVoicedOffsetMs ?? null,
      leading_trimmed_ms: leadingTrimmedMs,
    }, normalized.hadOddBoundary ? 'warn' : 'info')

    if (playbackBuffer) {
      enqueuePlaybackChunk(playbackBuffer)
    }
  }, [bumpLocalAudioCounter, enqueuePlaybackChunk, logBrowserEvent, updateLocalAudioDebug])

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
    if (inboundOddByteCarryRef.current?.byteLength) {
      logBrowserEvent('browser_playback.decode_suspicious', {
        reason: 'dangling_carry_byte_on_stop',
        dangling_bytes: inboundOddByteCarryRef.current.byteLength,
      }, 'warn')
    }
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
    currentTurnRef.current = null
    setMicState('idle')
    setAiState('silent')
    inboundPcmChunksRef.current = []
    inboundOddByteCarryRef.current = null
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
    // Merge: keep any WebSocket-pushed entries (ws-* ids) not yet in the DB response.
    setStatus(prev => {
      const dbIds = new Set(data.transcript_entries.map(e => e.id))
      const wsOnly = prev?.transcript_entries.filter(e => e.id.startsWith('ws-') && !dbIds.has(e.id)) ?? []
      return { ...data, transcript_entries: [...data.transcript_entries, ...wsOnly] }
    })
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
    currentTurnRef.current = null
    setLastAudioDownloadUrl(null)
    setLastAudioWavSize(0)
    setLastAudioValid(false)
    inboundPcmChunksRef.current = []
    inboundOddByteCarryRef.current = null
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
          try {
            const msg = JSON.parse(event.data) as { type?: string }
            if (msg.type === 'interrupted') {
              // Gemini interrupted — cancel all scheduled audio immediately
              const runtime = audioRuntimeRef.current
              for (const src of activeSourcesRef.current) {
                try { src.stop() } catch { /* already ended */ }
              }
              activeSourcesRef.current = []
              pendingPlaybackChunksRef.current = []
              pendingPlaybackBytesRef.current = 0
              if (playbackFlushTimerRef.current) {
                window.clearTimeout(playbackFlushTimerRef.current)
                playbackFlushTimerRef.current = null
              }
              if (runtime) {
                playbackCursorRef.current = runtime.context.currentTime
              }
              currentTurnRef.current = null
              setAiState('silent')
              logBrowserEvent('barge_in_interrupted', {})
              return
            }
            if (msg.type === 'call_ended') {
              // Agent-initiated hangup or normal backend termination — not an error.
              closingRef.current = true
              return
            }
            if (msg.type === 'tts_turn_metrics') {
              const typed = msg as {
                type: 'tts_turn_metrics'
                phase?: 'started' | 'completed'
                turn_id?: string
                tts_first_chunk_sent_to_bridge_ms?: number
                tts_first_non_silent_chunk_sent_ms?: number
                tts_first_non_silent_chunk_played_ms?: number
                tts_provider_first_non_silent_chunk_ms?: number
                tts_provider_leading_silence_ms?: number
                tts_backend_leading_silence_ms?: number
                emitted_audio_duration_ms?: number
                leading_silence_trimmed_ms?: number
                trailing_silence_trimmed_ms?: number
                raw_chunks_in?: number
                optimized_chunks_out?: number
              }
              if (typed.turn_id) {
                if (typed.phase === 'started') {
                  currentTurnRef.current = {
                    turnId: typed.turn_id,
                    backendFirstChunkSentMs: typed.tts_first_chunk_sent_to_bridge_ms ?? null,
                    backendFirstNonSilentChunkSentMs: (
                      typed.tts_first_non_silent_chunk_sent_ms
                      ?? typed.tts_first_chunk_sent_to_bridge_ms
                      ?? null
                    ),
                    backendProviderLeadingSilenceMs: typed.tts_provider_leading_silence_ms ?? null,
                    backendLeadingSilenceMs: typed.tts_backend_leading_silence_ms ?? null,
                    frontendFirstChunkReceivedAt: null,
                    frontendFirstNonSilentChunkReceivedAt: null,
                    frontendLastChunkReceivedAt: null,
                    frontendPlaybackStartedAt: null,
                    frontendFirstNonSilentSampleScheduledAt: null,
                    frontendLeadingSilenceBufferedMs: 0,
                    backendCompleted: false,
                    backendAudioDurationMs: null,
                  }
                  updateLocalAudioDebug({
                    lastTurnId: typed.turn_id,
                    firstChunkLatencyMs: typed.tts_first_chunk_sent_to_bridge_ms ?? null,
                    firstNonSilentChunkLatencyMs: (
                      typed.tts_first_non_silent_chunk_sent_ms
                      ?? typed.tts_first_chunk_sent_to_bridge_ms
                      ?? null
                    ),
                    firstNonSilentSampleScheduledMs: null,
                    firstAudibleLatencyMs: null,
                    leadingSilenceBufferedMs: 0,
                    drainLatencyMs: null,
                  })
                }
                if (typed.phase === 'completed' && currentTurnRef.current?.turnId === typed.turn_id) {
                  currentTurnRef.current = {
                    ...currentTurnRef.current,
                    backendCompleted: true,
                    backendAudioDurationMs: typed.emitted_audio_duration_ms ?? null,
                  }
                  logBrowserEvent('browser_playback.turn_completed', {
                    turn_id: typed.turn_id,
                    raw_chunks_in: typed.raw_chunks_in,
                    optimized_chunks_out: typed.optimized_chunks_out,
                    emitted_audio_duration_ms: typed.emitted_audio_duration_ms,
                    provider_first_non_silent_chunk_ms: typed.tts_provider_first_non_silent_chunk_ms,
                    first_non_silent_chunk_sent_ms: typed.tts_first_non_silent_chunk_sent_ms,
                    first_non_silent_chunk_played_ms: typed.tts_first_non_silent_chunk_played_ms,
                    provider_leading_silence_ms: typed.tts_provider_leading_silence_ms,
                    backend_leading_silence_ms: typed.tts_backend_leading_silence_ms,
                    leading_silence_trimmed_ms: typed.leading_silence_trimmed_ms,
                    trailing_silence_trimmed_ms: typed.trailing_silence_trimmed_ms,
                  })
                }
              }
              return
            }
            if (msg.type === 'transcript') {
              const { role, text } = msg as { role: string; text: string }
              setStatus(prev => {
                if (!prev) return prev
                const entry: TranscriptEntry = {
                  id: `ws-${Date.now()}-${Math.random()}`,
                  role,
                  text,
                  created_at: new Date().toISOString(),
                }
                return { ...prev, transcript_entries: [...prev.transcript_entries, entry] }
              })
              return
            }
          } catch { /* not JSON, fall through */ }
          logBrowserEvent('websocket_message', { payload: event.data })
          return
        }
        if (event.data instanceof Blob) {
          void event.data.arrayBuffer().then((payload) => {
            handleInboundPlaybackPayload(payload, 'websocket_blob')
          })
          return
        }
        handleInboundPlaybackPayload(event.data, 'websocket_arraybuffer')
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
    fetchStatus,
    handleInboundPlaybackPayload,
    label,
    logBrowserEvent,
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

  const debugGroups = useMemo(() => {
    const debug = status?.debug
    return [
      {
        title: 'Сессия',
        items: [
          ['id', (debug?.session_id || session?.session_id || '—').slice(-12)],
          ['агент', selectedAgentName],
          ['стратегия', debug?.voice_strategy || session?.voice_strategy || '—'],
          ['путь голоса', debug?.active_voice_path || session?.active_voice_path || '—'],
          ['резервный путь', debug?.fallback_used ? (debug?.fallback_voice_path || 'да') : 'нет'],
          ['статус', status?.status || session?.status || 'ожидание'],
        ],
      },
      {
        title: 'Браузер',
        items: [
          ['веб-сокет', localAudioDebug.websocketConnected ? 'подключён' : 'отключён'],
          ['микрофон', localAudioDebug.micPermission],
          ['audio ctx', localAudioDebug.audioContextState],
          ['вход SR', localAudioDebug.inputSampleRate ? `${localAudioDebug.inputSampleRate} Hz` : '—'],
          ['выход SR', `${localAudioDebug.targetSampleRate} Hz`],
          ['канал', localAudioDebug.inputChannelMode],
          ['задержка', localAudioDebug.estimatedOutputLatencyMs != null ? `${localAudioDebug.estimatedOutputLatencyMs} ms` : '—'],
        ],
      },
      {
        title: 'Чанки',
        items: [
          ['исходящих', String(localAudioDebug.outboundChunkCount)],
          ['входящих', String(localAudioDebug.inboundAudioChunkCount)],
          ['стартов воспроизведения', String(localAudioDebug.playbackStarts)],
          ['создано нод', String(localAudioDebug.playbackNodesCreated)],
          ['завершено воспроизведения', String(localAudioDebug.playbackEndedCount)],
          ['сервер вход', String(debug?.inbound_chunks_received ?? 0)],
          ['сервер выход', String(debug?.outbound_chunks_played ?? 0)],
          ['сервер tts raw', String(debug?.tts_chunks_in_last ?? 0)],
          ['сервер tts opt', String(debug?.tts_chunks_out_last ?? 0)],
          ['сервер tts tiny', String(debug?.tts_tiny_chunks_in_last ?? 0)],
        ],
      },
      {
        title: 'Воспроизведение',
        items: [
          ['усиление', String(localAudioDebug.playbackGainValue)],
          ['частота', localAudioDebug.playbackSampleRate ? `${localAudioDebug.playbackSampleRate} Hz` : '—'],
          ['каналы', String(localAudioDebug.playbackChannels)],
          ['бит', `${localAudioDebug.playbackBitDepth}-bit`],
          ['буфер', String(localAudioDebug.playbackBufferLength)],
          ['несовпадение', localAudioDebug.playbackFormatMismatch ? 'да' : 'нет'],
          ['диагностика', localAudioDebug.playbackDiagnostic || 'нет'],
          ['RMS', localAudioDebug.lastRms != null ? localAudioDebug.lastRms.toFixed(4) : '—'],
          ['peak', localAudioDebug.lastPeak != null ? localAudioDebug.lastPeak.toFixed(4) : '—'],
          ['тихо', localAudioDebug.audioTooQuiet ? 'да' : 'нет'],
          ['очередь, мс', `${Math.round(localAudioDebug.queuedPlaybackBufferMs)} ms`],
          ['backlog, мс', `${Math.round(localAudioDebug.scheduledPlaybackBacklogMs)} ms`],
          ['лидирующая тишина', formatMetric(localAudioDebug.leadingSilenceBufferedMs)],
          ['первый не-тихий', formatMetric(localAudioDebug.firstNonSilentSampleScheduledMs)],
          ['первый слышимый', formatMetric(localAudioDebug.firstAudibleLatencyMs)],
          ['дренаж', formatMetric(localAudioDebug.drainLatencyMs)],
          ['устройство', localAudioDebug.currentOutputDeviceId || 'по умолчанию'],
          ['здоровье', audioHealthError || 'норма'],
          ['размер wav', lastAudioWavSize ? `${lastAudioWavSize}b` : '0'],
          ['wav валиден', lastAudioValid ? 'да' : 'нет'],
        ],
      },
      {
        title: 'Loopback',
        items: [
          ['активен', loopbackDebug.active ? 'да' : 'нет'],
          ['чанки микрофона', String(loopbackDebug.micChunks)],
          ['чанки воспроизведения', String(loopbackDebug.playbackChunks)],
          ['задержка', loopbackDebug.estimatedLatencyMs != null ? `${loopbackDebug.estimatedLatencyMs} ms` : '—'],
          ['ошибка', loopbackDebug.lastError || 'нет'],
        ],
      },
      {
        title: 'Метрики',
        items: [
          ['модель', formatMetric(debug?.model_response_latency_ms_last)],
          ['tts', formatMetric(debug?.tts_latency_ms_last)],
          ['tts -> bridge', formatMetric(debug?.tts_first_chunk_sent_ms_last)],
          ['провайдер: первый голос', formatMetric(debug?.tts_provider_first_non_silent_chunk_ms_last)],
          ['bridge: первый голос', formatMetric(debug?.tts_first_non_silent_chunk_sent_ms_last)],
          ['воспроизведён первый голос', formatMetric(debug?.tts_first_non_silent_chunk_played_ms_last)],
          ['tts хвост', formatMetric(debug?.tts_last_chunk_received_ms_last)],
          ['длина tts-аудио', formatMetric(debug?.tts_audio_duration_ms_last)],
          ['лидирующая тишина провайдера', formatMetric(debug?.tts_provider_leading_silence_ms_last)],
          ['лидирующая тишина backend', formatMetric(debug?.tts_backend_leading_silence_ms_last)],
          ['обрезано в начале', formatMetric(debug?.tts_leading_silence_trimmed_ms_last)],
          ['обрезано в конце', formatMetric(debug?.tts_trailing_silence_trimmed_ms_last)],
          ['frontend: первый чанк', formatMetric(localAudioDebug.firstChunkLatencyMs)],
          ['frontend: первый голос', formatMetric(localAudioDebug.firstNonSilentChunkLatencyMs)],
          ['задержка воспроизведения', formatMetric(debug?.outbound_playback_latency_ms_last)],
          ['id хода', localAudioDebug.lastTurnId || debug?.tts_turn_id_last || '—'],
        ],
      },
      {
        title: 'Ошибки',
        items: [
          ['воспроизведение', localAudioDebug.lastPlaybackError || 'нет'],
          ['транспорт', localAudioDebug.lastTransportError || 'нет'],
          ['последняя ошибка', debug?.last_error || error || 'нет'],
          ['этап', debug?.last_failure_stage || '—'],
        ],
      },
    ]
  }, [audioHealthError, error, lastAudioValid, lastAudioWavSize, localAudioDebug, loopbackDebug, selectedAgentName, session, status])

  return (
    <section className="page-grid browser-call-page">
      <article className="hero-card browser-call-hero">
        <div>
          <p className="eyebrow">Внутреннее тестирование</p>
          <h3>Браузерный звонок</h3>
          <p>Тестовая голосовая сессия идёт мимо Mango и PSTN, но использует тот же контур `direct-runtime`, транскрипт и стратегию голоса.</p>
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
              {loopbackDebug.starting ? 'Запуск аудиопетли…' : 'Тест микрофона (аудиопетля)'}
            </button>
            <button
              type="button"
              onClick={() => void stopLoopback()}
              disabled={!loopbackDebug.active}
            >
              Остановить аудиопетлю
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
            <span className={`status-pill${status?.status === 'IN_PROGRESS' ? ' live' : ''}`}>Сессия: {status?.status || 'ожидание'}</span>
            <span className={`status-pill${aiState === 'speaking' ? ' live' : ''}`}>AI: {aiState}</span>
            <span className={`status-pill${error ? ' error' : ''}`}>Ошибка: {error || 'нет'}</span>
            <span className={`status-pill${audioHealthError ? ' error' : ''}`}>Аудио: {audioHealthError || 'норма'}</span>
            <span className={`status-pill${localAudioDebug.playbackDiagnostic ? ' error' : ''}`}>
              Воспроизведение: {localAudioDebug.playbackDiagnostic || 'норма'}
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
              <div className="transcript-meta">{entry.role === 'assistant' ? 'Агент' : 'Пользователь'} · {new Date(entry.created_at).toLocaleTimeString()}</div>
              <div>{entry.text}</div>
            </article>
          )) : (
            <article className="transcript-bubble empty">
              <div className="transcript-meta">Транскрипт пока пуст</div>
              <div>Ждём приветствие или ответ от агента.</div>
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
        <div className="debug-groups">
          {debugGroups.map((group) => (
            <div key={group.title} className="debug-group">
              <p className="debug-group-title">{group.title}</p>
              {group.items.map(([labelText, value]) => (
                <div className="debug-row" key={labelText}>
                  <span>{labelText}</span>
                  <strong>{value}</strong>
                </div>
              ))}
            </div>
          ))}
        </div>
      </aside>
    </section>
  )
}
