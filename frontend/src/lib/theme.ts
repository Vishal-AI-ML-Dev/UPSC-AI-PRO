// Lightweight theme helper. The theme is stored in localStorage and applied
// as a `data-theme` attribute on <html>. All colors are CSS variables (see
// index.css), so switching the attribute restyles the entire app instantly.

export type Theme = "light" | "dark"

const KEY = "upscai-theme"

export function getTheme(): Theme {
  const attr = document.documentElement.getAttribute("data-theme")
  if (attr === "dark" || attr === "light") return attr
  try {
    const saved = localStorage.getItem(KEY)
    if (saved === "dark" || saved === "light") return saved
  } catch {
    /* ignore */
  }
  return "light"
}

export function setTheme(theme: Theme): void {
  document.documentElement.setAttribute("data-theme", theme)
  try {
    localStorage.setItem(KEY, theme)
  } catch {
    /* ignore */
  }
}

// Applies the saved theme (default: light). Safe to call before React mounts.
export function initTheme(): void {
  let theme: Theme = "light"
  try {
    const saved = localStorage.getItem(KEY)
    if (saved === "dark" || saved === "light") theme = saved
  } catch {
    /* ignore */
  }
  document.documentElement.setAttribute("data-theme", theme)
}
