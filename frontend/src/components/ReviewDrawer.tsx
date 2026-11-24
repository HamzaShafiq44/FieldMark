import { CircleAlert, Download, Eye, LoaderCircle, X } from 'lucide-react'
import { type MouseEvent, useCallback, useEffect, useRef, useState } from 'react'
import PhotoLightbox from './PhotoLightbox'
import { type ProductCategory } from '../services/categories'
import {
  classifyInspectionResult,
  downloadInspectionReport,
  fetchInspectionDetail,
  inspectionAnnotatedUrl,
  inspectionOriginalUrl,
  type InspectionDetail,
  type InspectionResult,
} from '../services/api'

const VERDICT_LABELS: Record<InspectionResult['verdict'], string> = {
  pass: 'Looks good',
  defect: 'Defect detected',
  needs_review: 'Needs a closer look',
}

const PDF_REVOKE_DELAY_MS = 1_000

function capitalize(value: string): string {
  return value.charAt(0).toUpperCase() + value.slice(1)
}

function humanizeSlug(slug: string): string {
  return capitalize(slug.replace(/-/g, ' '))
}

function productName(item: InspectionResult, categories: ProductCategory[]): string {
  const detected = item.detected_item?.trim()
  if (detected) return capitalize(detected)
  if (item.produce_type === 'unknown') return 'Unidentified product'
  const category = categories.find((entry) => entry.slug === item.produce_type)
  return category?.display_name ?? humanizeSlug(item.produce_type)
}

function productEmoji(
  item: InspectionResult,
  categories: ProductCategory[],
  unsupported: boolean,
): string {
  const category = categories.find((entry) => entry.slug === item.produce_type)
  if (category?.emoji) return category.emoji
  return unsupported ? '🚫' : '🌱'
}

function formatDate(value: string): string {
  if (!value) return '—'
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
  window.setTimeout(() => URL.revokeObjectURL(url), PDF_REVOKE_DELAY_MS)
}

interface ReviewDrawerProps {
  inspectionId: string
  categories: ProductCategory[]
  onClose: () => void
}

