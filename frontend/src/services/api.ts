/** Typed API calls used by the inspection interface. */

export type ProduceType = string
export type Verdict = 'pass' | 'defect' | 'needs_review'
export type Grade = 'A' | 'B' | 'Reject' | 'Needs manual review'
export type Severity = 'none' | 'low' | 'medium' | 'high'
export type DefectType = 'bruise' | 'rot' | 'mold' | 'deformity'
export type BoundingBox = [number, number, number, number]
export type ApiStatus = 'checking' | 'connected' | 'offline'

export interface InspectionResult {
  inspection_id: string
  produce_type: ProduceType
  detected_item: string
  item_is_fruit: boolean
  in_inspection_categories: boolean
  verdict: Verdict
  confidence: number | null
  bbox: BoundingBox | null
  defect_types: DefectType[]
  severity: Severity
  grade: Grade
  verdict_notes: string[]
  recommended_action: string
  disclaimer: string
  report_available: boolean
  // Optional transcript when a voice note is provided
  voice_note_transcript?: string
}

export type JobStatus = 'queued' | 'processing' | 'completed' | 'failed'
export type JobKind = 'image' | 'voice'

export interface InspectionJob {
  job_id: string
  kind: JobKind
  status: JobStatus
  stage: string
  created_at: string
  inspection_id: string | null
  result: InspectionResult | null
  error: string | null
  filename: string
  report_ready: boolean
}

export interface InspectionHistoryItem {
  inspection_id: string
  created_at: string
  produce_type: ProduceType
  detected_item: string
  item_is_fruit: boolean
  in_inspection_categories: boolean
  verdict: Verdict
  grade: Grade
  thumbnail_url: string
  report_available: boolean
  report_source_available: boolean
  voice_note_transcript?: string
}

/** Result classification shown by the interface. */
export type ResultClassification = 'supported' | 'out_of_categories' | 'unsupported_item'

/**
 * Classify a result by whether the detected product belongs to the inspection categories.
 *
 * Edible products outside the categories still receive a normal grade and verdict.
 * Anything that is not an edible product is an unsupported item with no grade or verdict.
 */
export function classifyInspectionResult(item: {
  produce_type: ProduceType
  detected_item?: string
  item_is_fruit?: boolean
  in_inspection_categories?: boolean
  voice_note_transcript?: string
}): ResultClassification {
  if (item.in_inspection_categories ?? item.produce_type !== 'unknown') return 'supported'
  if (item.item_is_fruit) return 'out_of_categories'
  // A voice note with no identified product is a manual-review record, not an
  // unsupported physical item.
  if (item.voice_note_transcript && !item.detected_item?.trim()) return 'supported'
  return 'unsupported_item'
}

export class ApiError extends Error {
  readonly status: number

  constructor(message: string, status: number) {
    super(message)
    this.name = 'ApiError'
    this.status = status
  }
}

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL?.replace(/\/$/, '') ?? ''

export function apiUrl(path: string): string {
  return `${API_BASE_URL}${path}`
}

export async function checkApiHealth(): Promise<boolean> {
  const response = await fetch(apiUrl('/health'))
  return response.ok
}

/** AI providers can stall; never leave the interface spinning indefinitely. */
const INSPECTION_TIMEOUT_MS = 90_000

export async function inspectVoiceNote(file: File): Promise<InspectionResult> {
  const formData = new FormData()
  formData.append('voice_note', file)
  return postInspection(formData)
}

export async function inspectProduce(file: File): Promise<InspectionResult> {
  const formData = new FormData()
  formData.append('image', file)
  return postInspection(formData)
}

async function postInspection(formData: FormData): Promise<InspectionResult> {
  const controller = new AbortController()
  const timeoutId = window.setTimeout(() => controller.abort(), INSPECTION_TIMEOUT_MS)
  try {
    const response = await fetch(apiUrl('/inspections/product'), {
      method: 'POST',
      body: formData,
      signal: controller.signal,
    })

    if (!response.ok) {
      const body = (await response.json()) as { detail?: unknown }
      const message =
        typeof body.detail === 'string'
          ? body.detail
          : `Inspection failed with status ${response.status}.`
      throw new ApiError(message, response.status)
    }

    return (await response.json()) as InspectionResult
  } catch (caught: unknown) {
    if (caught instanceof DOMException && caught.name === 'AbortError') {
      throw new ApiError('The inspection took too long. Check the API and try again.', 408)
    }
    throw caught
  } finally {
    window.clearTimeout(timeoutId)
  }
}

/** Submit an image without waiting for the AI work to finish. */
const JOB_SUBMIT_TIMEOUT_MS = 15_000

