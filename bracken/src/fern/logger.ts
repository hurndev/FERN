type Details = Record<string, unknown>

const VERBOSE_KEY = 'fern:verbose'

function verboseEnabled(): boolean {
  try {
    return import.meta.env.DEV || window.localStorage.getItem(VERBOSE_KEY) === 'true'
  } catch {
    return import.meta.env.DEV
  }
}

function write(
  level: 'debug' | 'info' | 'warn' | 'error',
  scope: string,
  message: string,
  details?: Details,
): void {
  const prefix = `[FERN:${scope}] ${message}`
  if (details && Object.keys(details).length > 0) console[level](prefix, details)
  else console[level](prefix)
}

export const log = {
  debug(scope: string, message: string, details?: Details): void {
    if (verboseEnabled()) write('debug', scope, message, details)
  },
  info(scope: string, message: string, details?: Details): void {
    write('info', scope, message, details)
  },
  warn(scope: string, message: string, details?: Details): void {
    write('warn', scope, message, details)
  },
  error(scope: string, message: string, details?: Details): void {
    write('error', scope, message, details)
  },
}

export function shortId(value: string, length = 12): string {
  return value.length <= length ? value : `${value.slice(0, length)}…`
}

export function setVerboseLogging(enabled: boolean): void {
  window.localStorage.setItem(VERBOSE_KEY, String(enabled))
  log.info('logging', `verbose browser logging ${enabled ? 'enabled' : 'disabled'}`)
}
