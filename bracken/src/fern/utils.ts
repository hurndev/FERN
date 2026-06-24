export function toHex(bytes: Uint8Array): string {
  return Array.from(bytes)
    .map((b) => b.toString(16).padStart(2, '0'))
    .join('')
}

export function fromHex(hex: string): Uint8Array {
  const bytes = new Uint8Array(hex.length / 2)
  for (let i = 0; i < hex.length; i += 2) {
    bytes[i / 2] = parseInt(hex.slice(i, i + 2), 16)
  }
  return bytes
}

export async function sha256Hex(data: Uint8Array): Promise<string> {
  const hashBuffer = await crypto.subtle.digest('SHA-256', data.buffer as ArrayBuffer)
  return toHex(new Uint8Array(hashBuffer))
}

export function isValidHex(s: string, length: number): boolean {
  return new RegExp(`^[0-9a-f]{${length}}$`).test(s)
}

export function isValidPubkey(s: string): boolean {
  return isValidHex(s, 64)
}

export function isValidEventId(s: string): boolean {
  return isValidHex(s, 64)
}

export function isValidSig(s: string): boolean {
  return isValidHex(s, 128)
}

export function randomHexId(): string {
  const bytes = new Uint8Array(32)
  crypto.getRandomValues(bytes)
  return toHex(bytes)
}

export function truncateId(id: string, prefix = 8): string {
  if (id.length <= prefix + 4) return id
  return `${id.slice(0, prefix)}\u2026${id.slice(-4)}`
}

export function relativeTime(ts: number): string {
  const now = Date.now() / 1000
  const diff = now - ts
  if (diff < 60) return 'just now'
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`
  if (diff < 604800) return `${Math.floor(diff / 86400)}d ago`
  return new Date(ts * 1000).toLocaleDateString()
}

export function absoluteTime(ts: number): string {
  return new Date(ts * 1000).toLocaleString()
}
