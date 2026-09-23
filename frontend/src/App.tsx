import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { useState, lazy, Suspense } from 'react'
import { AuthProvider } from '@/context/auth'

const HomePage          = lazy(() => import('./pages/HomePage'))
const LoginPage         = lazy(() => import('./pages/LoginPage'))
const RegisterPage      = lazy(() => import('./pages/RegisterPage'))
const ForgotPasswordPage = lazy(() => import('./pages/ForgotPasswordPage'))
const ResetPasswordPage  = lazy(() => import('./pages/ResetPasswordPage'))
const AuthCallbackPage   = lazy(() => import('./pages/AuthCallbackPage'))
const ChatPage          = lazy(() => import('./pages/ChatPage'))
const SearchPage        = lazy(() => import('./pages/SearchPage'))
const MyCasePage        = lazy(() => import('./pages/MyCasePage'))
const ConnectPage       = lazy(() => import('./pages/ConnectPage'))
const IntakePage        = lazy(() => import('./pages/IntakePage'))
const PrivacyPage       = lazy(() => import('./pages/PrivacyPage'))
const TermsPage         = lazy(() => import('./pages/TermsPage'))

export default function App() {
  const [client] = useState(() => new QueryClient({
    defaultOptions: { queries: { staleTime: 30_000, retry: 1 } },
  }))

  return (
    <QueryClientProvider client={client}>
      <AuthProvider>
        <BrowserRouter>
          <Suspense>
            <Routes>
              <Route path="/"                  element={<HomePage />} />
              <Route path="/login"             element={<LoginPage />} />
              <Route path="/register"          element={<RegisterPage />} />
              <Route path="/forgot-password"   element={<ForgotPasswordPage />} />
              <Route path="/reset-password"    element={<ResetPasswordPage />} />
              <Route path="/auth/callback"     element={<AuthCallbackPage />} />
              <Route path="/chat"              element={<ChatPage />} />
              <Route path="/search"            element={<SearchPage />} />
              <Route path="/my-case"           element={<MyCasePage />} />
              <Route path="/connect"           element={<ConnectPage />} />
              <Route path="/intake"            element={<IntakePage />} />
              <Route path="/privacy"            element={<PrivacyPage />} />
              <Route path="/terms"              element={<TermsPage />} />
              <Route path="*"                  element={<Navigate to="/" replace />} />
            </Routes>
          </Suspense>
        </BrowserRouter>
      </AuthProvider>
    </QueryClientProvider>
  )
}
