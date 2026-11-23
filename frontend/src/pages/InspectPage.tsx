import {
  ArrowRight,
  Camera,
  Check,
  CircleAlert,
  Clock3,
  Download,
  ImagePlus,
  LoaderCircle,
  RefreshCw,
  ScanLine,
  ShieldCheck,
  Sparkles,
  Trash2,
  Upload,
  X,
  Mic,
  Square,
} from 'lucide-react'
import { useCallback, useEffect, useRef, useState, type ChangeEvent, type DragEvent } from 'react'
import {
  classifyInspectionResult,
  clearInspectionHistory,
  type Verdict,
  downloadInspectionReport,
  inspectionAnnotatedUrl,
  inspectionOriginalUrl,
  inspectionThumbnailUrl,
  listInspectionHistory,
  relocateDefect,
  type ApiStatus,
  type InspectionHistoryItem,
  type InspectionResult,
} from '../services/api'
import { useInspectionJobs } from '../context/useInspectionJobs.ts'
import { listCategories, type ProductCategory } from '../services/categories'
import { prepareRecording, SILENCE_PEAK_THRESHOLD } from '../utils/audio'

const MAX_IMAGE_BYTES = 10 * 1024 * 1024
const MAX_VOICE_NOTE_BYTES = 25 * 1024 * 1024
const PDF_URL_REVOKE_DELAY_MS = 1_000
const ACCEPTED_IMAGE_TYPES = new Set(['image/jpeg', 'image/png', 'image/webp'])
const ACCEPTED_VOICE_NOTE_TYPES = new Set([
  'audio/mpeg',
  'audio/mp3',
  'audio/wav',
  'audio/x-wav',
  'audio/mp4',
  'audio/m4a',
  'audio/x-m4a',
  'audio/aac',
  'audio/x-aac',
  'audio/flac',
  'audio/x-flac',
  'audio/webm',
  'audio/ogg',
])
const ACCEPTED_VOICE_NOTE_EXTENSIONS = new Set([
  'mp3',
  'mpga',
  'wav',
  'mp4',
  'm4a',
  'aac',
  'flac',
  'webm',
  'ogg',
])
const ACCEPTED_IMAGE_EXTENSIONS = '.jpg, .jpeg, .png, .webp'
// Hero art is decorative: these are layout slots, not bindings to specific fruits.
// Reordering or adding categories changes which fruit fills each slot, never the layout.
const HERO_CARD_SLOTS = [
  'fruit-card-focus',
  'fruit-card-upper-left',
  'fruit-card-lower-right',
  'fruit-card-upper-right',
]
const FALLBACK_CATEGORIES: ProductCategory[] = [
  {
    slug: 'tomato',
    display_name: 'Tomato',
    emoji: '🍅',
    is_active: true,
    created_at: '',
  },
  {
    slug: 'apple',
    display_name: 'Apple',
    emoji: '🍎',
    is_active: true,
    created_at: '',
  },
  {
    slug: 'banana',
    display_name: 'Banana',
    emoji: '🍌',
    is_active: true,
    created_at: '',
  },
  {
    slug: 'orange',
    display_name: 'Orange',
    emoji: '🍊',
    is_active: true,
    created_at: '',
  },
]

function capitalize(value: string): string {
  return value.charAt(0).toUpperCase() + value.slice(1)
}

function humanizeSlug(slug: string): string {
  return capitalize(slug.replace(/-/g, ' '))
}

// Virtual loopback devices (BlackHole, Teams, Soundflower) are silent unless
// another app feeds them audio, so never pick one automatically for a voice note.
const VIRTUAL_AUDIO_PATTERN = /blackhole|virtual|teams|loopback|soundflower/i
const BUILTIN_MIC_PATTERN = /macbook pro microphone|built-in microphone|internal microphone/i

/** Pick the real hardware microphone, preferring the built-in one. */
function preferredAudioDevice(devices: MediaDeviceInfo[]): MediaDeviceInfo | undefined {
  return (
    devices.find(
      (device) =>
        BUILTIN_MIC_PATTERN.test(device.label) && !VIRTUAL_AUDIO_PATTERN.test(device.label),
    ) ??
    devices.find(
      (device) => device.deviceId !== 'default' && !VIRTUAL_AUDIO_PATTERN.test(device.label),
    ) ??
    devices.find((device) => !VIRTUAL_AUDIO_PATTERN.test(device.label)) ??
    devices[0]
  )
}

function productName(
  item: { produce_type: string; detected_item?: string },
  categories: ProductCategory[],
): string {
  const detected = item.detected_item?.trim()
  if (detected) return capitalize(detected)
  if (item.produce_type === 'unknown') return 'Unidentified product'
  const category = categories.find((entry) => entry.slug === item.produce_type)
  return category?.display_name ?? humanizeSlug(item.produce_type)
}

function productEmoji(
  item: { produce_type: string; detected_item?: string },
  categories: ProductCategory[],
  unsupported: boolean,
): string {
  const category = categories.find((entry) => entry.slug === item.produce_type)
  if (category?.emoji) return category.emoji
  return unsupported ? '🚫' : '🌱'
}
const VERDICT_LABELS: Record<InspectionResult['verdict'], string> = {
  pass: 'Looks good',
  defect: 'Defect detected',
  needs_review: 'Needs a closer look',
}
type HistoryFilter = 'all' | Verdict | 'unsupported'
const HISTORY_FILTERS: { value: HistoryFilter; label: string }[] = [
  { value: 'all', label: 'All' },
  { value: 'pass', label: 'Looks good' },
  { value: 'defect', label: 'Defects' },
  { value: 'needs_review', label: 'Review' },
  { value: 'unsupported', label: 'Unsupported' },
]

function matchesHistoryFilter(item: InspectionHistoryItem, filter: HistoryFilter): boolean {
  if (filter === 'all') return true
  const unsupported = classifyInspectionResult(item) === 'unsupported_item'
  if (filter === 'unsupported') return unsupported
  return !unsupported && item.verdict === filter
}
const RIPENESS_GUIDE = [
  {
    name: 'Orange',
    emoji: '🍊',
    lessRipe: [
      'A greenish or pale cast may remain, especially near the stem.',
      'Peel shine or tight texture alone cannot establish ripeness.',
    ],
    riper: [
      'Orange to yellow-orange color is common, but some ripe varieties keep green areas.',
      'A drier, flatter stem button can be a weak cue, not a rule.',
    ],
  },
  {
    name: 'Apple',
    emoji: '🍎',
    lessRipe: [
      'The background color may look greener; red blush varies by variety.',
      'A matte finish can occur, but surface sheen varies too.',
    ],
    riper: [
      'The background may shift toward cream or yellow as it ripens.',
      'Judge the base color, not red blush alone; varieties and finishes differ.',
    ],
  },
  {
    name: 'Banana',
    emoji: '🍌',
    lessRipe: ['A mostly green peel and more angular shape often indicate an earlier stage.'],
    riper: [
      'The peel commonly turns yellow; small brown sugar spots can be normal ripening.',
      'Green tips may remain while the banana continues to ripen.',
    ],
  },
  {
    name: 'Tomato',
    emoji: '🍅',
    lessRipe: ['A pale or green cast, including green shoulders, may appear before full color.'],
    riper: [
      'Color often deepens toward the variety’s mature color, which is not always red.',
      'Green shoulders alone do not mean the tomato is defective.',
    ],
  },
] as const

