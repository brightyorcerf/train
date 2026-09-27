/** Middle-truncate an address or hash for display; short strings pass through untouched. */
export const short = (a: string, head = 10, tail = 6) =>
  a.length > head + tail + 2 ? `${a.slice(0, head)}…${a.slice(-tail)}` : a

/** Unix seconds -> `YYYY-MM-DD HH:MM:SS` (UTC). */
export const ts = (t: number) => new Date(t * 1000).toISOString().replace('T', ' ').slice(0, 19)