export async function submitImageJob(file: File): Promise<InspectionJob> {
  const formData = new FormData()
  formData.append('image', file)
  return submitJob(formData)
}

export async function submitVoiceJob(file: File): Promise<InspectionJob> {
  const formData = new FormData()
  formData.append('voice_note', file)
  return submitJob(formData)
}

async function submitJob(formData: FormData): Promise<InspectionJob> {
  const controller = new AbortController()
  const timeoutId = window.setTimeout(() => controller.abort(), JOB_SUBMIT_TIMEOUT_MS)
  try {
    const response = await fetch(apiUrl('/inspections/jobs'), {
      method: 'POST',
      body: formData,
      signal: controller.signal,
    })
    if (!response.ok) {
      const body = (await response.json()) as { detail?: unknown }
      const message =
        typeof body.detail === 'string'
          ? body.detail
          : `Inspection could not be queued (status ${response.status}).`
      throw new ApiError(message, response.status)
    }
    return (await response.json()) as InspectionJob
  } catch (caught: unknown) {
    if (caught instanceof DOMException && caught.name === 'AbortError') {
      throw new ApiError('The upload took too long. Check your connection and try again.', 408)
    }
    throw caught
  } finally {
    window.clearTimeout(timeoutId)
  }
}

export async function listJobs(): Promise<InspectionJob[]> {
  const response = await fetch(apiUrl('/inspections/jobs'))
  if (!response.ok) {
    throw new ApiError(
      `Could not load inspection progress (status ${response.status}).`,
      response.status,
    )
  }
  return (await response.json()) as InspectionJob[]
}

export async function getJob(jobId: string): Promise<InspectionJob> {
  const response = await fetch(apiUrl(`/inspections/jobs/${jobId}`))
  if (!response.ok) {
    throw new ApiError(`Inspection job not found (status ${response.status}).`, response.status)
  }
  return (await response.json()) as InspectionJob
}

export async function listInspectionHistory(): Promise<InspectionHistoryItem[]> {
  const response = await fetch(apiUrl('/inspections/history'))
  if (!response.ok) {
    throw new ApiError(
      `Could not load inspection history (status ${response.status}).`,
      response.status,
    )
  }
  const body = (await response.json()) as { items?: unknown }
  if (!Array.isArray(body.items)) {
    throw new Error('The API returned an invalid inspection history response.')
  }
  return body.items as InspectionHistoryItem[]
}

export function inspectionThumbnailUrl(inspectionId: string): string {
  return apiUrl(`/inspections/history/${encodeURIComponent(inspectionId)}/thumbnail`)
}

export function inspectionOriginalUrl(inspectionId: string): string {
  return apiUrl(`/inspections/history/${encodeURIComponent(inspectionId)}/original`)
}

export function inspectionAnnotatedUrl(inspectionId: string): string {
  return apiUrl(`/inspections/history/${encodeURIComponent(inspectionId)}/annotated`)
}

export async function createInspectionReport(
  inspectionId: string,
  voiceNote?: File,
): Promise<Blob> {
  const formData = new FormData()
  formData.append('user_type', 'farmer')
  if (voiceNote) formData.append('voice_note', voiceNote)

  const response = await fetch(reportUrl(inspectionId), {
    method: 'POST',
    body: formData,
  })
  if (!response.ok) throw await reportApiError(response)
  return response.blob()
}

export async function downloadInspectionReport(inspectionId: string): Promise<Blob> {
  const response = await fetch(`${reportUrl(inspectionId)}?download=true`)
  if (!response.ok) throw await reportApiError(response)
  return response.blob()
}

export async function relocateDefect(inspectionId: string): Promise<InspectionResult> {
  const response = await fetch(
    apiUrl(`/inspections/${encodeURIComponent(inspectionId)}/relocate`),
    {
      method: 'POST',
    },
  )
  if (!response.ok) {
    throw await responseApiError(response, 'Could not locate the defect on this photo.')
  }
  return (await response.json()) as InspectionResult
}

export async function clearInspectionHistory(): Promise<void> {
  const response = await fetch(apiUrl('/inspections/history'), { method: 'DELETE' })
  if (!response.ok) {
    throw await responseApiError(response, 'Could not clear inspection history.')
  }
}

function reportUrl(inspectionId: string): string {
  return apiUrl(`/inspections/${encodeURIComponent(inspectionId)}/report`)
}

async function reportApiError(response: Response): Promise<ApiError> {
  return responseApiError(response, `Report request failed with status ${response.status}.`)
}

async function responseApiError(response: Response, fallback: string): Promise<ApiError> {
  const body = (await response.json()) as { detail?: unknown }
  const message = typeof body.detail === 'string' ? body.detail : fallback
  return new ApiError(message, response.status)
}
