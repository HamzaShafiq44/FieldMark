import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import {
  ApiError,
  listJobs,
  submitImageJob,
  submitVoiceJob,
  type InspectionJob,
  type InspectionResult,
  type JobStatus,
} from '../services/api'
import { JobsContext, type JobsContextValue, type JobToast } from './jobsContext'

const POLL_INTERVAL_MS = 2_000
const TOAST_TIMEOUT_MS = 5_000
const TERMINAL_STATUSES: JobStatus[] = ['completed', 'failed']

function toastForJob(job: InspectionJob, id: number): JobToast {
  if (job.status === 'failed') {
    return { id, message: job.error || 'Inspection failed.', kind: 'error' }
  }
  const product = job.result?.detected_item?.trim() || job.result?.produce_type || 'Inspection'
  return { id, message: `${product} inspection ready.`, kind: 'success' }
}

/** Tracks background inspections above the router so navigation never loses them. */
export function InspectionJobsProvider({ children }: { children: ReactNode }) {
  const [jobs, setJobs] = useState<InspectionJob[]>([])
  const [lastCompleted, setLastCompleted] = useState<InspectionResult | null>(null)
  const [toasts, setToasts] = useState<JobToast[]>([])
  const [submitError, setSubmitError] = useState<string | null>(null)
  const [dismissedFailures, setDismissedFailures] = useState<Set<string>>(new Set())
  const previousStatuses = useRef<Record<string, JobStatus>>({})
  const previousReportReady = useRef<Record<string, boolean>>({})
  const nextToastId = useRef(0)

  const pushToast = useCallback((toast: JobToast) => {
    setToasts((current) => [...current, toast])
    window.setTimeout(() => {
      setToasts((current) => current.filter((item) => item.id !== toast.id))
    }, TOAST_TIMEOUT_MS)
  }, [])

  const applyJobs = useCallback(
    (incoming: InspectionJob[], allowHandoff: boolean = true) => {
      setJobs(incoming)
      for (const job of incoming) {
        const previous = previousStatuses.current[job.job_id]
        const hadReport = previousReportReady.current[job.job_id] ?? false
        // Only hand off results that finish during this page session. Loads of
        // older jobs on refresh must not reopen a stale result panel.
        const justCompleted = Boolean(
          previous && previous !== job.status && TERMINAL_STATUSES.includes(job.status),
        )
        if (allowHandoff && justCompleted) {
          nextToastId.current += 1
          pushToast(toastForJob(job, nextToastId.current))
        }
        if (
          allowHandoff &&
          job.status === 'completed' &&
          job.result &&
          (justCompleted || (!hadReport && job.report_ready))
        ) {
          setLastCompleted(job.result)
        }
        previousStatuses.current[job.job_id] = job.status
        previousReportReady.current[job.job_id] = job.report_ready
      }
    },
    [pushToast],
  )

  useEffect(() => {
    let active = true
    listJobs()
      .then((initial) => {
        // The first load only establishes baselines; a stale result must not
        // reopen the panel after a browser refresh.
        if (active) applyJobs(initial, false)
      })
      .catch(() => {
        // Progress is best-effort; the inspection itself keeps running.
      })
    return () => {
      active = false
    }
  }, [applyJobs])

  const activeJobs = jobs.filter(
    (job) => job.status === 'queued' || job.status === 'processing' || job.stage === 'report',
  )
  const activeCount = activeJobs.length
  const activeJob = activeJobs[0] ?? null

  useEffect(() => {
    if (activeCount === 0) return
    const timer = window.setInterval(() => {
      listJobs()
        .then(applyJobs)
        .catch(() => {})
    }, POLL_INTERVAL_MS)
    return () => window.clearInterval(timer)
  }, [activeCount, applyJobs])

  const startJob = useCallback(async (submit: () => Promise<InspectionJob>) => {
    setSubmitError(null)
    try {
      const job = await submit()
      setJobs((current) => [job, ...current])
      previousStatuses.current[job.job_id] = job.status
      previousReportReady.current[job.job_id] = job.report_ready
    } catch (caught: unknown) {
      const message =
        caught instanceof ApiError || caught instanceof Error
          ? caught.message
          : 'Could not queue the inspection.'
      setSubmitError(message)
      throw caught
    }
  }, [])

  const startImageJob = useCallback(
    (file: File) => startJob(() => submitImageJob(file)),
    [startJob],
  )
  const startVoiceJob = useCallback(
    (file: File) => startJob(() => submitVoiceJob(file)),
    [startJob],
  )
  const clearLastCompleted = useCallback(() => setLastCompleted(null), [])
  const clearSubmitError = useCallback(() => setSubmitError(null), [])
  const latestFailed = jobs[0]?.status === 'failed' ? jobs[0] : null
  const jobError =
    latestFailed && !dismissedFailures.has(latestFailed.job_id) ? latestFailed.error : null
  const clearJobError = useCallback(() => {
    if (latestFailed) {
      setDismissedFailures((current) => new Set(current).add(latestFailed.job_id))
    }
  }, [latestFailed])
  const dismissToast = useCallback(
    (id: number) => setToasts((current) => current.filter((toast) => toast.id !== id)),
    [],
  )

  const value = useMemo<JobsContextValue>(
    () => ({
      jobs,
      activeCount,
      activeJob,
      lastCompleted,
      clearLastCompleted,
      toasts,
      dismissToast,
      submitError,
      clearSubmitError,
      jobError,
      clearJobError,
      startImageJob,
      startVoiceJob,
    }),
    [
      jobs,
      activeCount,
      activeJob,
      lastCompleted,
      clearLastCompleted,
      toasts,
      dismissToast,
      submitError,
      clearSubmitError,
      jobError,
      clearJobError,
      startImageJob,
      startVoiceJob,
    ],
  )

  return <JobsContext.Provider value={value}>{children}</JobsContext.Provider>
}
