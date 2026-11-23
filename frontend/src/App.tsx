import { CircleCheck, Leaf, LoaderCircle, Moon, Sun, TriangleAlert } from 'lucide-react'
import { useEffect, useState } from 'react'
import { Link, NavLink, Route, Routes } from 'react-router-dom'
import './App.css'
import CategoriesPage from './pages/CategoriesPage'
import InspectPage from './pages/InspectPage'
import { useInspectionJobs } from './context/useInspectionJobs.ts'
import { checkApiHealth, type ApiStatus } from './services/api'

const THEME_STORAGE_KEY = 'fieldmark-theme'

type Theme = 'light' | 'dark'

function getInitialTheme(): Theme {
  const savedTheme = window.localStorage.getItem(THEME_STORAGE_KEY)
  if (savedTheme === 'light' || savedTheme === 'dark') return savedTheme
  return window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'
}

function progressForStage(stage: string | undefined): number {
  if (stage === 'validating') return 15
  if (stage === 'analyzing') return 55
  if (stage === 'report') return 85
  return 5
}

function labelForStage(stage: string | undefined, kind: string | undefined): string {
  const voice = kind === 'voice'
  if (stage === 'validating') return 'Uploading…'
  if (stage === 'analyzing') return voice ? 'Reading voice note…' : 'Analyzing photo…'
  if (stage === 'report') return 'Writing report…'
  return 'Processing…'
}

function App() {
  const [apiStatus, setApiStatus] = useState<ApiStatus>('checking')
  const [theme, setTheme] = useState<Theme>(getInitialTheme)
  const { activeCount, activeJob, toasts, dismissToast } = useInspectionJobs()
  const progress = progressForStage(activeJob?.stage)
  const progressLabel = labelForStage(activeJob?.stage, activeJob?.kind)

  useEffect(() => {
    document.documentElement.dataset.theme = theme
    window.localStorage.setItem(THEME_STORAGE_KEY, theme)
  }, [theme])

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

  return (
    <div className="app-shell">
      <header className="topbar">
        <Link className="brand" to="/" aria-label="Fieldmark home">
          <span className="brand-mark">
            <Leaf size={21} strokeWidth={2.2} />
          </span>
          <span className="brand-copy">
            <span className="brand-name">FieldMark</span>
            <span className="brand-caption">PRODUCT QUALITY STUDIO</span>
          </span>
        </Link>
        <nav className="main-nav" aria-label="Primary navigation">
          <NavLink
            to="/"
            end
            className={({ isActive }) => (isActive ? 'nav-link active' : 'nav-link')}
          >
            Inspect
          </NavLink>
          <NavLink
            to="/categories"
            className={({ isActive }) => (isActive ? 'nav-link active' : 'nav-link')}
          >
            Categories
          </NavLink>
        </nav>
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

      {activeCount > 0 && (
        <div className="job-progress-banner" role="status" aria-live="polite">
          <div className="job-progress-heading">
            <span className="job-progress-label">
              <LoaderCircle className="spin" size={15} />
              {progressLabel}
            </span>
            {activeCount > 1 && <span className="job-progress-count">+{activeCount - 1} more</span>}
          </div>
          <div
            className="job-progress-track"
            role="progressbar"
            aria-valuenow={progress}
            aria-valuemin={0}
            aria-valuemax={100}
          >
            <span style={{ width: `${progress}%` }} />
          </div>
        </div>
      )}

      <main>
        <Routes>
          <Route path="/" element={<InspectPage onApiStatusChange={setApiStatus} />} />
          <Route path="/categories" element={<CategoriesPage />} />
        </Routes>
      </main>

      <footer className="footer">
        <span className="footer-brand">
          <Leaf size={15} /> FIELDMARK
        </span>
        <span>Visual quality guidance, not a food safety assessment.</span>
        <span>
          MADE FOR THE FIELD <span className="footer-sun">✳</span>
        </span>
      </footer>
      <div className="job-toasts" aria-live="polite">
        {toasts.map((toast) => (
          <button
            className={`job-toast job-toast-${toast.kind}`}
            key={toast.id}
            type="button"
            onClick={() => dismissToast(toast.id)}
          >
            {toast.kind === 'success' ? <CircleCheck size={16} /> : <TriangleAlert size={16} />}
            <span>{toast.message}</span>
          </button>
        ))}
      </div>
    </div>
  )
}

export default App