function formatFileSize(bytes: number): string {
  return `${(bytes / (1024 * 1024)).toFixed(2)} MB`
}

function stageMessage(stage: string | undefined, kind: 'image' | 'voice'): string {
  if (stage === 'validating') return 'Preparing your upload…'
  if (stage === 'analyzing') {
    return kind === 'voice'
      ? 'Reading what you said and estimating a quality grade…'
      : 'Checking visible signs and estimating a quality grade…'
  }
  if (stage === 'report') return 'Writing your report…'
  return kind === 'voice'
    ? 'Reading what you said and estimating a quality grade.'
    : 'Checking visible signs and estimating a quality grade.'
}

function stageProgress(stage: string | undefined): number {
  if (stage === 'validating') return 15
  if (stage === 'analyzing') return 55
  if (stage === 'report') return 85
  return 5
}

function formatInspectionDate(value: string): string {
  return new Intl.DateTimeFormat(undefined, {
    dateStyle: 'medium',
    timeStyle: 'short',
  }).format(new Date(value))
}

function downloadPdf(pdf: Blob, inspectionId: string): void {
  const url = URL.createObjectURL(pdf)
  const link = document.createElement('a')
  link.href = url
  link.download = `fieldmark-inspection-${inspectionId}.pdf`
  link.click()
  window.setTimeout(() => URL.revokeObjectURL(url), PDF_URL_REVOKE_DELAY_MS)
}

interface InspectPageProps {
  onApiStatusChange: (status: ApiStatus) => void
}

