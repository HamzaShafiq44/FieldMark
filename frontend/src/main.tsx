import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter } from 'react-router-dom'
import './index.css'
import App from './App.tsx'
import { InspectionJobsProvider } from './context/InspectionJobsContext.tsx'

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <BrowserRouter>
      <InspectionJobsProvider>
        <App />
      </InspectionJobsProvider>
    </BrowserRouter>
  </StrictMode>,
)
