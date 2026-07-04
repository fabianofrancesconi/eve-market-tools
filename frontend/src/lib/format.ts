export function fmtIsk(value: number | null | undefined): string {
  if (value == null) return '-'
  if (Math.abs(value) >= 1_000_000_000) {
    return (value / 1_000_000_000).toFixed(2) + 'B'
  }
  if (Math.abs(value) >= 1_000_000) {
    return (value / 1_000_000).toFixed(1) + 'M'
  }
  if (Math.abs(value) >= 1_000) {
    return (value / 1_000).toFixed(1) + 'K'
  }
  return value.toFixed(0)
}

export function fmtNum(value: number | null | undefined, decimals = 0): string {
  if (value == null) return '-'
  return value.toLocaleString(undefined, {
    minimumFractionDigits: decimals,
    maximumFractionDigits: decimals,
  })
}

export function fmtPct(value: number | null | undefined, decimals = 1): string {
  if (value == null) return '-'
  return value.toFixed(decimals) + '%'
}

export function fmtDuration(seconds: number | null | undefined): string {
  if (seconds == null) return '-'
  const h = Math.floor(seconds / 3600)
  const m = Math.floor((seconds % 3600) / 60)
  if (h > 24) {
    const d = Math.floor(h / 24)
    return `${d}d ${h % 24}h`
  }
  return h > 0 ? `${h}h ${m}m` : `${m}m`
}

export function fmtAge(seconds: number | null | undefined): string {
  if (seconds == null) return '-'
  const h = Math.floor(seconds / 3600)
  if (h > 24) {
    const d = Math.floor(h / 24)
    return `${d}d`
  }
  return `${h}h`
}
