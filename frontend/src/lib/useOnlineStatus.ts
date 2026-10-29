import { useEffect, useState } from "react"

// Tracks browser connectivity via the online/offline events so the UI can warn
// the user when the network drops (the app is API-driven and needs it).
export function useOnlineStatus(): boolean {
  const [online, setOnline] = useState<boolean>(
    typeof navigator !== "undefined" ? navigator.onLine : true,
  )
  useEffect(() => {
    const goOnline = () => setOnline(true)
    const goOffline = () => setOnline(false)
    window.addEventListener("online", goOnline)
    window.addEventListener("offline", goOffline)
    return () => {
      window.removeEventListener("online", goOnline)
      window.removeEventListener("offline", goOffline)
    }
  }, [])
  return online
}
