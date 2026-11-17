import {
  ArrowRight,
  Camera,
  Check,
  CircleAlert,
  Clock3,
  ImagePlus,
  Leaf,
  LoaderCircle,
  Moon,
  RefreshCw,
  ScanLine,
  ShieldCheck,
  Sparkles,
  Sun,
  Upload,
  X,
} from 'lucide-react'
import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type ChangeEvent,
  type DragEvent,
} from 'react'
import './App.css'
import {
  ApiError,
  checkApiHealth,
  inspectionThumbnailUrl,
  inspectProduce,
  listInspectionHistory,
  type InspectionHistoryItem,
  type InspectionResult,
  type ProduceType,
} from './services/api'

const MAX_IMAGE_BYTES = 10 * 1024 * 1024
const THEME_STORAGE_KEY = 'fieldmark-theme'
const ACCEPTED_IMAGE_TYPES = new Set(['image/jpeg', 'image/png', 'image/webp'])
const ACCEPTED_IMAGE_EXTENSIONS = '.jpg, .jpeg, .png, .webp'
const PRODUCE_LABELS: Record<ProduceType, string> = {
  tomato: 'Tomato',
  apple: 'Apple',
  banana: 'Banana',
  orange: 'Orange',
  unknown: 'Unidentified produce',
}
const PRODUCE_EMOJI: Record<ProduceType, string> = {
  tomato: '🍅',
  apple: '🍎',
  banana: '🍌',
  orange: '🍊',
  unknown: '🌱',
}
const VERDICT_LABELS: Record<InspectionResult['verdict'], string> = {
  pass: 'Looks good',
  defect: 'Defect detected',
  needs_review: 'Needs a closer look',
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
    lessRipe: [
      'A mostly green peel and more angular shape often indicate an earlier stage.',
    ],
    riper: [
      'The peel commonly turns yellow; small brown sugar spots can be normal ripening.',
      'Green tips may remain while the banana continues to ripen.',
    ],
  },
  {
    name: 'Tomato',
    emoji: '🍅',
    lessRipe: [
      'A pale or green cast, including green shoulders, may appear before full color.',
    ],
    riper: [
      'Color often deepens toward the variety’s mature color, which is not always red.',
      'Green shoulders alone do not mean the tomato is defective.',
    ],
  },
] as const

type ApiStatus = 'checking' | 'connected' | 'offline'
type Theme = 'light' | 'dark'

function getInitialTheme(): Theme {
  const savedTheme = window.localStorage.getItem(THEME_STORAGE_KEY)
  if (savedTheme === 'light' || savedTheme === 'dark') return savedTheme
  return window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'
}

function formatFileSize(bytes: number): string {
  return `${(bytes / (1024 * 1024)).toFixed(2)} MB`
}

function formatInspectionDate(value: string): string {
  return new Intl.DateTimeFormat(undefined, {
    dateStyle: 'medium',
    timeStyle: 'short',
  }).format(new Date(value))
}

