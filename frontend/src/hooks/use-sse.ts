import { useState, useEffect, useRef, useCallback } from 'react'

interface SseState<T> {
  progress: number
  message: string
  result: T | null
  error: string | null
  isStreaming: boolean
}

export function useSse<T>(url: string | null, trigger?: number): SseState<T> & { cancel: () => void } {
  const [state, setState] = useState<SseState<T>>({
    progress: 0, message: '', result: null, error: null, isStreaming: false
  })
  const esRef = useRef<EventSource | null>(null)

  useEffect(() => {
    if (!url) {
      setState(s => ({ ...s, isStreaming: false }))
      return
    }

    setState({ progress: 0, message: '', result: null, error: null, isStreaming: true })
    const es = new EventSource(url, { withCredentials: true })
    esRef.current = es

    es.addEventListener('progress', (e) => {
      try {
        const data = JSON.parse(e.data)
        setState(s => ({ ...s, progress: data.pct, message: data.msg || '' }))
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

    es.onerror = () => {
      setState(s => ({ ...s, error: 'Connection lost', isStreaming: false }))
      es.close()
    }

    return () => { es.close() }
  }, [url, trigger])

  const cancel = useCallback(() => {
    esRef.current?.close()
    setState(s => ({ ...s, isStreaming: false }))
  }, [])

  return { ...state, cancel }
}
