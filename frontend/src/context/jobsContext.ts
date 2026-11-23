import { createContext } from 'react'
import type { InspectionJob, InspectionResult } from '../services/api'

export interface JobToast {
  id: number
  message: string
  kind: 'success' | 'error'
}

export interface JobsContextValue {
  jobs: InspectionJob[]
  activeCount: number
  activeJob: InspectionJob | null
  lastCompleted: InspectionResult | null
  clearLastCompleted: () => void
  toasts: JobToast[]
  dismissToast: (id: number) => void
  submitError: string | null
  clearSubmitError: () => void
  jobError: string | null
  clearJobError: () => void
  startImageJob: (file: File) => Promise<void>
  startVoiceJob: (file: File) => Promise<void>
}

export const JobsContext = createContext<JobsContextValue | null>(null)
