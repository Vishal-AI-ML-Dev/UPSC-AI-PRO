import { useState, type FormEvent } from "react"
import { useNavigate, useSearchParams } from "react-router-dom"
import { api } from "../lib/api"
import { Button, Card, Input } from "../components/ui"

export function ResetPassword() {
  const [params] = useSearchParams()
  const navigate = useNavigate()
  const token = params.get("reset_token") || params.get("token") || ""
  const [password, setPassword] = useState("")
  const [confirm, setConfirm] = useState("")
  const [msg, setMsg] = useState<string | null>(null)
  const [done, setDone] = useState(false)
  const [busy, setBusy] = useState(false)

  async function submit(e: FormEvent) {
    e.preventDefault()
    setMsg(null)
    if (!token) {
      setMsg("Missing reset token. Please use the link from your email.")
      return
    }
    if (password.length < 8) {
      setMsg("Password must be at least 8 characters.")
      return
    }
    if (password !== confirm) {
      setMsg("Passwords do not match.")
      return
    }
    setBusy(true)
    try {
      const res = await api.resetPassword(token, password)
      setDone(true)
      setMsg(res.message || "Password updated. You can now sign in.")
      setTimeout(() => navigate("/login", { replace: true }), 1500)
    } catch (err) {
      setMsg(err instanceof Error ? err.message : "Reset failed.")
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="grid min-h-screen place-items-center px-4">
      <Card className="w-full max-w-sm">
        <div className="mb-1 text-center text-2xl font-extrabold">
          Set a new password
        </div>
        <p className="mb-5 text-center text-sm text-muted">
          Choose a strong password (at least 8 characters).
        </p>
        <form onSubmit={submit} className="space-y-3">
          <div>
            <label className="label">New password</label>
            <Input
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              placeholder="••••••••"
              required
            />
          </div>
          <div>
            <label className="label">Confirm password</label>
            <Input
              type="password"
              value={confirm}
              onChange={(e) => setConfirm(e.target.value)}
              placeholder="••••••••"
              required
            />
          </div>
          <Button type="submit" className="w-full" disabled={busy || done}>
            {busy ? "Updating…" : "Update password"}
          </Button>
        </form>
        {msg && <p className="mt-3 text-center text-sm text-brand-400">{msg}</p>}
      </Card>
    </div>
  )
}
