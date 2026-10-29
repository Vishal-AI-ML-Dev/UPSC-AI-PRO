import { useState } from "react"
import { getTheme, setTheme, type Theme } from "../lib/theme"

export function ThemeToggle({ className = "" }: { className?: string }) {
  const [theme, setThemeState] = useState<Theme>(() => getTheme())

  function toggle() {
    const next: Theme = theme === "dark" ? "light" : "dark"
    setTheme(next)
    setThemeState(next)
  }

  return (
    <button
      type="button"
      onClick={toggle}
      aria-label="Toggle theme"
      title={theme === "dark" ? "Switch to light" : "Switch to dark"}
      className={
        "grid h-9 w-9 shrink-0 place-items-center rounded-full border border-border bg-surface text-fg transition hover:border-brand-400 " +
        className
      }
    >
      {theme === "dark" ? "\u2600\uFE0F" : "\u{1F319}"}
    </button>
  )
}
