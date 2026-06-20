const FALLBACK_HINTS = ['ws://localhost:8765']

function parseHints(value: string | undefined): string[] {
  if (!value) return FALLBACK_HINTS
  const parsed = [
    ...new Set(
      value
        .split(/[\s,]+/)
        .map((s) => s.trim())
        .filter(Boolean),
    ),
  ]
  return parsed.length > 0 ? parsed : FALLBACK_HINTS
}

export const DEFAULT_RELAY_HINTS: string[] = parseHints(
  import.meta.env.VITE_RELAY_URL as string | undefined,
)
