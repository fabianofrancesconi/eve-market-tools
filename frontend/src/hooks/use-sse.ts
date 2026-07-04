import { useState, useEffect, useRef, useCallback } from 'react'

interface SseState<T> {
  progress: number
  message: string
  subMessage: string
  result: T | null
  error: string | null
  isStreaming: boolean
}

export function useSse<T>(url: string | null, trigger?: number): SseState<T> & { cancel: () => void } {
  const [state, setState] = useState<SseState<T>>({
    progress: 0, message: '', subMessage: '', result: null, error: null, isStreaming: false
  })
  const esRef = useRef<EventSource | null>(null)

  useEffect(() => {
    if (!url) {
      setState(s => ({ ...s, isStreaming: false }))
      return
    }

    setState({ progress: 0, message: '', subMessage: '', result: null, error: null, isStreaming: true })
    const es = new EventSource(url, { withCredentials: true })
    esRef.current = es

    es.addEventListener('progress', (e) => {
      try {
        const data = JSON.parse(e.data)
        setState(s => ({ ...s, progress: data.pct, message: data.msg || '', subMessage: data.sub || '' }))
      } catch {
        setState(s => ({ ...s, error: 'Invalid server response', isStreaming: false }))
        es.close()
      }
    })

    es.addEventListener('result', (e) => {
      try {
        const data = JSON.parse(e.data)
        setState(s => ({ ...s, result: data, isStreaming: false, progress: 100 }))
      } catch {
        setState(s => ({ ...s, error: 'Invalid result data', isStreaming: false }))
      }
      es.close()
    })

    es.addEventListener('error', (e) => {
      const data = (e as MessageEvent).data
      if (data) {
        // Server-sent `event: error` frame carrying a message.
        let msg = 'Scan failed'
        try { msg = JSON.parse(data).message || msg } catch { /* keep default */ }
        setState(s => ({ ...s, error: msg, isStreaming: false }))
      } else {
        // Native connection error — ignore once a result has arrived.
        setState(s => (s.result ? { ...s, isStreaming: false } : { ...s, error: 'Connection lost', isStreaming: false }))
      }
      es.close()
    })

    return () => { es.close() }
  }, [url, trigger])

  const cancel = useCallback(() => {
    esRef.current?.close()
    setState(s => ({ ...s, isStreaming: false }))
  }, [])

  return { ...state, cancel }
}