function App() {
  const cameraInput = useRef<HTMLInputElement>(null)
  const previewUrlRef = useRef<string | null>(null)
  const [file, setFile] = useState<File | null>(null)
  const [previewUrl, setPreviewUrl] = useState<string | null>(null)
  const [result, setResult] = useState<InspectionResult | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [isDragging, setIsDragging] = useState(false)
  const [isLoading, setIsLoading] = useState(false)
  const [apiStatus, setApiStatus] = useState<ApiStatus>('checking')
  const [history, setHistory] = useState<InspectionHistoryItem[]>([])
  const [historyError, setHistoryError] = useState<string | null>(null)
  const [isHistoryLoading, setIsHistoryLoading] = useState(true)
  const [theme, setTheme] = useState<Theme>(getInitialTheme)

  useEffect(() => {
    document.documentElement.dataset.theme = theme
    window.localStorage.setItem(THEME_STORAGE_KEY, theme)
  }, [theme])

  useEffect(
    () => () => {
      if (previewUrlRef.current) URL.revokeObjectURL(previewUrlRef.current)
    },
    [],
  )

  useEffect(() => {
    let active = true
    checkApiHealth()
      .then((isHealthy) => {
        if (active) setApiStatus(isHealthy ? 'connected' : 'offline')
      })
      .catch(() => {
        if (active) setApiStatus('offline')
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
    try {
      const inspection = await inspectProduce(file)
      setResult(inspection)
      setApiStatus('connected')
      void refreshHistory()
    } catch (caught: unknown) {
      if (caught instanceof TypeError) {
        setApiStatus('offline')
        setError('Could not reach the API. Make sure the backend is running, then try again.')
      } else if (caught instanceof ApiError && caught.status === 502) {
        setApiStatus('connected')
        setError(
          'The vision service could not complete this inspection. Check model availability, API limits, and try again.',
        )
      } else {
        setApiStatus('connected')
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
    setError(null)
  }

  return (
    <div className="app-shell">
      <header className="topbar">
        <a className="brand" href="#" aria-label="Fieldmark home">
          <span className="brand-mark">
            <Leaf size={21} strokeWidth={2.2} />
          </span>
          <span className="brand-copy">
            <span className="brand-name">fieldmark</span>
            <span className="brand-caption">PRODUCE QUALITY STUDIO</span>
          </span>
        </a>
        <div className="topbar-right">
          <span className="topbar-note">FIELD INSPECTION / 01</span>
          <button
            className="theme-toggle"
            type="button"
            aria-label={`Switch to ${theme === 'dark' ? 'light' : 'dark'} mode`}
            aria-pressed={theme === 'dark'}
            onClick={() => setTheme(theme === 'dark' ? 'light' : 'dark')}
          >
            {theme === 'dark' ? <Sun size={16} /> : <Moon size={16} />}
            <span>{theme === 'dark' ? 'Light mode' : 'Dark mode'}</span>
          </button>
          <div className={`connection-pill connection-${apiStatus}`}>
            <span className="connection-dot" />
            {apiStatus === 'checking'
              ? 'Connecting'
              : apiStatus === 'connected'
                ? 'API connected'
                : 'API offline'}
          </div>
        </div>
      </header>

      <main>
        <section className="hero">
          <div className="hero-copy">
            <div className="eyebrow">
              <span className="eyebrow-dot" />
              A FRESH LOOK AT QUALITY
            </div>
            <h1>
              Know your harvest
              <br />
              <span>at a glance.</span>
            </h1>
            <p className="hero-description">
              A quick visual check for the produce you grow. Add a photo to see a quality grade,
              visible defects, and a practical next step.
            </p>
            <div className="hero-footnote">
              <ShieldCheck size={16} />
              <span>Indicative results. Always inspect manually when in doubt.</span>
            </div>
          </div>
          <div className="hero-art" aria-label="Supported produce: tomato, apple, banana, and orange">
            <div className="orbit orbit-one" />
            <div className="orbit orbit-two" />
            <div className="fruit-card fruit-card-main">
              <span className="fruit-emoji">🍎</span>
              <span className="fruit-label">APPLE</span>
            </div>
            <div className="fruit-card fruit-card-tomato">
              <span className="fruit-emoji">🍅</span>
            </div>
            <div className="fruit-card fruit-card-banana">
              <span className="fruit-emoji">🍌</span>
            </div>
            <div className="fruit-card fruit-card-orange">
              <span className="fruit-emoji">🍊</span>
            </div>
            <div className="art-stamp">
              <Sparkles size={16} />
              <span>GROWN WITH CARE</span>
            </div>
          </div>
        </section>

        <section className="inspection-grid" aria-label="Produce inspection">
          <section className="panel upload-panel">
            <div className="panel-heading">
              <div>
                <p className="section-kicker">01 <span>/</span> IMAGE INPUT</p>
                <h2>Start with a photo</h2>
                <p className="panel-subtitle">One piece of produce, clearly in frame.</p>
              </div>
              <span className="heading-icon"><ImagePlus size={19} /></span>
            </div>

            <label
              className={`upload-zone ${isDragging ? 'upload-zone-dragging' : ''} ${previewUrl ? 'has-preview' : ''}`}
              htmlFor="produce-file"
              onDragOver={(event) => {
                event.preventDefault()
                setIsDragging(true)
              }}
              onDragLeave={() => setIsDragging(false)}
              onDrop={handleDrop}
            >
              <input
                id="produce-file"
                className="visually-hidden"
                type="file"
                accept={ACCEPTED_IMAGE_EXTENSIONS}
                onChange={handleFileChange}
              />
              {previewUrl ? (
                <div className="image-preview">
                  <div className="image-canvas">
                    <img src={previewUrl} alt="Selected produce preview" />
                  </div>
                  <span className="preview-change"><RefreshCw size={14} /> Replace photo</span>
                </div>
              ) : (
                <div className="upload-prompt">
                  <span className="upload-icon"><Upload size={23} /></span>
                  <span className="upload-title">Drop your photo here</span>
                  <span className="upload-subtitle">or <strong>browse files</strong> on your device</span>
                  <span className="upload-formats">JPEG · PNG · WEBP <i /> UP TO 10 MB</span>
                </div>
              )}
            </label>

            {file ? (
              <div className="file-row">
                <span className="file-icon"><ImagePlus size={17} /></span>
                <span className="file-info">
                  <strong>{file.name}</strong>
                  <small>{formatFileSize(file.size)}</small>
                </span>
                <button className="icon-button" type="button" onClick={clearImage} aria-label="Remove image">
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
                <button className="camera-button" type="button" onClick={() => cameraInput.current?.click()}>
                  <Camera size={16} /> Use camera
                </button>
              </div>
            )}

            <div className="supported-row">
              <span>SUPPORTED</span>
              <div className="supported-fruit"><i>🍅</i> Tomato</div>
              <div className="supported-fruit"><i>🍎</i> Apple</div>
              <div className="supported-fruit"><i>🍌</i> Banana</div>
              <div className="supported-fruit"><i>🍊</i> Orange</div>
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
                  Inspect produce <ArrowRight size={18} />
                </>
              )}
            </button>
            <p className="button-caption">
              <Sparkles size={13} /> AI-assisted visual grading · usually under 10 seconds
            </p>
          </section>

          <section className={`result-panel ${result ? `result-${result.verdict}` : ''}`} aria-live="polite">
            <div className="result-panel-top">
              <div>
                <p className="section-kicker">02 <span>/</span> INSPECTION RESULT</p>
                <h2>{result ? 'Your quality readout' : 'Your result, right here.'}</h2>
              </div>
              {result && <span className={`verdict-pill verdict-${result.verdict}`}>{VERDICT_LABELS[result.verdict]}</span>}
            </div>

            {error ? (
              <div className="result-message error-message">
                <span className="message-icon"><CircleAlert size={22} /></span>
                <h3>Inspection didn’t finish</h3>
                <p>{error}</p>
                <button type="button" className="retry-button" onClick={handleInspect} disabled={!file || isLoading}>
                  Try again <RefreshCw size={15} />
                </button>
              </div>
            ) : isLoading ? (
              <div className="result-message loading-message">
                <span className="scan-loader"><ScanLine size={30} /></span>
                <h3>Looking a little closer…</h3>
                <p>Checking visible signs and estimating a quality grade.</p>
                <div className="loading-track"><span /></div>
              </div>
            ) : result ? (
              <div className="result-content">
                <div className="grade-row">
                  <div className="grade-block">
                    <span className="result-label">QUALITY GRADE</span>
                    <strong className={`grade-value grade-${result.grade === 'A' ? 'a' : result.grade === 'B' ? 'b' : 'review'}`}>
                      {result.grade === 'Needs manual review' ? '—' : result.grade}
                    </strong>
                  </div>
                  <div className="produce-identified">
                    <span className="identified-emoji">{PRODUCE_EMOJI[result.produce_type]}</span>
                    <span>
                      <small>IDENTIFIED AS</small>
                      <strong>{PRODUCE_LABELS[result.produce_type]}</strong>
                    </span>
                  </div>
                </div>

                <div className="confidence-block">
                  <div className="confidence-head">
                    <span>MODEL CONFIDENCE</span>
                    <strong>{result.confidence === null ? '—' : `${Math.round(result.confidence * 100)}%`}</strong>
                  </div>
                  <div
                    className="confidence-track"
                    role="meter"
                    aria-label="Model confidence"
                    aria-valuemin={0}
                    aria-valuemax={100}
                    aria-valuenow={result.confidence === null ? 0 : Math.round(result.confidence * 100)}
                  >
                    <span style={{ width: `${(result.confidence ?? 0) * 100}%` }} />
                  </div>
                </div>

                <div className="details-grid">
                  <div className="detail-cell">
                    <span className="result-label">VISIBLE DEFECTS</span>
                    {result.defect_types.length ? (
                      <div className="defect-tags">
                        {result.defect_types.map((defect) => (
                          <span className="defect-tag" key={defect}>{defect}</span>
                        ))}
                      </div>
                    ) : (
                      <strong className="detail-value"><Check size={15} /> No visible defects</strong>
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
                    {result.verdict_notes.map((note) => <li key={note}>{note}</li>)}
                  </ul>
                </section>

                <div className="action-card">
                  <span className="action-icon"><ArrowRight size={17} /></span>
                  <span>
                    <small>RECOMMENDED NEXT STEP</small>
                    <strong>{result.recommended_action}</strong>
                  </span>
                </div>
                <p className="result-disclaimer">{result.disclaimer}</p>
                <p className="inspection-id">INSPECTION {result.inspection_id.slice(0, 8).toUpperCase()}</p>
              </div>
            ) : (
              <div className="result-message empty-message">
                <div className="empty-visual">
                  <span className="empty-ring empty-ring-one" />
                  <span className="empty-ring empty-ring-two" />
                  <span className="empty-scan"><ScanLine size={30} /></span>
                  <span className="empty-check"><Check size={15} /></span>
                </div>
                <h3>Good decisions start with a closer look.</h3>
                <p>Your grade, visible defects, and next step will appear here after inspection.</p>
                <div className="empty-tip"><Sparkles size={14} /> Clear light makes for a clearer read.</div>
              </div>
            )}
          </section>
        </section>

        <section className="history-section" aria-label="Recent inspections">
          <div className="history-heading">
            <div>
              <p className="section-kicker">03 <span>/</span> SAVED INSPECTIONS</p>
              <h2>Recent history</h2>
            </div>
            <button
              className="history-refresh"
              type="button"
              onClick={() => void refreshHistory()}
              disabled={isHistoryLoading}
            >
              <RefreshCw size={15} className={isHistoryLoading ? 'spin' : ''} />
              Refresh
            </button>
          </div>
          {historyError ? (
            <div className="history-message history-error">
              <p>{historyError}</p>
              <button type="button" onClick={() => void refreshHistory()}>Try again</button>
            </div>
          ) : isHistoryLoading && history.length === 0 ? (
            <p className="history-message">Loading saved inspections…</p>
          ) : history.length ? (
            <div className="history-list">
              {history.map((item) => (
                <article className="history-item" key={item.inspection_id}>
                  <img
                    className="history-thumbnail"
                    src={inspectionThumbnailUrl(item.inspection_id)}
                    alt={`${PRODUCE_LABELS[item.produce_type]} inspection`}
                  />
                  <div className="history-item-copy">
                    <strong>{PRODUCE_LABELS[item.produce_type]}</strong>
                    <span>{formatInspectionDate(item.created_at)}</span>
                  </div>
                  <div className="history-item-result">
                    <span className={`verdict-pill verdict-${item.verdict}`}>
                      {VERDICT_LABELS[item.verdict]}
                    </span>
                    <strong>{item.grade === 'Needs manual review' ? 'Review' : item.grade}</strong>
                  </div>
                </article>
              ))}
            </div>
          ) : (
            <div className="history-empty">
              <Clock3 size={20} />
              <p>Your completed inspections will be saved here.</p>
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
              <span className="step-icon"><ImagePlus size={19} /></span>
              <strong>Choose a photo</strong>
              <p>Use a clear image with one piece of produce in view.</p>
            </div>
            <div className="step-connector"><ArrowRight size={17} /></div>
            <div className="step-card">
              <span className="step-number">02</span>
              <span className="step-icon"><ScanLine size={19} /></span>
              <strong>Review the signs</strong>
              <p>The model looks for visible quality issues and their location.</p>
            </div>
            <div className="step-connector"><ArrowRight size={17} /></div>
            <div className="step-card">
              <span className="step-number">03</span>
              <span className="step-icon"><Check size={19} /></span>
              <strong>Choose your next step</strong>
              <p>Use the grade as a guide, and inspect manually when unsure.</p>
            </div>
          </div>
        </section>

        <details className="ripeness-guide">
          <summary>
            <span>
              <span className="section-kicker">A QUICK VISUAL REFERENCE</span>
              <strong>Ripeness cues for your produce</strong>
            </span>
            <span className="ripeness-summary-action">Show / hide</span>
          </summary>
          <p className="ripeness-disclaimer">
            These are visual hints, not guarantees. Variety, lighting, and storage change
            appearance; being unripe alone does not mean fruit is spoiled or defective.
          </p>
          <div className="ripeness-grid">
            {RIPENESS_GUIDE.map((fruit) => (
              <article className="ripeness-card" key={fruit.name}>
                <h3><span aria-hidden="true">{fruit.emoji}</span> {fruit.name}</h3>
                <div className="ripeness-cue-group">
                  <h4>Less ripe signs</h4>
                  <ul>{fruit.lessRipe.map((cue) => <li key={cue}>{cue}</li>)}</ul>
                </div>
                <div className="ripeness-cue-group">
                  <h4>Riper signs</h4>
                  <ul>{fruit.riper.map((cue) => <li key={cue}>{cue}</li>)}</ul>
                </div>
              </article>
            ))}
          </div>
        </details>
      </main>

      <footer className="footer">
        <span className="footer-brand"><Leaf size={15} /> FIELDMARK</span>
        <span>Visual quality guidance, not a food safety assessment.</span>
        <span>MADE FOR THE FIELD <span className="footer-sun">✳</span></span>
      </footer>
    </div>
  )
}

export default App
