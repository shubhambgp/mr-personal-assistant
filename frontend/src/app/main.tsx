import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter } from 'react-router-dom'

import { AuthProvider } from '@/features/auth/AuthProvider'
import '@/styles/index.css'

import App from './App'
import { ErrorBoundary } from './ErrorBoundary'
import { ThemeProvider } from './providers/ThemeProvider'

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <ThemeProvider>
      <AuthProvider>
        <BrowserRouter>
          {/* Inside ThemeProvider so the fallback card gets the tokens; around
              everything else so no crash below can white-screen the app. */}
          <ErrorBoundary>
            <App />
          </ErrorBoundary>
        </BrowserRouter>
      </AuthProvider>
    </ThemeProvider>
  </StrictMode>,
)
