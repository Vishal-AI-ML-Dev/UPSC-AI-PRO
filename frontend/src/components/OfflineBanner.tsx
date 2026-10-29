import { useOnlineStatus } from "../lib/useOnlineStatus"

// Sticky banner shown only while the browser reports it is offline.
export function OfflineBanner() {
  const online = useOnlineStatus()
  if (online) return null
  return (
    <div
      role="status"
      aria-live="polite"
      className="sticky top-0 z-[60] bg-danger px-4 py-2 text-center text-sm font-medium text-white"
    >
      {"\u26A0\uFE0F"} You&rsquo;re offline — some features may not work until your
      connection returns.
    </div>
  )
}
