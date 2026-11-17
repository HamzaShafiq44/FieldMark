/** Typed API calls used by the inspection interface. */

export type ProduceType = 'tomato' | 'apple' | 'banana' | 'orange' | 'unknown'
export type Verdict = 'pass' | 'defect' | 'needs_review'
export type Grade = 'A' | 'B' | 'Reject' | 'Needs manual review'
export type Severity = 'none' | 'low' | 'medium' | 'high'
export type DefectType = 'bruise' | 'rot' | 'mold' | 'deformity'
export type BoundingBox = [number, number, number, number]

export interface InspectionResult {
  inspection_id: string
  produce_type: ProduceType
  verdict: Verdict
  confidence: number | null
  bbox: BoundingBox | null
  defect_types: DefectType[]
  severity: Severity
  grade: Grade
  verdict_notes: string[]
  recommended_action: string
  disclaimer: string
}

export interface InspectionHistoryItem {
  inspection_id: string
  created_at: string
  produce_type: ProduceType
  verdict: Verdict
  grade: Grade
  thumbnail_url: string
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

function apiUrl(path: string): string {
  return `${API_BASE_URL}${path}`
}

export async function checkApiHealth(): Promise<boolean> {
  const response = await fetch(apiUrl('/health'))
  return response.ok
}

export async function inspectProduce(file: File): Promise<InspectionResult> {
  const formData = new FormData()
  formData.append('image', file)

  const response = await fetch(apiUrl('/inspections/produce'), {
    method: 'POST',
    body: formData,
  })

  if (!response.ok) {
    const body = (await response.json()) as { detail?: unknown }
    const message =
      typeof body.detail === 'string' ? body.detail : `Inspection failed with status ${response.status}.`
    throw new ApiError(message, response.status)
  }

  return (await response.json()) as InspectionResult
}

export async function listInspectionHistory(): Promise<InspectionHistoryItem[]> {
  const response = await fetch(apiUrl('/inspections/history'))
  if (!response.ok) {
    throw new ApiError(`Could not load inspection history (status ${response.status}).`, response.status)
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