/** Read-only overlay that replays everything in a saved inspection's PDF. */
function ReviewDrawer({ inspectionId, categories, onClose }: ReviewDrawerProps) {
  const [detail, setDetail] = useState<InspectionDetail | null>(null)
  const [isLoading, setIsLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [isDownloading, setIsDownloading] = useState(false)
  const [downloadError, setDownloadError] = useState<string | null>(null)
  const [lightbox, setLightbox] = useState<{ src: string; caption: string } | null>(null)
  const closeButtonRef = useRef<HTMLButtonElement>(null)
  const drawerRef = useRef<HTMLElement>(null)
  const previouslyFocusedRef = useRef<HTMLElement | null>(null)
  const lightboxOpenRef = useRef(false)
  useEffect(() => {
    lightboxOpenRef.current = lightbox !== null
  }, [lightbox])

  useEffect(() => {
    let active = true
    fetchInspectionDetail(inspectionId)
      .then((loaded) => {
        if (active) setDetail(loaded)
      })
      .catch((caught: unknown) => {
        if (active) {
          setError(caught instanceof Error ? caught.message : 'Could not load this inspection.')
        }
      })
      .finally(() => {
        if (active) setIsLoading(false)
      })
    return () => {
      active = false
    }
  }, [inspectionId])

  useEffect(() => {
    const handleKeyDown = (event: KeyboardEvent) => {
      if (lightboxOpenRef.current) return
      if (event.key === 'Escape') {
        onClose()
        return
      }
      if (event.key !== 'Tab') return
      const focusable = drawerRef.current?.querySelectorAll<HTMLElement>(
        'button:not([disabled]), a[href], input:not([disabled]), [tabindex]:not([tabindex="-1"])',
      )
      if (!focusable || focusable.length === 0) return
      const first = focusable[0]
      const last = focusable[focusable.length - 1]
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault()
        last.focus()
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault()
        first.focus()
      }
    }
    const active = document.activeElement
    previouslyFocusedRef.current = active instanceof HTMLElement ? active : null
    document.addEventListener('keydown', handleKeyDown)
    const previousOverflow = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    closeButtonRef.current?.focus()
    return () => {
      document.removeEventListener('keydown', handleKeyDown)
      document.body.style.overflow = previousOverflow
      previouslyFocusedRef.current?.focus()
    }
  }, [onClose])

  const handleBackdropMouseDown = (event: MouseEvent<HTMLDivElement>) => {
    if (event.target === event.currentTarget) onClose()
  }

  const closeLightbox = useCallback(() => setLightbox(null), [])

  const handleDownload = useCallback(async () => {
    if (isDownloading || !detail?.report) return
    setIsDownloading(true)
    setDownloadError(null)
    try {
      const pdf = await downloadInspectionReport(detail.inspection_id)
      downloadPdf(pdf, detail.inspection_id)
    } catch (caught: unknown) {
      setDownloadError(
        caught instanceof Error ? caught.message : 'PDF report could not be downloaded.',
      )
    } finally {
      setIsDownloading(false)
    }
  }, [detail, isDownloading])

  const result = detail?.result
  const report = detail?.report
  const classification = result ? classifyInspectionResult(result) : null
  const unsupported = classification === 'unsupported_item'
  const isVoiceOnly = Boolean(result?.voice_note_transcript)
  const confidencePercent =
    result?.confidence === null || result?.confidence === undefined
      ? null
      : Math.round(result.confidence * 100)

  return (
    <>
      <div className="review-backdrop" onMouseDown={handleBackdropMouseDown}>
        <aside
          ref={drawerRef}
          className="review-drawer"
          role="dialog"
          aria-modal="true"
          aria-labelledby="review-title"
        >
          <header className="review-drawer-header">
            <div>
              <p className="section-kicker">
                SAVED INSPECTION <span>/</span> REVIEW
              </p>
              <h2 id="review-title">Inspection review</h2>
            </div>
            <button
              ref={closeButtonRef}
              className="icon-button review-close"
              type="button"
              onClick={onClose}
              aria-label="Close review"
            >
              <X size={18} />
            </button>
          </header>

          <div className="review-drawer-scroll">
            {isLoading ? (
              <div className="review-skeleton" aria-hidden="true">
                <div className="skeleton skeleton-line skeleton-line-wide" />
                <div className="skeleton skeleton-block" />
                <div className="skeleton skeleton-line" />
                <div className="skeleton skeleton-line skeleton-line-short" />
                <div className="skeleton skeleton-block" />
              </div>
            ) : error || !result ? (
              <div className="review-state">
                <CircleAlert size={26} />
                <p>{error ?? 'This inspection is no longer available.'}</p>
                <button className="report-button" type="button" onClick={onClose}>
                  Close
                </button>
              </div>
            ) : (
              <div className="review-content">
                <div className="review-summary">
                  <div className="review-summary-main">
                    <span className="identified-emoji">
                      {productEmoji(result, categories, unsupported)}
                    </span>
                    <div>
                      <span className="result-label">
                        {unsupported ? 'DETECTED' : 'IDENTIFIED AS'}
                      </span>
                      <strong>{productName(result, categories)}</strong>
                    </div>
                  </div>
                  <span
                    className={`verdict-pill ${
                      unsupported ? 'verdict-needs_review' : `verdict-${result.verdict}`
                    }`}
                  >
                    {unsupported ? 'Unsupported item' : VERDICT_LABELS[result.verdict]}
                  </span>
                </div>

                <div className="review-facts">
                  {!unsupported && (
                    <div className="grade-block">
                      <span className="result-label">QUALITY GRADE</span>
                      <strong
                        className={`grade-value grade-${
                          result.grade === 'A' ? 'a' : result.grade === 'B' ? 'b' : 'review'
                        }`}
                      >
                        {result.grade === 'Needs manual review' ? '—' : result.grade}
                      </strong>
                    </div>
                  )}
                  <div className="review-fact">
                    <span className="result-label">INSPECTED</span>
                    <strong>{formatDate(detail?.created_at ?? '')}</strong>
                  </div>
                  {!unsupported && confidencePercent !== null && (
                    <div className="review-fact">
                      <span className="result-label">CONFIDENCE</span>
                      <strong>{confidencePercent}%</strong>
                    </div>
                  )}
                </div>

                {classification === 'out_of_categories' && (
                  <p className="category-notice">
                    This product is {productName(result, categories)} - from out of inspection
                    products
                  </p>
                )}
                {isVoiceOnly && (
                  <div className="review-voice-intro">
                    <strong>Graded from your voice note</strong>
                    <span>
                      This grade is based on what you said, not a photo. Inspect the product
                      manually before acting.
                    </span>
                  </div>
                )}

                {!isVoiceOnly && (
                  <section className="review-section" aria-label="Photo evidence">
                    <div className="review-section-heading">
                      <span className="result-label">PHOTO EVIDENCE</span>
                      {result.bbox && <span>Reported defect area marked</span>}
                    </div>
                    <div className={`photo-evidence-grid ${result.bbox ? 'has-annotated' : ''}`}>
                      <figure className="photo-figure">
                        <button
                          className="photo-zoom-button"
                          type="button"
                          aria-label="View original photo full size"
                          onClick={() =>
                            setLightbox({
                              src: inspectionOriginalUrl(result.inspection_id),
                              caption: 'Original photo',
                            })
                          }
                        >
                          <img
                            src={inspectionOriginalUrl(result.inspection_id)}
                            alt="Original uploaded product photo"
                          />
                        </button>
                        <figcaption>Original photo</figcaption>
                      </figure>
                      {result.bbox && (
                        <figure className="photo-figure">
                          <button
                            className="photo-zoom-button"
                            type="button"
                            aria-label="View annotated photo full size"
                            onClick={() =>
                              setLightbox({
                                src: inspectionAnnotatedUrl(result.inspection_id),
                                caption: 'Reported defect area',
                              })
                            }
                          >
                            <img
                              src={inspectionAnnotatedUrl(result.inspection_id)}
                              alt="Product photo with the reported defect area marked"
                            />
                          </button>
                          <figcaption>Reported defect area</figcaption>
                        </figure>
                      )}
                    </div>
                  </section>
                )}

                {!unsupported && (
                  <>
                    <section className="review-section" aria-label="Defects and severity">
                      <div className="details-grid">
                        <div className="detail-cell">
                          <span className="result-label">
                            {isVoiceOnly ? 'REPORTED DEFECTS' : 'VISIBLE DEFECTS'}
                          </span>
                          {result.defect_types.length ? (
                            <div className="defect-tags">
                              {result.defect_types.map((defect) => (
                                <span className="defect-tag" key={defect}>
                                  {defect}
                                </span>
                              ))}
                            </div>
                          ) : (
                            <strong className="detail-value">No defects reported</strong>
                          )}
                        </div>
                        <div className="detail-cell">
                          <span className="result-label">SEVERITY</span>
                          <strong className={`severity-value severity-${result.severity}`}>
                            {result.severity}
                          </strong>
                        </div>
                      </div>
                    </section>

                    {result.verdict_notes.length > 0 && (
                      <section className="verdict-notes" aria-label="Decision basis">
                        <h3>Why this verdict?</h3>
                        <ul>
                          {result.verdict_notes.map((note) => (
                            <li key={note}>{note}</li>
                          ))}
                        </ul>
                      </section>
                    )}

                    <div className="action-card">
                      <span className="action-icon">
                        <Eye size={17} />
                      </span>
                      <span>
                        <small>RECOMMENDED NEXT STEP</small>
                        <strong>{result.recommended_action}</strong>
                      </span>
                    </div>
                  </>
                )}

                {result.voice_note_transcript && (
                  <section className="review-section" aria-label="Voice note transcript">
                    <span className="result-label">VOICE NOTE TRANSCRIPT</span>
                    <p className="transcript-text">“{result.voice_note_transcript}”</p>
                  </section>
                )}

                <section className="review-section" aria-label="Written report">
                  <div className="review-section-heading">
                    <span className="result-label">WRITTEN REPORT</span>
                    {report && <span>{formatDate(report.created_at)}</span>}
                  </div>
                  {report ? (
                    <div className="review-narrative">
                      <div className="review-narrative-block">
                        <h3>What we saw</h3>
                        <p>{report.report_text.summary}</p>
                      </div>
                      <div className="review-narrative-block">
                        <h3>Inspection findings</h3>
                        <ul>
                          {report.report_text.findings.map((finding, index) => (
                            <li key={`${finding}-${index}`}>{finding}</li>
                          ))}
                        </ul>
                      </div>
                      <div className="review-narrative-block">
                        <h3>Recommendations</h3>
                        <ul>
                          {report.report_text.recommendations.map((recommendation, index) => (
                            <li key={`${recommendation}-${index}`}>{recommendation}</li>
                          ))}
                        </ul>
                      </div>
                      {report.report_text.voice_note_relation === 'conflicts' &&
                        report.report_text.voice_note_conflict && (
                          <div className="review-conflict">
                            <h3>Voice note conflict</h3>
                            <p>{report.report_text.voice_note_conflict}</p>
                          </div>
                        )}
                      {report.transcript && (
                        <div className="review-narrative-block">
                          <span className="result-label">REPORT TRANSCRIPT</span>
                          <p className="transcript-text">“{report.transcript}”</p>
                        </div>
                      )}
                    </div>
                  ) : (
                    <p className="review-report-empty">
                      No written report is available for this inspection yet. The result details
                      above are still saved.
                    </p>
                  )}
                </section>

                <p className="result-disclaimer">{result.disclaimer}</p>
              </div>
            )}
          </div>

          {result && (
            <footer className="review-drawer-footer">
              <span className="review-footer-id">
                INSPECTION {result.inspection_id.slice(0, 8).toUpperCase()}
              </span>
              <div className="review-footer-actions">
                {report && (
                  <button
                    className="report-button"
                    type="button"
                    onClick={() => void handleDownload()}
                    disabled={isDownloading}
                  >
                    {isDownloading ? (
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
                <button className="review-close-button" type="button" onClick={onClose}>
                  Close
                </button>
              </div>
              {downloadError && (
                <p className="report-error" role="alert">
                  {downloadError}
                </p>
              )}
            </footer>
          )}
        </aside>
      </div>
      {lightbox && (
        <PhotoLightbox src={lightbox.src} caption={lightbox.caption} onClose={closeLightbox} />
      )}
    </>
  )
}

export default ReviewDrawer
