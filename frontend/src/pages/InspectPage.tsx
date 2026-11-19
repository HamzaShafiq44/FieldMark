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
} from 'lucide-react'
import { useCallback, useEffect, useRef, useState, type ChangeEvent, type DragEvent } from 'react'
import {
  ApiError,
  classifyInspectionResult,
  clearInspectionHistory,
  type Verdict,
  createInspectionReport,
  downloadInspectionReport,
  inspectionThumbnailUrl,
  inspectProduce,
  listInspectionHistory,
  type ApiStatus,
  type InspectionHistoryItem,
  type InspectionResult,
} from '../services/api'
import { listCategories, type ProductCategory } from '../services/categories'

const MAX_IMAGE_BYTES = 10 * 1024 * 1024
const MAX_VOICE_NOTE_BYTES = 25 * 1024 * 1024
const PDF_URL_REVOKE_DELAY_MS = 1_000
const ACCEPTED_IMAGE_TYPES = new Set(['image/jpeg', 'image/png', 'image/webp'])
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
  const cameraInput = useRef<HTMLInputElement>(null)
  const voiceNoteInput = useRef<HTMLInputElement>(null)
  const previewUrlRef = useRef<string | null>(null)
  const [file, setFile] = useState<File | null>(null)
  const [previewUrl, setPreviewUrl] = useState<string | null>(null)
  const [result, setResult] = useState<InspectionResult | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [isDragging, setIsDragging] = useState(false)
  const [isLoading, setIsLoading] = useState(false)
  const [history, setHistory] = useState<InspectionHistoryItem[]>([])
  const [historyFilter, setHistoryFilter] = useState<HistoryFilter>('all')
  const [historyError, setHistoryError] = useState<string | null>(null)
  const [isHistoryLoading, setIsHistoryLoading] = useState(true)
  const [isClearingHistory, setIsClearingHistory] = useState(false)
  const [voiceNoteFile, setVoiceNoteFile] = useState<File | null>(null)
  const [reportActionId, setReportActionId] = useState<string | null>(null)
  const [reportError, setReportError] = useState<string | null>(null)
  const [categories, setCategories] = useState<ProductCategory[]>(FALLBACK_CATEGORIES)
  const resultClassification = result ? classifyInspectionResult(result) : null
  const isUnsupportedItem = resultClassification === 'unsupported_item'
  const activeCategories = categories.filter((category) => category.is_active)
  const heroCategories = activeCategories.slice(0, HERO_CARD_SLOTS.length)
  const visibleHistory = history.filter((item) => matchesHistoryFilter(item, historyFilter))

  useEffect(
    () => () => {
      if (previewUrlRef.current) URL.revokeObjectURL(previewUrlRef.current)
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

  const selectImage = useCallback((selected: File) => {
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
    setVoiceNoteFile(null)
    if (voiceNoteInput.current) voiceNoteInput.current.value = ''
    setReportError(null)
    setError(null)
  }, [])

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

  const handleInspect = async () => {
    if (!file || isLoading) return

    setIsLoading(true)
    setError(null)
    setResult(null)
    setVoiceNoteFile(null)
    if (voiceNoteInput.current) voiceNoteInput.current.value = ''
    setReportError(null)
    try {
      const inspection = await inspectProduce(file)
      setResult(inspection)
      onApiStatusChange('connected')
      void refreshHistory()
    } catch (caught: unknown) {
      if (caught instanceof TypeError) {
        onApiStatusChange('offline')
        setError('Could not reach the API. Make sure the backend is running, then try again.')
      } else if (caught instanceof ApiError && caught.status === 502) {
        onApiStatusChange('connected')
        setError(
          'The vision service could not complete this inspection. Check model availability, API limits, and try again.',
        )
      } else {
        onApiStatusChange('connected')
        setError(caught instanceof Error ? caught.message : 'Inspection failed. Please try again.')
      }
    } finally {
      setIsLoading(false)
    }
  }

  const clearImage = () => {
    if (previewUrlRef.current) URL.revokeObjectURL(previewUrlRef.current)
    previewUrlRef.current = null
    setPreviewUrl(null)
    setFile(null)
    setResult(null)
    setVoiceNoteFile(null)
    if (voiceNoteInput.current) voiceNoteInput.current.value = ''
    setReportError(null)
    setError(null)
  }

  const handleUpdateReportWithVoiceNote = async (inspectionId: string, voiceNote: File) => {
    if (reportActionId) return
    setReportActionId(inspectionId)
    setReportError(null)
    try {
      const pdf = await createInspectionReport(inspectionId, voiceNote)
      downloadPdf(pdf, inspectionId)
      setResult((current) =>
        current && current.inspection_id === inspectionId
          ? { ...current, report_available: true }
          : current,
      )
      setVoiceNoteFile(null)
      if (voiceNoteInput.current) voiceNoteInput.current.value = ''
      void refreshHistory()
    } catch (caught: unknown) {
      setReportError(caught instanceof Error ? caught.message : 'PDF report could not be updated.')
    } finally {
      setReportActionId(null)
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
    if (isClearingHistory || isLoading || reportActionId || history.length === 0) return
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
      setVoiceNoteFile(null)
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
            onClick={handleInspect}
            disabled={!file || isLoading}
          >
            {isLoading ? (
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
            <Sparkles size={13} /> AI-assisted visual grading · usually under 10 seconds
          </p>
          <p className="upload-retention-note">
            Original photos are stored with inspection history so PDF reports can include them.
          </p>
        </section>

        <section
          className={`result-panel ${result ? `result-${result.verdict}` : ''}`}
          aria-live="polite"
        >
          <div className="result-panel-top">
            <div>
              <p className="section-kicker">
                02 <span>/</span> INSPECTION RESULT
              </p>
              <h2>{result ? 'Your quality readout' : 'Your result, right here.'}</h2>
            </div>
            {result && (
              <span
                className={`verdict-pill ${
                  isUnsupportedItem ? 'verdict-needs_review' : `verdict-${result.verdict}`
                }`}
              >
                {isUnsupportedItem ? 'Unsupported item' : VERDICT_LABELS[result.verdict]}
              </span>
            )}
          </div>

          {error ? (
            <div className="result-message error-message">
              <span className="message-icon">
                <CircleAlert size={22} />
              </span>
              <h3>Inspection didn’t finish</h3>
              <p>{error}</p>
              <button
                type="button"
                className="retry-button"
                onClick={handleInspect}
                disabled={!file || isLoading}
              >
                Try again <RefreshCw size={15} />
              </button>
            </div>
          ) : isLoading ? (
            <div className="result-message loading-message">
              <span className="scan-loader">
                <ScanLine size={30} />
              </span>
              <h3>Looking a little closer…</h3>
              <p>Checking visible signs and estimating a quality grade.</p>
              <div className="loading-track">
                <span />
              </div>
            </div>
          ) : result ? (
            <div className="result-content">
              <div className="grade-row">
                {!isUnsupportedItem && (
                  <div className="grade-block">
                    <span className="result-label">QUALITY GRADE</span>
                    <strong
                      className={`grade-value grade-${result.grade === 'A' ? 'a' : result.grade === 'B' ? 'b' : 'review'}`}
                    >
                      {result.grade === 'Needs manual review' ? '—' : result.grade}
                    </strong>
                  </div>
                )}
                <div className="product-identified">
                  <span className="identified-emoji">
                    {productEmoji(result, categories, isUnsupportedItem)}
                  </span>
                  <span>
                    <small>{isUnsupportedItem ? 'DETECTED' : 'IDENTIFIED AS'}</small>
                    <strong>{productName(result, categories)}</strong>
                  </span>
                </div>
              </div>

              {resultClassification === 'out_of_categories' && (
                <p className="category-notice" role="status">
                  This product is {productName(result, categories)} - from out of inspection
                  products
                </p>
              )}
              {isUnsupportedItem && (
                <p className="category-notice" role="status">
                  Unsupported item
                </p>
              )}

              {!isUnsupportedItem && (
                <>
                  <div className="details-grid">
                    <div className="detail-cell">
                      <span className="result-label">VISIBLE DEFECTS</span>
                      {result.defect_types.length ? (
                        <div className="defect-tags">
                          {result.defect_types.map((defect) => (
                            <span className="defect-tag" key={defect}>
                              {defect}
                            </span>
                          ))}
                        </div>
                      ) : (
                        <strong className="detail-value">
                          <Check size={15} /> No visible defects
                        </strong>
                      )}
                    </div>
                    <div className="detail-cell">
                      <span className="result-label">SEVERITY</span>
                      <strong className={`severity-value severity-${result.severity}`}>
                        {result.severity}
                      </strong>
                    </div>
                  </div>

                  <section className="verdict-notes" aria-label="Decision basis">
                    <h3>Why this verdict?</h3>
                    <ul>
                      {result.verdict_notes.map((note) => (
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
                      <strong>{result.recommended_action}</strong>
                    </span>
                  </div>
                </>
              )}
              <p className="result-disclaimer">{result.disclaimer}</p>
              <p className="inspection-id">
                INSPECTION {result.inspection_id.slice(0, 8).toUpperCase()}
              </p>
              <section className="report-controls" aria-label="PDF inspection report">
                <div>
                  <h3>Inspection report</h3>
                  <p>
                    {isUnsupportedItem
                      ? 'This item is outside the inspection categories, so no PDF report was created.'
                      : result.report_available
                        ? 'Your PDF is ready. Add a short voice note to update it with spoken context.'
                        : 'The PDF could not be created for this inspection.'}
                  </p>
                </div>
                {result.report_available && !isUnsupportedItem && (
                  <>
                    <button
                      className="report-button"
                      type="button"
                      onClick={() => void handleDownloadReport(result.inspection_id)}
                      disabled={reportActionId !== null}
                    >
                      {reportActionId === result.inspection_id ? (
                        <>
                          <LoaderCircle className="spin" size={16} /> Preparing PDF
                        </>
                      ) : (
                        <>
                          <Download size={16} /> Download PDF
                        </>
                      )}
                    </button>
                    <label>
                      Add a voice note to update the report
                      <input
                        ref={voiceNoteInput}
                        type="file"
                        accept="audio/*"
                        onChange={(event) => {
                          const selected = event.currentTarget.files?.[0] ?? null
                          setReportError(null)
                          if (!selected) return
                          if (selected.size > MAX_VOICE_NOTE_BYTES) {
                            setVoiceNoteFile(null)
                            setReportError('Voice note is over 25 MB. Choose a smaller audio file.')
                            event.currentTarget.value = ''
                            return
                          }
                          setVoiceNoteFile(selected)
                          void handleUpdateReportWithVoiceNote(result.inspection_id, selected)
                        }}
                      />
                    </label>
                    {voiceNoteFile && <p className="report-file-name">{voiceNoteFile.name}</p>}
                  </>
                )}
                {!result.report_available && (
                  <p className="report-unavailable">Report unavailable.</p>
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
                isLoading ||
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
                  <img
                    className="history-thumbnail"
                    src={inspectionThumbnailUrl(item.inspection_id)}
                    alt={`${productName(item, categories)} inspection`}
                  />
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
