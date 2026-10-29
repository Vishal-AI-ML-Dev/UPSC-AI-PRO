import { useEffect } from "react"
import type { ButtonHTMLAttributes, InputHTMLAttributes, ReactNode } from "react"

export function Button({
  variant = "brand",
  className = "",
  children,
  ...props
}: ButtonHTMLAttributes<HTMLButtonElement> & { variant?: "brand" | "ghost" }) {
  const v = variant === "brand" ? "btn-brand" : "btn-ghost"
  return (
    <button className={`btn ${v} ${className}`} {...props}>
      {children}
    </button>
  )
}

export function Input(props: InputHTMLAttributes<HTMLInputElement>) {
  return <input className="input" {...props} />
}

export function Card({
  children,
  className = "",
}: {
  children: ReactNode
  className?: string
}) {
  return <div className={`card p-5 ${className}`}>{children}</div>
}

export function Spinner({ label }: { label?: string }) {
  return (
    <span className="inline-flex items-center gap-2 text-muted">
      <span className="h-4 w-4 animate-spin rounded-full border-2 border-border border-t-brand-400" />
      {label}
    </span>
  )
}

export function Skeleton({ className = "" }: { className?: string }) {
  return (
    <div className={`animate-pulse rounded-md bg-surface2 ${className}`} aria-hidden />
  )
}

export function ErrorState({
  message = "Something went wrong.",
  onRetry,
}: {
  message?: string
  onRetry?: () => void
}) {
  return (
    <Card className="flex flex-col items-center gap-3 text-center text-muted">
      <span>{message}</span>
      {onRetry && (
        <Button variant="ghost" onClick={onRetry}>
          Try again
        </Button>
      )}
    </Card>
  )
}

export function EmptyState({ title, hint }: { title: string; hint?: string }) {
  return (
    <div className="grid place-items-center py-12 text-center">
      <p className="font-medium text-fg">{title}</p>
      {hint && <p className="mt-1 text-sm text-muted">{hint}</p>}
    </div>
  )
}

export function Modal({
  open,
  title,
  onClose,
  children,
}: {
  open: boolean
  title: string
  onClose: () => void
  children: ReactNode
}) {
  useEffect(() => {
    if (!open) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose()
    }
    window.addEventListener("keydown", onKey)
    return () => window.removeEventListener("keydown", onKey)
  }, [open, onClose])

  if (!open) return null
  return (
    <div
      className="fixed inset-0 z-[70] grid place-items-center bg-black/50 p-4"
      role="dialog"
      aria-modal="true"
      aria-label={title}
      onClick={onClose}
    >
      <div className="card w-full max-w-md p-5" onClick={(e) => e.stopPropagation()}>
        <div className="mb-3 flex items-center justify-between">
          <h2 className="text-lg font-extrabold text-fg">{title}</h2>
          <button
            onClick={onClose}
            aria-label="Close dialog"
            className="grid h-8 w-8 place-items-center rounded-lg text-muted hover:bg-surface2 hover:text-fg"
          >
            {"\u2715"}
          </button>
        </div>
        {children}
      </div>
    </div>
  )
}
