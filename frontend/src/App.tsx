import { CircleCheck, Leaf, Moon, Sun, TriangleAlert } from 'lucide-react'
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

function App() {
  const [apiStatus, setApiStatus] = useState<ApiStatus>('checking')
  const [theme, setTheme] = useState<Theme>(getInitialTheme)
  const { toasts, dismissToast } = useInspectionJobs()

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
