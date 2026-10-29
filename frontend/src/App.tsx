import { Navigate, Route, Routes, useSearchParams } from "react-router-dom"
import { accountKey, useAuth } from "./lib/auth"
import { Login } from "./pages/Login"
import { Landing } from "./pages/Landing"
import { AppLayout } from "./pages/AppLayout"
import { VerifyEmail } from "./pages/VerifyEmail"
import { ForgotPassword } from "./pages/ForgotPassword"
import { ResetPassword } from "./pages/ResetPassword"
import { Spinner } from "./components/ui"
import { OfflineBanner } from "./components/OfflineBanner"
import { NotFound } from "./pages/NotFound"

export default function App() {
  const { user, loading } = useAuth()
  const [params] = useSearchParams()

  if (loading) {
    return (
      <div className="grid min-h-screen place-items-center">
        <Spinner label="Loading…" />
      </div>
    )
  }

  // Email links land on the SPA root with a token query param
  // (frontend_url + "?verify_token=..." / "?reset_token=..."). Honour those
  // first so the link works no matter which path it opens.
  const verifyToken = params.get("verify_token")
  const resetToken = params.get("reset_token")

  return (
    <>
      <OfflineBanner />
      <Routes>
        <Route
          path="/"
          element={
            verifyToken ? (
              <VerifyEmail />
            ) : resetToken ? (
              <ResetPassword />
            ) : user ? (
              <Navigate to="/app" replace />
            ) : (
              <Landing />
            )
          }
        />
        <Route
          path="/login"
          element={user ? <Navigate to="/app" replace /> : <Login />}
        />
        <Route path="/verify-email" element={<VerifyEmail />} />
        <Route path="/forgot-password" element={<ForgotPassword />} />
        <Route path="/reset-password" element={<ResetPassword />} />
        <Route
          path="/app/*"
          element={
            // Keyed by account AND server session so switching users, or replacing
            // the same user's session, remounts every feature: no in-memory state
            // or pending work from the previous session survives.
            user ? (
              <AppLayout key={`${accountKey(user)}:${String(user.sid ?? "")}`} />
            ) : (
              <Navigate to="/login" replace />
            )
          }
        />
        <Route path="*" element={<NotFound />} />
      </Routes>
    </>
  )
}
