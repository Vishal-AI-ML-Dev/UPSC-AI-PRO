// Collision-resistant random id (RFC 4122 v4 format). Uses crypto.randomUUID
// where available (secure contexts) and otherwise builds one from
// crypto.getRandomValues, which every supported browser exposes.
export function randomId(): string {
  const c = globalThis.crypto
  if (typeof c?.randomUUID === "function") return c.randomUUID()
  const b = c.getRandomValues(new Uint8Array(16))
  b[6] = (b[6] & 0x0f) | 0x40 // version 4
  b[8] = (b[8] & 0x3f) | 0x80 // RFC 4122 variant
  const hex = Array.from(b, (x) => x.toString(16).padStart(2, "0")).join("")
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`
}
