import { useEffect, useRef, useState } from "react"
import { useNavigate, useSearchParams } from "react-router-dom"
import { api } from "../lib/api"
import { useAuth } from "../lib/auth"
import { Button, Card } from "../components/ui"

export function VerifyEmail() {
  const [params] = useSearchParams()
  const navigate = useNavigate()
  const { refreshUser } = useAuth()
  const [status, setStatus] = useState<"working" | "ok" | "error">("working")
  const [msg, setMsg] = useState("Verifying your email…")
  const ran = useRef(false)

  useEffect(() => {
    if (ran.current) return
    ran.current = true
    const token = params.get("verify_token") || params.get("token")
    if (!token) {
      setStatus("error")
      setMsg("Missing verification token. Please use the link from your email.")
      return
    }
    void (async () => {
      try {
        const res = await api.verifyEmail(token)
        await refreshUser()
        setStatus("ok")
        setMsg(res.message || "Email verified. You are now signed in.")
        setTimeout(() => navigate("/app", { replace: true }), 1200)
      } catch (err) {
        setStatus("error")
        setMsg(err instanceof Error ? err.message : "Verification failed.")
      }
    })()
  }, [params, navigate, refreshUser])

  return (
    <div className="grid min-h-screen place-items-center px-4">
      <Card className="w-full max-w-sm text-center">
        <div className="mb-3 text-4xl">
          {status === "ok" ? "✅" : status === "error" ? "⚠️" : "⏳"}
        </div>
        <p className="text-sm text-fg">{msg}</p>
        {status === "error" && (
          <Button className="mt-4 w-full" onClick={() => navigate("/login")}>
            Back to sign in
          </Button>
        )}
      </Card>
    </div>
  )
}
