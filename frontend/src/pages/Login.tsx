import { useState, type FormEvent } from "react"
import { Link } from "react-router-dom"
import { useAuth } from "../lib/auth"
import { Button, Card, Input } from "../components/ui"
import { ThemeToggle } from "../components/ThemeToggle"

export function Login() {
  const { login, register, notice } = useAuth()
  const [mode, setMode] = useState<"login" | "register">("login")
  const [email, setEmail] = useState("")
  const [password, setPassword] = useState("")
  const [name, setName] = useState("")
  const [msg, setMsg] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  async function submit(e: FormEvent) {
    e.preventDefault()
    setMsg(null)
    setBusy(true)
    try {
      if (mode === "login") {
        await login(email, password)
      } else {
        const res = await register(email, password, name)
        if (res.verification_required) {
          setMsg(
            res.message ||
              "Account created. Please verify your email before signing in.",
          )
          setMode("login")
        }
      }
    } catch (err) {
      setMsg(err instanceof Error ? err.message : "Something went wrong")
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="relative grid min-h-screen place-items-center px-4">
      <div className="absolute inset-x-0 top-0 flex items-center justify-end px-4 py-4">
        <ThemeToggle />
      </div>
      <Card className="w-full max-w-sm">
        <Link
          to="/"
          className="mb-1 block text-center text-2xl font-extrabold transition hover:opacity-80"
          title="Go to UPSC AI home"
        >
          UPSC<span className="text-brand-400">AI</span>
        </Link>
        <p className="mb-5 text-center text-sm text-muted">
          Sign in or create your account
        </p>
        <div className="mb-4 grid grid-cols-2 gap-1 rounded-lg bg-surface2 p-1 text-sm">
          <button
            type="button"
            className={`rounded-md py-1.5 ${
              mode === "login" ? "bg-brand text-white" : "text-muted"
            }`}
            onClick={() => setMode("login")}
          >
            Sign In
          </button>
          <button
            type="button"
            className={`rounded-md py-1.5 ${
              mode === "register" ? "bg-brand text-white" : "text-muted"
            }`}
            onClick={() => setMode("register")}
          >
            Create Account
          </button>
        </div>
        {notice && (
          <p
            role="status"
            data-testid="session-notice"
            className="mb-3 rounded-md border border-border bg-surface2 px-3 py-2 text-center text-sm text-fg"
          >
            {notice}
          </p>
        )}
        <form onSubmit={submit} className="space-y-3">
          {mode === "register" && (
            <div>
              <label className="label">Name</label>
              <Input
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder="Your name"
              />
            </div>
          )}
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
          <div>
            <label className="label">Password</label>
            <Input
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              placeholder="••••••••"
              required
            />
          </div>
          <Button type="submit" className="w-full" disabled={busy}>
            {busy ? "Please wait…" : mode === "login" ? "Sign In" : "Create Account"}
          </Button>
        </form>
        {msg && <p className="mt-3 text-center text-sm text-warning">{msg}</p>}
        {mode === "login" && (
          <div className="mt-3 text-center">
            <Link to="/forgot-password" className="text-sm text-muted hover:text-fg">
              Forgot password?
            </Link>
          </div>
        )}
        <div className="mt-3 text-center">
          <Link
            to="/"
            className="text-sm font-medium text-muted transition hover:text-fg"
            title="Back to home"
          >
            {"\u2190"} Back to home
          </Link>
        </div>
      </Card>
    </div>
  )
}
