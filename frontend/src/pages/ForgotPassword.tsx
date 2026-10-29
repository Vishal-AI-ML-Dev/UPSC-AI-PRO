import { useState, type FormEvent } from "react"
import { useNavigate } from "react-router-dom"
import { api } from "../lib/api"
import { Button, Card, Input } from "../components/ui"

export function ForgotPassword() {
  const navigate = useNavigate()
  const [email, setEmail] = useState("")
  const [msg, setMsg] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  async function submit(e: FormEvent) {
    e.preventDefault()
    setBusy(true)
    setMsg(null)
    try {
      const res = await api.forgotPassword(email)
      setMsg(res.message || "If an account exists, a reset link has been sent.")
    } catch (err) {
      setMsg(err instanceof Error ? err.message : "Something went wrong.")
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="grid min-h-screen place-items-center px-4">
      <Card className="w-full max-w-sm">
        <div className="mb-1 text-center text-2xl font-extrabold">Reset password</div>
        <p className="mb-5 text-center text-sm text-muted">
          Enter your email and we&apos;ll send you a reset link.
        </p>
        <form onSubmit={submit} className="space-y-3">
          <div>
            <label className="label">Email</label>
            <Input
              type="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              placeholder="you@example.com"
              required
            />
          </div>
          <Button type="submit" className="w-full" disabled={busy}>
            {busy ? "Sending…" : "Send reset link"}
          </Button>
        </form>
        {msg && <p className="mt-3 text-center text-sm text-brand-400">{msg}</p>}
        <button
          type="button"
          className="mt-4 w-full text-center text-sm text-muted hover:text-fg"
          onClick={() => navigate("/login")}
        >
          Back to sign in
        </button>
      </Card>
    </div>
  )
}
