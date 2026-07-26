export function fmtSize(n?: number): string {
  let x = n || 0
  const u = ['B', 'KB', 'MB', 'GB', 'TB']
  let i = 0
  while (x >= 1024 && i < u.length - 1) {
    x /= 1024
    i++
  }
  return x.toFixed(i ? 1 : 0) + ' ' + u[i]
}

export function fmtTime(mtime?: number): string {
  if (!mtime) return ''
  try {
    return new Date(mtime * 1000).toLocaleString()
  } catch {
    return ''
  }
}
