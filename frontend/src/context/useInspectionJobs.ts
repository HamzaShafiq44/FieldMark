import { useContext } from 'react'
import { JobsContext, type JobsContextValue } from './jobsContext'

export function useInspectionJobs(): JobsContextValue {
  const context = useContext(JobsContext)
  if (!context) throw new Error('useInspectionJobs must be used within InspectionJobsProvider')
  return context
}