function InspectPage({ onApiStatusChange }: InspectPageProps) {
  const {
    jobs,
    startImageJob,
    startVoiceJob,
    lastCompleted,
    clearLastCompleted,
    submitError,
    clearSubmitError,
    jobError,
    clearJobError,
  } = useInspectionJobs()
  const cameraInput = useRef<HTMLInputElement>(null)
  const voiceNoteInput = useRef<HTMLInputElement>(null)
  const previewUrlRef = useRef<string | null>(null)
  const [file, setFile] = useState<File | null>(null)
  const [previewUrl, setPreviewUrl] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [result, setResult] = useState<InspectionResult | null>(null)
  const [isDragging, setIsDragging] = useState(false)
  // One flag per action so inspecting a voice note never spins the photo button.
  const [activeInspection, setActiveInspection] = useState<'image' | 'voice' | null>(null)
  const lastInspectionKindRef = useRef<'image' | 'voice'>('image')
  const [history, setHistory] = useState<InspectionHistoryItem[]>([])
  const [historyFilter, setHistoryFilter] = useState<HistoryFilter>('all')
  const [historyError, setHistoryError] = useState<string | null>(null)
  const [isHistoryLoading, setIsHistoryLoading] = useState(true)
  const [isClearingHistory, setIsClearingHistory] = useState(false)
  const [voiceNoteFile, setVoiceNoteFile] = useState<File | null>(null)
  const [voiceAudioUrl, setVoiceAudioUrl] = useState<string | null>(null)
  const [isRecording, setIsRecording] = useState(false)
  const [recordingSeconds, setRecordingSeconds] = useState(0)
  const mediaRecorderRef = useRef<MediaRecorder | null>(null)
  const audioChunksRef = useRef<Blob[]>([])
  const recordingStartedAtRef = useRef<number | null>(null)
  const recordingTimerRef = useRef<number | null>(null)
  const analyserRef = useRef<AnalyserNode | null>(null)
  const audioContextRef = useRef<AudioContext | null>(null)
  const micLevelTimerRef = useRef<number | null>(null)
  const voiceAudioUrlRef = useRef<string | null>(null)
  const recordingTokenRef = useRef(0)
  const activeDeviceLabelRef = useRef<string | null>(null)
  // Distinguish an explicit user choice from our label-less initial guess.
  const userSelectedDeviceRef = useRef(false)
  const [micLevel, setMicLevel] = useState(0)
  const [voiceWarning, setVoiceWarning] = useState<string | null>(null)
  const [audioDevices, setAudioDevices] = useState<MediaDeviceInfo[]>([])
  const [selectedDeviceId, setSelectedDeviceId] = useState<string>('')
  const [activeDeviceLabel, setActiveDeviceLabel] = useState<string | null>(null)
  const [reportActionId, setReportActionId] = useState<string | null>(null)
  const [reportError, setReportError] = useState<string | null>(null)
  // The submit call returns in milliseconds; the UI must keep spinning until
  // the queued job actually finishes, not until the upload request ends.
  const analysisJob = jobs.find((job) => job.status === 'queued' || job.status === 'processing')
  const loadingKind = activeInspection ?? analysisJob?.kind ?? null
  const reportPending = jobs.some((job) => job.status === 'completed' && job.stage === 'report')
  // Never show a previous verdict while a new job is still processing.
  const visibleResult = loadingKind ? null : result
  const [relocationId, setRelocationId] = useState<string | null>(null)
  const [relocationError, setRelocationError] = useState<string | null>(null)
  const [categories, setCategories] = useState<ProductCategory[]>(FALLBACK_CATEGORIES)
  const resultClassification = visibleResult ? classifyInspectionResult(visibleResult) : null
  const isUnsupportedItem = resultClassification === 'unsupported_item'
  const isVoiceOnlyResult = Boolean(visibleResult?.voice_note_transcript)
  const activeCategories = categories.filter((category) => category.is_active)
  const heroCategories = activeCategories.slice(0, HERO_CARD_SLOTS.length)
  const visibleHistory = history.filter((item) => matchesHistoryFilter(item, historyFilter))

  const stopMicMeter = useCallback(() => {
    if (micLevelTimerRef.current) {
      clearInterval(micLevelTimerRef.current)
      micLevelTimerRef.current = null
    }
    analyserRef.current = null
    const context = audioContextRef.current
    audioContextRef.current = null
    if (context && context.state !== 'closed') {
      void context.close().catch(() => {
        // Closing an already-closed context is harmless.
      })
    }
    setMicLevel(0)
  }, [])

  useEffect(
    () => () => {
      if (previewUrlRef.current) URL.revokeObjectURL(previewUrlRef.current)
      if (voiceAudioUrlRef.current) URL.revokeObjectURL(voiceAudioUrlRef.current)
      if (recordingTimerRef.current) clearInterval(recordingTimerRef.current)
      if (micLevelTimerRef.current) clearInterval(micLevelTimerRef.current)
      if (audioContextRef.current && audioContextRef.current.state !== 'closed') {
        void audioContextRef.current.close().catch(() => {})
      }
      if (mediaRecorderRef.current && mediaRecorderRef.current.state !== 'inactive') {
        mediaRecorderRef.current.stop()
      }
    },
    [],
  )

  useEffect(() => {
    let active = true
    listCategories()
      .then((items) => {
        if (active && items.length) setCategories(items)
      })
      .catch(() => {
        // Keep the built-in categories when the category API is unavailable.
      })
    return () => {
      active = false
    }
  }, [])

  const refreshHistory = useCallback(async () => {
    setIsHistoryLoading(true)
    setHistoryError(null)
    try {
      setHistory(await listInspectionHistory())
    } catch (caught: unknown) {
      setHistoryError(
        caught instanceof Error ? caught.message : 'Inspection history could not be loaded.',
      )
    } finally {
      setIsHistoryLoading(false)
    }
  }, [])

  useEffect(() => {
    if (!lastCompleted) return
    // Defer to the next tick so a completed job reopens its result without a
    // synchronous state update during render.
    const timer = window.setTimeout(() => {
      setResult(lastCompleted)
      clearLastCompleted()
      void refreshHistory()
    }, 0)
    return () => window.clearTimeout(timer)
  }, [lastCompleted, clearLastCompleted, refreshHistory])

  useEffect(() => {
    let active = true
    listInspectionHistory()
      .then((items) => {
        if (active) setHistory(items)
      })
      .catch((caught: unknown) => {
        if (active) {
          setHistoryError(
            caught instanceof Error ? caught.message : 'Inspection history could not be loaded.',
          )
        }
      })
      .finally(() => {
        if (active) setIsHistoryLoading(false)
      })
    return () => {
      active = false
    }
  }, [])

  const updateVoiceAudioUrl = useCallback((audioFile: File | null) => {
    if (voiceAudioUrlRef.current) {
      URL.revokeObjectURL(voiceAudioUrlRef.current)
      voiceAudioUrlRef.current = null
    }
    if (audioFile) {
      const url = URL.createObjectURL(audioFile)
      voiceAudioUrlRef.current = url
      setVoiceAudioUrl(url)
    } else {
      setVoiceAudioUrl(null)
    }
  }, [])

  const applyAudioDevices = useCallback((devices: MediaDeviceInfo[]) => {
    const inputs = devices.filter((device) => device.kind === 'audioinput')
    setAudioDevices(inputs)
    setSelectedDeviceId((current) => {
      if (
        userSelectedDeviceRef.current &&
        current &&
        inputs.some((device) => device.deviceId === current)
      ) {
        return current
      }
      return preferredAudioDevice(inputs)?.deviceId ?? ''
    })
  }, [])

  const refreshAudioDevices = useCallback(async () => {
    if (!navigator.mediaDevices?.enumerateDevices) return
    try {
      applyAudioDevices(await navigator.mediaDevices.enumerateDevices())
    } catch {
      // Device labels stay hidden until permission is granted; try again on record.
    }
  }, [applyAudioDevices])

  useEffect(() => {
    let active = true
    navigator.mediaDevices
      ?.enumerateDevices?.()
      .then((devices) => {
        if (active) applyAudioDevices(devices)
      })
      .catch(() => {
        // Labels may be hidden until the user grants microphone permission.
      })
    return () => {
      active = false
    }
  }, [applyAudioDevices])

  const stopRecording = useCallback(() => {
    if (mediaRecorderRef.current && mediaRecorderRef.current.state !== 'inactive') {
      mediaRecorderRef.current.stop()
    }
    stopMicMeter()
    setIsRecording(false)
    if (recordingTimerRef.current) {
      clearInterval(recordingTimerRef.current)
      recordingTimerRef.current = null
    }
  }, [stopMicMeter])

  const startRecording = useCallback(async () => {
    if (activeInspection || isRecording) return
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
      setError('Audio recording is not supported in this browser.')
      return
    }

    let audioContext: AudioContext | null = null
    let stream: MediaStream | null = null
    const token = ++recordingTokenRef.current

    try {
      // Create and resume inside the click handler, before any await, so
      // autoplay policies cannot leave the meter permanently at zero.
      const AudioContextClass =
        window.AudioContext ??
        (window as unknown as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext
      if (AudioContextClass) {
        audioContext = new AudioContextClass()
        audioContextRef.current = audioContext
        if (audioContext.state === 'suspended') {
          await audioContext.resume()
        }
      }

      const requestedId = selectedDeviceId || undefined
      stream = await navigator.mediaDevices.getUserMedia(
        requestedId ? { audio: { deviceId: { exact: requestedId } } } : { audio: true },
      )

      // Labels become available after permission. If the browser handed us a
      // silent virtual device (BlackHole/Teams), re-open the real microphone.
      await refreshAudioDevices()
      const devices = (await navigator.mediaDevices.enumerateDevices()).filter(
        (device) => device.kind === 'audioinput',
      )
      // Once labels are available, re-evaluate the auto choice: the initial
      // label-less guess may have been a silent virtual device.
      const target =
        userSelectedDeviceRef.current && requestedId
          ? devices.find((device) => device.deviceId === requestedId)
          : preferredAudioDevice(devices)
      const currentTrack = stream.getAudioTracks()[0]
      if (target && currentTrack && currentTrack.getSettings().deviceId !== target.deviceId) {
        currentTrack.stop()
        stream = await navigator.mediaDevices.getUserMedia({
          audio: { deviceId: { exact: target.deviceId } },
        })
      }

      // Freeze the final stream so the onstop closure keeps the non-null type.
      const activeStream = stream
      const activeTrack = activeStream.getAudioTracks()[0]
      const label = activeTrack?.label || target?.label || 'the selected microphone'
      activeDeviceLabelRef.current = label
      setActiveDeviceLabel(label)

      // Live input meter, plus an immediate warning when the input is silent.
      if (audioContext) {
        const source = audioContext.createMediaStreamSource(activeStream)
        const analyser = audioContext.createAnalyser()
        analyser.fftSize = 1024
        source.connect(analyser)
        analyserRef.current = analyser
        const data = new Uint8Array(analyser.fftSize)
        let silentMs = 0
        let warnedAboutSilence = false
        setMicLevel(0)
        if (micLevelTimerRef.current) clearInterval(micLevelTimerRef.current)
        micLevelTimerRef.current = window.setInterval(() => {
          analyser.getByteTimeDomainData(data)
          let peak = 0
          for (let index = 0; index < data.length; index += 1) {
            const amplitude = Math.abs(data[index] - 128) / 128
            if (amplitude > peak) peak = amplitude
          }
          setMicLevel(peak)
          silentMs = peak < SILENCE_PEAK_THRESHOLD ? silentMs + 100 : 0
          if (!warnedAboutSilence && silentMs >= 1500) {
            warnedAboutSilence = true
            setVoiceWarning(
              `No signal from ${activeDeviceLabelRef.current ?? 'the selected microphone'}. Choose another microphone above, or check System Settings → Sound → Input.`,
            )
          }
        }, 100)
      }

      let chosenMimeType = 'audio/webm'
      let extension = 'webm'
      if (typeof MediaRecorder !== 'undefined') {
        if (MediaRecorder.isTypeSupported('audio/webm')) {
          chosenMimeType = 'audio/webm'
          extension = 'webm'
        } else if (MediaRecorder.isTypeSupported('audio/mp4')) {
          chosenMimeType = 'audio/mp4'
          extension = 'm4a'
        } else if (MediaRecorder.isTypeSupported('audio/ogg')) {
          chosenMimeType = 'audio/ogg'
          extension = 'ogg'
        } else if (MediaRecorder.isTypeSupported('audio/wav')) {
          chosenMimeType = 'audio/wav'
          extension = 'wav'
        }
      }

      const recorder = new MediaRecorder(
        activeStream,
        chosenMimeType ? { mimeType: chosenMimeType } : undefined,
      )
      mediaRecorderRef.current = recorder
      audioChunksRef.current = []

      recorder.ondataavailable = (event) => {
        if (event.data && event.data.size > 0) {
          audioChunksRef.current.push(event.data)
        }
      }

      recorder.onstop = () => {
        activeStream.getTracks().forEach((track) => track.stop())
        // Ignore a late onstop from an older recording: it must not close the
        // meter belonging to a newer one.
        if (recordingTokenRef.current === token) stopMicMeter()
        activeDeviceLabelRef.current = null
        setActiveDeviceLabel(null)

        const durationMs =
          recordingStartedAtRef.current === null ? 0 : Date.now() - recordingStartedAtRef.current
        recordingStartedAtRef.current = null

        const blobType = chosenMimeType
        const audioBlob = new Blob(audioChunksRef.current, { type: blobType })
        console.info('[voice-note] recording captured', {
          durationMs,
          bytes: audioBlob.size,
          mimeType: blobType,
          device: label,
        })

        // Reject only genuinely empty recordings; real speech in compressed
        // WebM can be just a few KB, so a hard size floor caused false rejections.
        if (durationMs < 500 || audioBlob.size < 512) {
          setError('No audio captured. Check your microphone, speak clearly, and try again.')
          return
        }

        if (audioBlob.size > MAX_VOICE_NOTE_BYTES) {
          setError('Recorded audio exceeds the 25 MB limit.')
          return
        }

        // Decode to WAV. A low peak is a warning, not a block: the transcription
        // service is the final judge of whether the recording contains speech.
        void prepareRecording(audioBlob)
          .then(({ blob, hasSpeech, peak }) => {
            console.info('[voice-note] audio prepared', {
              peak,
              hasSpeech,
              bytes: blob.size,
              mimeType: blob.type,
            })
            const isWav = blob.type === 'audio/wav'
            const recordedFile = new File(
              [blob],
              `voice-recording-${Date.now()}.${isWav ? 'wav' : extension}`,
              { type: blob.type || blobType },
            )

            if (previewUrlRef.current) URL.revokeObjectURL(previewUrlRef.current)
            previewUrlRef.current = null
            setPreviewUrl(null)
            setFile(null)
            setResult(null)
            setReportError(null)
            setError(null)

            setVoiceNoteFile(recordedFile)
            updateVoiceAudioUrl(recordedFile)
            setVoiceWarning((current) =>
              hasSpeech
                ? null
                : (current ??
                  'The recording sounded very quiet. If the transcript is empty, check your microphone input and try again.'),
            )
          })
          .catch(() => {
            setError('Could not process the recording. Please try again.')
          })
      }

      recorder.start(250)
      setIsRecording(true)
      setRecordingSeconds(0)
      setVoiceWarning(null)
      setError(null)

      const startTime = Date.now()
      recordingStartedAtRef.current = startTime
      if (recordingTimerRef.current) clearInterval(recordingTimerRef.current)
      recordingTimerRef.current = window.setInterval(() => {
        setRecordingSeconds(Math.floor((Date.now() - startTime) / 1000))
      }, 500)
    } catch (caught: unknown) {
      stream?.getTracks().forEach((track) => track.stop())
      if (audioContext) {
        if (audioContextRef.current === audioContext) stopMicMeter()
        else if (audioContext.state !== 'closed') void audioContext.close().catch(() => {})
      }
      setIsRecording(false)
      if (recordingTimerRef.current) clearInterval(recordingTimerRef.current)
      const message = caught instanceof Error ? caught.message : 'Microphone access denied.'
      setError(`Microphone error: ${message}`)
    }
  }, [
    activeInspection,
    isRecording,
    selectedDeviceId,
    refreshAudioDevices,
    updateVoiceAudioUrl,
    stopMicMeter,
  ])

  const selectImage = useCallback(
    (selected: File) => {
      if (!ACCEPTED_IMAGE_TYPES.has(selected.type)) {
        if (previewUrlRef.current) URL.revokeObjectURL(previewUrlRef.current)
        previewUrlRef.current = null
        setPreviewUrl(null)
        setFile(null)
        setResult(null)
        setError('Choose a JPEG, PNG, or WebP image to continue.')
        return
      }
      if (selected.size > MAX_IMAGE_BYTES) {
        if (previewUrlRef.current) URL.revokeObjectURL(previewUrlRef.current)
        previewUrlRef.current = null
        setPreviewUrl(null)
        setFile(null)
        setResult(null)
        setError('This image is over 10 MB. Choose a smaller file.')
        return
      }
      if (previewUrlRef.current) URL.revokeObjectURL(previewUrlRef.current)
      previewUrlRef.current = URL.createObjectURL(selected)
      setPreviewUrl(previewUrlRef.current)
      setFile(selected)
      setResult(null)
      if (isRecording) stopRecording()
      setVoiceNoteFile(null)
      updateVoiceAudioUrl(null)
      setVoiceWarning(null)
      if (voiceNoteInput.current) voiceNoteInput.current.value = ''
      setReportError(null)
      setError(null)
    },
    [isRecording, stopRecording, updateVoiceAudioUrl],
  )

  const handleFileChange = (event: ChangeEvent<HTMLInputElement>) => {
    const selected = event.currentTarget.files?.[0]
    if (selected) selectImage(selected)
    event.currentTarget.value = ''
  }

  const handleDrop = (event: DragEvent<HTMLLabelElement>) => {
    event.preventDefault()
    setIsDragging(false)
    const droppedFile = event.dataTransfer.files[0]
    if (droppedFile) selectImage(droppedFile)
  }

  const handleVoiceNoteChange = (event: ChangeEvent<HTMLInputElement>) => {
    const selected = event.currentTarget.files?.[0]
    if (!selected) return
    const extension = selected.name.split('.').pop()?.toLowerCase() ?? ''
    // Some browsers report .m4a as audio/x-m4a or leave the type blank, so
    // fall back to the extension before rejecting a valid file.
    if (
      !ACCEPTED_VOICE_NOTE_TYPES.has(selected.type) &&
      !ACCEPTED_VOICE_NOTE_EXTENSIONS.has(extension)
    ) {
      setError('Choose an audio file (MP3, M4A, WAV, MP4, FLAC, WebM, OGG).')
      event.currentTarget.value = ''
      return
    }
    if (selected.size > MAX_VOICE_NOTE_BYTES) {
      setError('This audio file is over 25 MB. Choose a smaller file.')
      event.currentTarget.value = ''
      return
    }
    if (isRecording) stopRecording()
    // Clear image state
    if (previewUrlRef.current) URL.revokeObjectURL(previewUrlRef.current)
    previewUrlRef.current = null
    setPreviewUrl(null)
    setFile(null)
    setResult(null)
    setReportError(null)
    setError(null)
    setVoiceNoteFile(selected)
    updateVoiceAudioUrl(selected)
    setVoiceWarning(null)
    event.currentTarget.value = ''
  }

  const clearVoiceNote = () => {
    if (isRecording) stopRecording()
    setVoiceNoteFile(null)
    updateVoiceAudioUrl(null)
    setVoiceWarning(null)
    if (voiceNoteInput.current) voiceNoteInput.current.value = ''
    setError(null)
  }

  const handleInspect = async (kind: 'image' | 'voice') => {
    if (loadingKind) return
    if (kind === 'image' && !file) return
    if (kind === 'voice' && !voiceNoteFile) return

    lastInspectionKindRef.current = kind
    setActiveInspection(kind)
    setError(null)
    setResult(null)
    setReportError(null)
    clearSubmitError()
    clearJobError()
    try {
      if (kind === 'voice') await startVoiceJob(voiceNoteFile!)
      else await startImageJob(file!)
      onApiStatusChange('connected')
    } catch (caught: unknown) {
      onApiStatusChange(caught instanceof TypeError ? 'offline' : 'connected')
    } finally {
      setActiveInspection(null)
    }
  }

  const clearImage = () => {
    if (previewUrlRef.current) URL.revokeObjectURL(previewUrlRef.current)
    previewUrlRef.current = null
    setPreviewUrl(null)
    setFile(null)
    setResult(null)
    if (isRecording) stopRecording()
    setVoiceNoteFile(null)
    updateVoiceAudioUrl(null)
    if (voiceNoteInput.current) voiceNoteInput.current.value = ''
    setReportError(null)
    setError(null)
  }

  const handleRelocateDefect = async (inspectionId: string) => {
    if (relocationId) return
    setRelocationId(inspectionId)
    setRelocationError(null)
    try {
      const updated = await relocateDefect(inspectionId)
      setResult((current) =>
        current && current.inspection_id === inspectionId
          ? { ...updated, report_available: current.report_available }
          : current,
      )
      void refreshHistory()
    } catch (caught: unknown) {
      setRelocationError(
        caught instanceof Error ? caught.message : 'Could not locate the defect area.',
      )
    } finally {
      setRelocationId(null)
    }
  }

  const handleDownloadReport = async (inspectionId: string) => {
    if (reportActionId) return
    setReportActionId(inspectionId)
    setReportError(null)
    try {
      const pdf = await downloadInspectionReport(inspectionId)
      downloadPdf(pdf, inspectionId)
    } catch (caught: unknown) {
      setReportError(
        caught instanceof Error ? caught.message : 'PDF report could not be downloaded.',
      )
    } finally {
      setReportActionId(null)
    }
  }

  const handleClearHistory = async () => {
    if (isClearingHistory || loadingKind || reportActionId || history.length === 0) return
    const confirmed = window.confirm(
      'Permanently delete all saved inspections, original photos, and PDF reports? This cannot be undone.',
    )
    if (!confirmed) return
    setIsClearingHistory(true)
    setHistoryError(null)
    setReportError(null)
    try {
      await clearInspectionHistory()
      setHistory([])
      setResult(null)
      if (isRecording) stopRecording()
      setVoiceNoteFile(null)
      updateVoiceAudioUrl(null)
      if (voiceNoteInput.current) voiceNoteInput.current.value = ''
    } catch (caught: unknown) {
      setHistoryError(
        caught instanceof Error ? caught.message : 'Inspection history could not be cleared.',
      )
    } finally {
      setIsClearingHistory(false)
    }
  }

  return (
    <>
      <section className="hero">
        <div className="hero-copy">
          <div className="eyebrow">
            <span className="eyebrow-dot" />A FRESH LOOK AT QUALITY
          </div>
          <h1>
            Know your harvest
            <br />
            <span>at a glance.</span>
          </h1>
          <p className="hero-description">
            A quick visual check for the products you grow. Add a photo to see a quality grade,
            visible defects, and a practical next step.
          </p>
          <div className="hero-footnote">
            <ShieldCheck size={16} />
            <span>Indicative results. Always inspect manually when in doubt.</span>
          </div>
        </div>
        <div
          className="hero-art"
          aria-label={`Inspection categories: ${heroCategories
            .map((category) => category.display_name)
            .join(', ')}`}
        >
          <div className="orbit orbit-one" />
          <div className="orbit orbit-two" />
          {heroCategories.map((category, index) => (
            <div className={`fruit-card ${HERO_CARD_SLOTS[index]}`} key={category.slug}>
              <span className="fruit-emoji">{category.emoji || '🌱'}</span>
              {index === 0 && (
                <span className="fruit-label">{category.display_name.toUpperCase()}</span>
              )}
            </div>
          ))}
          <div className="art-stamp">
            <Sparkles size={16} />
            <span>GROWN WITH CARE</span>
          </div>
        </div>
      </section>

      <section className="inspection-grid" aria-label="Product inspection">
        <section className="panel upload-panel">
          <div className="panel-heading">
            <div>
              <p className="section-kicker">
                01 <span>/</span> IMAGE INPUT
              </p>
              <h2>Start with a photo</h2>
              <p className="panel-subtitle">One product, clearly in frame.</p>
            </div>
            <span className="heading-icon">
              <ImagePlus size={19} />
            </span>
          </div>

          <label
            className={`upload-zone ${isDragging ? 'upload-zone-dragging' : ''} ${previewUrl ? 'has-preview' : ''}`}
            htmlFor="product-file"
            onDragOver={(event) => {
              event.preventDefault()
              setIsDragging(true)
            }}
            onDragLeave={() => setIsDragging(false)}
            onDrop={handleDrop}
          >
            <input
              id="product-file"
              className="visually-hidden"
              type="file"
              accept={ACCEPTED_IMAGE_EXTENSIONS}
              onChange={handleFileChange}
            />
            {previewUrl ? (
              <div className="image-preview">
                <div className="image-canvas">
                  <img src={previewUrl} alt="Selected product preview" />
                </div>
                <span className="preview-change">
                  <RefreshCw size={14} /> Replace photo
                </span>
              </div>
            ) : (
              <div className="upload-prompt">
                <span className="upload-icon">
                  <Upload size={23} />
                </span>
                <span className="upload-title">Drop your photo here</span>
                <span className="upload-subtitle">
                  or <strong>browse files</strong> on your device
                </span>
                <span className="upload-formats">
                  JPEG · PNG · WEBP <i /> UP TO 10 MB
                </span>
              </div>
            )}
          </label>

          {file ? (
            <div className="file-row">
              <span className="file-icon">
                <ImagePlus size={17} />
              </span>
              <span className="file-info">
                <strong>{file.name}</strong>
                <small>{formatFileSize(file.size)}</small>
              </span>
              <button
                className="icon-button"
                type="button"
                onClick={clearImage}
                aria-label="Remove image"
              >
                <X size={17} />
              </button>
            </div>
          ) : (
            <div className="capture-row">
              <span className="capture-note">Prefer a live photo?</span>
              <input
                ref={cameraInput}
                type="file"
                accept={ACCEPTED_IMAGE_EXTENSIONS}
                capture="environment"
                hidden
                onChange={handleFileChange}
              />
              <button
                className="camera-button"
                type="button"
                onClick={() => cameraInput.current?.click()}
              >
                <Camera size={16} /> Use camera
              </button>
            </div>
          )}

          <div className="supported-row">
            <span>SUPPORTED</span>
            {activeCategories.slice(0, 6).map((category) => (
              <div className="supported-fruit" key={category.slug}>
                <i>{category.emoji || '🌱'}</i> {category.display_name}
              </div>
            ))}
          </div>

          <button
            className="inspect-button"
            type="button"
            onClick={() => void handleInspect('image')}
            disabled={!file || loadingKind !== null}
          >
            {loadingKind === 'image' ? (
              <>
                <LoaderCircle className="spin" size={18} /> Inspecting photo
              </>
            ) : (
              <>
                Inspect product <ArrowRight size={18} />
              </>
            )}
          </button>
          <p className="button-caption">
            <Sparkles size={13} /> AI-assisted visual grading
          </p>
          <p className="upload-retention-note">
            Original photos are stored with inspection history so PDF reports can include them.
          </p>
        </section>

        <section className="panel voice-panel">
          <div className="panel-heading">
            <div>
              <p className="section-kicker">
                01b <span>/</span> VOICE NOTE INPUT
              </p>
              <h2>Or record / upload a voice note</h2>
              <p className="panel-subtitle">Record your voice directly or upload an audio file.</p>
            </div>
            <span className="heading-icon">
              <Mic size={19} />
            </span>
          </div>

          <div className="voice-record-card">
            <div className="voice-record-status">
              {isRecording ? (
                <div className="recording-indicator">
                  <span className="recording-pulse" />
                  <strong>Recording voice note…</strong>
                  <span className="recording-timer">
                    {Math.floor(recordingSeconds / 60)
                      .toString()
                      .padStart(2, '0')}
                    :{(recordingSeconds % 60).toString().padStart(2, '0')}
                  </span>
                </div>
              ) : (
                <span className="capture-note">
                  {voiceNoteFile
                    ? 'Voice note ready for inspection'
                    : 'Speak directly about product quality or defects'}
                </span>
              )}
            </div>

            <div className="mic-device-row">
              <label className="mic-device-label" htmlFor="mic-device">
                Microphone
                <select
                  id="mic-device"
                  className="mic-device-select"
                  value={selectedDeviceId}
                  onChange={(event) => {
                    userSelectedDeviceRef.current = true
                    setSelectedDeviceId(event.target.value)
                  }}
                  disabled={isRecording || loadingKind !== null}
                >
                  {audioDevices.length === 0 ? (
                    <option value="">Default microphone</option>
                  ) : (
                    audioDevices.map((device) => (
                      <option key={device.deviceId || 'default'} value={device.deviceId}>
                        {device.label || `Microphone ${device.deviceId.slice(0, 6)}`}
                      </option>
                    ))
                  )}
                </select>
              </label>
              {activeDeviceLabel && (
                <span className="mic-device-active">Using: {activeDeviceLabel}</span>
              )}
            </div>

            <div className="record-actions">
              {isRecording ? (
                <button
                  className="camera-button recording-btn active"
                  type="button"
                  onClick={stopRecording}
                >
                  <Square size={16} /> Stop recording
                </button>
              ) : (
                <button
                  className="camera-button recording-btn"
                  type="button"
                  onClick={() => void startRecording()}
                  disabled={loadingKind !== null}
                >
                  <Mic size={16} /> Record in browser
                </button>
              )}
            </div>

            {isRecording && (
              <div className="mic-level" role="status" aria-label="Microphone input level">
                <span className="mic-level-label">Mic level</span>
                <span className="mic-level-track">
                  <span
                    className={`mic-level-fill ${micLevel > 0.05 ? 'is-live' : ''}`}
                    style={{ width: `${Math.min(100, Math.round(micLevel * 400))}%` }}
                  />
                </span>
              </div>
            )}
          </div>

          <label className="upload-zone voice-upload-zone" htmlFor="voice-note-file">
            <input
              id="voice-note-file"
              className="visually-hidden"
              ref={voiceNoteInput}
              type="file"
              accept="audio/*"
              onChange={handleVoiceNoteChange}
            />
            <div className="upload-prompt">
              <span className="upload-icon">
                <Mic size={23} />
              </span>
              <span className="upload-title">Drop an audio file here</span>
              <span className="upload-subtitle">
                or <strong>browse audio files</strong> on your device
              </span>
              <span className="upload-formats">
                MP3 · M4A · WAV · MP4 · FLAC · WEBM · OGG <i /> UP TO 25 MB
              </span>
            </div>
          </label>

          {voiceNoteFile && (
            <div className="file-row">
              <span className="file-icon">
                <Mic size={17} />
              </span>
              <span className="file-info">
                <strong>{voiceNoteFile.name}</strong>
                <small>{formatFileSize(voiceNoteFile.size)}</small>
                {voiceAudioUrl && (
                  <audio
                    className="audio-preview-player"
                    controls
                    src={voiceAudioUrl}
                    style={{ marginTop: '8px', width: '100%', maxWidth: '280px', height: '34px' }}
                  />
                )}
              </span>
              <button
                className="icon-button"
                type="button"
                onClick={clearVoiceNote}
                aria-label="Remove voice note"
              >
                <X size={17} />
              </button>
            </div>
          )}

          {voiceWarning && (
            <p className="voice-warning" role="status">
              {voiceWarning}
            </p>
          )}

          <button
            className="inspect-button"
            type="button"
            onClick={() => void handleInspect('voice')}
            disabled={!voiceNoteFile || loadingKind !== null || isRecording}
          >
            {loadingKind === 'voice' ? (
              <>
                <LoaderCircle className="spin" size={18} /> Analyzing voice note
              </>
            ) : (
              <>
                Inspect voice note <ArrowRight size={18} />
              </>
            )}
          </button>
          <p className="button-caption">
            <Sparkles size={13} /> Grades the product you describe · no photo needed
          </p>
        </section>

        <section
          className={`result-panel ${visibleResult ? `result-${visibleResult.verdict}` : ''}`}
          aria-live="polite"
        >
          <div className="result-panel-top">
            <div>
              <p className="section-kicker">
                02 <span>/</span> INSPECTION RESULT
              </p>
              <h2>{visibleResult ? 'Your quality readout' : 'Your result, right here.'}</h2>
            </div>
            {visibleResult && (
              <span
                className={`verdict-pill ${
                  isUnsupportedItem ? 'verdict-needs_review' : `verdict-${visibleResult.verdict}`
                }`}
              >
                {isUnsupportedItem ? 'Unsupported item' : VERDICT_LABELS[visibleResult.verdict]}
              </span>
            )}
          </div>

          {error || submitError || jobError ? (
            <div className="result-message error-message">
              <span className="message-icon">
                <CircleAlert size={22} />
              </span>
              <h3>Inspection didn’t finish</h3>
              <p>{submitError || jobError || error}</p>
              <button
                type="button"
                className="retry-button"
                onClick={() => void handleInspect(lastInspectionKindRef.current)}
                disabled={loadingKind !== null}
              >
                Try again <RefreshCw size={15} />
              </button>
            </div>
          ) : loadingKind ? (
            <div className="result-message loading-message">
              <span className="scan-loader">
                <ScanLine size={30} />
              </span>
              <h3>Looking a little closer…</h3>
              <p>{stageMessage(analysisJob?.stage, loadingKind)}</p>
              <div className="loading-track">
                <span style={{ width: `${stageProgress(analysisJob?.stage)}%` }} />
              </div>
            </div>
          ) : visibleResult ? (
            <div className="result-content">
              {isVoiceOnlyResult && (
                <div className="voice-result-intro">
                  <span className="voice-result-icon">
                    <Mic size={22} />
                  </span>
                  <span>
                    <strong>Graded from your voice note</strong>
                    <small>
                      This grade is based on what you said, not a photo. Inspect the product
                      manually before acting.
                    </small>
                  </span>
                </div>
              )}
              <div className="grade-row">
                {!isUnsupportedItem && (
                  <div className="grade-block">
                    <span className="result-label">QUALITY GRADE</span>
                    <strong
                      className={`grade-value grade-${visibleResult.grade === 'A' ? 'a' : visibleResult.grade === 'B' ? 'b' : 'review'}`}
                    >
                      {visibleResult.grade === 'Needs manual review' ? '—' : visibleResult.grade}
                    </strong>
                  </div>
                )}
                <div className="product-identified">
                  <span className="identified-emoji">
                    {productEmoji(visibleResult, categories, isUnsupportedItem)}
                  </span>
                  <span>
                    <small>{isUnsupportedItem ? 'DETECTED' : 'IDENTIFIED AS'}</small>
                    <strong>{productName(visibleResult, categories)}</strong>
                  </span>
                </div>
              </div>

              {resultClassification === 'out_of_categories' && (
                <p className="category-notice" role="status">
                  This product is {productName(visibleResult, categories)} - from out of inspection
                  products
                </p>
              )}
              {isUnsupportedItem && (
                <p className="category-notice" role="status">
                  Unsupported item
                </p>
              )}

              {!isVoiceOnlyResult && (
                <section className="photo-evidence" aria-label="Photo evidence">
                  <div className="photo-evidence-heading">
                    <span className="result-label">PHOTO EVIDENCE</span>
                    {visibleResult.bbox && <span>Reported defect area marked</span>}
                  </div>
                  <div
                    className={`photo-evidence-grid ${visibleResult.bbox ? 'has-annotated' : ''}`}
                  >
                    <figure className="photo-figure">
                      <img
                        src={previewUrl ?? inspectionOriginalUrl(visibleResult.inspection_id)}
                        alt="Original uploaded product photo"
                      />
                      <figcaption>Original photo</figcaption>
                    </figure>
                    {visibleResult.bbox && (
                      <figure className="photo-figure">
                        <img
                          src={inspectionAnnotatedUrl(visibleResult.inspection_id)}
                          alt="Product photo with the reported defect area marked"
                        />
                        <figcaption>Reported defect area</figcaption>
                      </figure>
                    )}
                  </div>
                  {!visibleResult.bbox &&
                    !isUnsupportedItem &&
                    (visibleResult.defect_types.length > 0 || visibleResult.grade === 'Reject') && (
                      <div className="relocate-action">
                        <button
                          className="history-report-button"
                          type="button"
                          onClick={() => void handleRelocateDefect(visibleResult.inspection_id)}
                          disabled={relocationId !== null}
                        >
                          {relocationId === visibleResult.inspection_id ? (
                            <LoaderCircle className="spin" size={14} />
                          ) : (
                            <ScanLine size={14} />
                          )}
                          Find defect area
                        </button>
                        {relocationError && (
                          <p className="report-error" role="alert">
                            {relocationError}
                          </p>
                        )}
                      </div>
                    )}
                </section>
              )}

              {!isUnsupportedItem && (
                <>
                  <div className="details-grid">
                    <div className="detail-cell">
                      <span className="result-label">
                        {isVoiceOnlyResult ? 'REPORTED DEFECTS' : 'VISIBLE DEFECTS'}
                      </span>
                      {visibleResult.defect_types.length ? (
                        <div className="defect-tags">
                          {visibleResult.defect_types.map((defect) => (
                            <span className="defect-tag" key={defect}>
                              {defect}
                            </span>
                          ))}
                        </div>
                      ) : (
                        <strong className="detail-value">
                          <Check size={15} /> No {isVoiceOnlyResult ? 'reported' : 'visible'}{' '}
                          defects
                        </strong>
                      )}
                    </div>
                    <div className="detail-cell">
                      <span className="result-label">SEVERITY</span>
                      <strong className={`severity-value severity-${visibleResult.severity}`}>
                        {visibleResult.severity}
                      </strong>
                    </div>
                  </div>

                  <section className="verdict-notes" aria-label="Decision basis">
                    <h3>Why this verdict?</h3>
                    <ul>
                      {visibleResult.verdict_notes.map((note) => (
                        <li key={note}>{note}</li>
                      ))}
                    </ul>
                  </section>

                  <div className="action-card">
                    <span className="action-icon">
                      <ArrowRight size={17} />
                    </span>
                    <span>
                      <small>RECOMMENDED NEXT STEP</small>
                      <strong>{visibleResult.recommended_action}</strong>
                    </span>
                  </div>
                </>
              )}
              {visibleResult.voice_note_transcript && (
                <section className="transcript-section" aria-label="Voice note transcript">
                  <span className="result-label">VOICE NOTE TRANSCRIPT</span>
                  <p className="transcript-text">“{visibleResult.voice_note_transcript}”</p>
                </section>
              )}
              <p className="result-disclaimer">{visibleResult.disclaimer}</p>
              <p className="inspection-id">
                INSPECTION {visibleResult.inspection_id.slice(0, 8).toUpperCase()}
              </p>
              <section className="report-controls" aria-label="PDF inspection report">
                <div>
                  <h3>Inspection report</h3>
                  <p>
                    {isUnsupportedItem
                      ? 'This item is outside the inspection categories, so no PDF report was created.'
                      : visibleResult.report_available
                        ? isVoiceOnlyResult
                          ? 'Your PDF is ready with the grade and transcript from your voice note.'
                          : 'Your PDF is ready.'
                        : reportPending
                          ? 'Your PDF is being prepared in the background.'
                          : 'The PDF could not be created for this inspection.'}
                  </p>
                </div>
                {visibleResult.report_available && !isUnsupportedItem && (
                  <button
                    className="report-button"
                    type="button"
                    onClick={() => void handleDownloadReport(visibleResult.inspection_id)}
                    disabled={reportActionId !== null}
                  >
                    {reportActionId === visibleResult.inspection_id ? (
                      <>
                        <LoaderCircle className="spin" size={16} /> Preparing PDF
                      </>
                    ) : (
                      <>
                        <Download size={16} /> Download PDF
                      </>
                    )}
                  </button>
                )}
                {!visibleResult.report_available && (
                  <p className="report-unavailable">
                    {reportPending ? 'Preparing PDF…' : 'Report unavailable.'}
                  </p>
                )}
                {reportError && (
                  <p className="report-error" role="alert">
                    {reportError}
                  </p>
                )}
              </section>
            </div>
          ) : (
            <div className="result-message empty-message">
              <div className="empty-visual">
                <span className="empty-ring empty-ring-one" />
                <span className="empty-ring empty-ring-two" />
                <span className="empty-scan">
                  <ScanLine size={30} />
                </span>
                <span className="empty-check">
                  <Check size={15} />
                </span>
              </div>
              <h3>Good decisions start with a closer look.</h3>
              <p>Your grade, visible defects, and next step will appear here after inspection.</p>
              <div className="empty-tip">
                <Sparkles size={14} /> Clear light makes for a clearer read.
              </div>
            </div>
          )}
        </section>
      </section>

      <section className="history-section" aria-label="Recent inspections">
        <div className="history-heading">
          <div>
            <p className="section-kicker">
              03 <span>/</span> SAVED INSPECTIONS
            </p>
            <h2>Recent history</h2>
          </div>
          <div className="history-actions">
            <button
              className="history-refresh"
              type="button"
              onClick={() => void refreshHistory()}
              disabled={isHistoryLoading || isClearingHistory}
            >
              <RefreshCw size={15} className={isHistoryLoading ? 'spin' : ''} />
              Refresh
            </button>
            <button
              className="history-clear"
              type="button"
              onClick={() => void handleClearHistory()}
              disabled={
                history.length === 0 ||
                isHistoryLoading ||
                isClearingHistory ||
                loadingKind !== null ||
                reportActionId !== null
              }
            >
              {isClearingHistory ? (
                <>
                  <LoaderCircle className="spin" size={15} /> Clearing
                </>
              ) : (
                <>
                  <Trash2 size={15} /> Clear history
                </>
              )}
            </button>
          </div>
        </div>
        {history.length > 0 && (
          <div className="history-filters" role="group" aria-label="Filter saved inspections">
            {HISTORY_FILTERS.map(({ value, label }) => (
              <button
                key={value}
                type="button"
                className={`history-filter-chip ${historyFilter === value ? 'is-active' : ''}`}
                aria-pressed={historyFilter === value}
                onClick={() => setHistoryFilter(value)}
              >
                {label}
                <span>{history.filter((item) => matchesHistoryFilter(item, value)).length}</span>
              </button>
            ))}
          </div>
        )}
        {historyError ? (
          <div className="history-message history-error">
            <p>{historyError}</p>
            <button type="button" onClick={() => void refreshHistory()}>
              Try again
            </button>
          </div>
        ) : isHistoryLoading && history.length === 0 ? (
          <p className="history-message">Loading saved inspections…</p>
        ) : history.length === 0 ? (
          <div className="history-empty">
            <Clock3 size={20} />
            <p>Your completed inspections will be saved here.</p>
          </div>
        ) : visibleHistory.length === 0 ? (
          <div className="history-empty">
            <Clock3 size={20} />
            <p>No inspections match this filter. Choose another filter to see them.</p>
          </div>
        ) : (
          <div className="history-list">
            {visibleHistory.map((item) => {
              const classification = classifyInspectionResult(item)
              const unsupported = classification === 'unsupported_item'
              return (
                <article className="history-item" key={item.inspection_id}>
                  {item.voice_note_transcript ? (
                    <span
                      className="history-thumbnail history-voice-thumbnail"
                      aria-label="Voice note inspection"
                    >
                      <Mic size={22} />
                    </span>
                  ) : (
                    <img
                      className="history-thumbnail"
                      src={inspectionThumbnailUrl(item.inspection_id)}
                      alt={`${productName(item, categories)} inspection`}
                    />
                  )}
                  <div className="history-item-copy">
                    <strong>{productName(item, categories)}</strong>
                    <span>{formatInspectionDate(item.created_at)}</span>
                    {classification === 'out_of_categories' && (
                      <small className="history-category-note">Not in inspection categories</small>
                    )}
                  </div>
                  <div className="history-item-result">
                    {!unsupported && (
                      <>
                        <span className={`verdict-pill verdict-${item.verdict}`}>
                          {VERDICT_LABELS[item.verdict]}
                        </span>
                        <strong>
                          {item.grade === 'Needs manual review' ? 'Review' : item.grade}
                        </strong>
                      </>
                    )}
                    {unsupported ? (
                      <span className="history-unsupported">Unsupported item</span>
                    ) : item.report_available ? (
                      <button
                        className="history-report-button"
                        type="button"
                        onClick={() => void handleDownloadReport(item.inspection_id)}
                        disabled={reportActionId !== null}
                      >
                        {reportActionId === item.inspection_id ? (
                          <LoaderCircle className="spin" size={14} />
                        ) : (
                          <>
                            <Download size={14} /> PDF
                          </>
                        )}
                      </button>
                    ) : (
                      <span
                        className="history-report-unavailable"
                        title="No PDF report is available"
                      >
                        Report unavailable
                      </span>
                    )}
                  </div>
                </article>
              )
            })}
            {reportError && (
              <p className="report-error" role="alert">
                {reportError}
              </p>
            )}
          </div>
        )}
      </section>

      <section className="how-it-works" aria-label="How it works">
        <div className="how-heading">
          <p className="section-kicker">A SIMPLE THREE-STEP CHECK</p>
          <span>From photo to practical next step.</span>
        </div>
        <div className="steps">
          <div className="step-card">
            <span className="step-number">01</span>
            <span className="step-icon">
              <ImagePlus size={19} />
            </span>
            <strong>Choose a photo</strong>
            <p>Use a clear image with one product in view.</p>
          </div>
          <div className="step-connector">
            <ArrowRight size={17} />
          </div>
          <div className="step-card">
            <span className="step-number">02</span>
            <span className="step-icon">
              <ScanLine size={19} />
            </span>
            <strong>Review the signs</strong>
            <p>The model looks for visible quality issues and their location.</p>
          </div>
          <div className="step-connector">
            <ArrowRight size={17} />
          </div>
          <div className="step-card">
            <span className="step-number">03</span>
            <span className="step-icon">
              <Check size={19} />
            </span>
            <strong>Choose your next step</strong>
            <p>Use the grade as a guide, and inspect manually when unsure.</p>
          </div>
        </div>
      </section>

      <details className="ripeness-guide">
        <summary>
          <span>
            <span className="section-kicker">A QUICK VISUAL REFERENCE</span>
            <strong>Ripeness cues for your products</strong>
          </span>
          <span className="ripeness-summary-action">Show / hide</span>
        </summary>
        <p className="ripeness-disclaimer">
          These are visual hints, not guarantees. Variety, lighting, and storage change appearance;
          being unripe alone does not mean fruit is spoiled or defective.
        </p>
        <div className="ripeness-grid">
          {RIPENESS_GUIDE.map((fruit) => (
            <article className="ripeness-card" key={fruit.name}>
              <h3>
                <span aria-hidden="true">{fruit.emoji}</span> {fruit.name}
              </h3>
              <div className="ripeness-cue-group">
                <h4>Less ripe signs</h4>
                <ul>
                  {fruit.lessRipe.map((cue) => (
                    <li key={cue}>{cue}</li>
                  ))}
                </ul>
              </div>
              <div className="ripeness-cue-group">
                <h4>Riper signs</h4>
                <ul>
                  {fruit.riper.map((cue) => (
                    <li key={cue}>{cue}</li>
                  ))}
                </ul>
              </div>
            </article>
          ))}
        </div>
      </details>
    </>
  )
}

export default InspectPage
