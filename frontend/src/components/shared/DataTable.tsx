import { useState, useMemo } from 'react'

interface Column<T> {
  key: string
  header: string
  render?: (row: T) => React.ReactNode
  sortable?: boolean
  align?: 'left' | 'right' | 'center'
  width?: string
  hiddenByDefault?: boolean
}

interface Props<T> {
  data: T[]
  columns: Column<T>[]
  onRowClick?: (row: T) => void
  keyFn: (row: T) => string | number
  emptyMessage?: string
  rowClassName?: (row: T) => string
  showColumnPicker?: boolean
  /** Initial sort (e.g. restored from persisted prefs). */
  defaultSortKey?: string | null
  defaultSortDir?: SortDir
  /** Notified whenever the user changes the sort, so callers can persist it. */
  onSortChange?: (key: string | null, dir: SortDir) => void
  /** Persisted column order (list of column keys); drag headers to reorder. */
  defaultColOrder?: string[]
  onColOrderChange?: (order: string[]) => void
  /** Persisted column widths in px, keyed by column key; drag the edge to resize. */
  defaultColWidths?: Record<string, number>
  onColWidthsChange?: (widths: Record<string, number>) => void
}

type SortDir = 'asc' | 'desc' | null

const alignClass = { left: 'text-left', right: 'text-right', center: 'text-center' } as const

export function DataTable<T extends Record<string, any>>({
  data, columns, onRowClick, keyFn, emptyMessage = 'No data',
  rowClassName, showColumnPicker = false,
  defaultSortKey = null, defaultSortDir = null, onSortChange,
  defaultColOrder, onColOrderChange, defaultColWidths, onColWidthsChange,
}: Props<T>) {
  const [sortKey, setSortKey] = useState<string | null>(defaultSortKey)
  const [sortDir, setSortDir] = useState<SortDir>(defaultSortKey ? (defaultSortDir ?? 'desc') : null)
  const [hiddenCols, setHiddenCols] = useState<Set<string>>(
    () => new Set(columns.filter(c => c.hiddenByDefault).map(c => c.key))
  )
  const [pickerOpen, setPickerOpen] = useState(false)
  const [colOrder, setColOrder] = useState<string[]>(
    () => (defaultColOrder && defaultColOrder.length ? defaultColOrder : columns.map(c => c.key))
  )
  const [widths, setWidths] = useState<Record<string, number>>(() => defaultColWidths ?? {})
  const [dragKey, setDragKey] = useState<string | null>(null)

  // Apply the persisted order to the column list; unknown/new keys are appended.
  const orderedColumns = useMemo(() => {
    const byKey = new Map(columns.map(c => [c.key, c]))
    const ordered = colOrder.map(k => byKey.get(k)).filter(Boolean) as Column<T>[]
    for (const c of columns) if (!colOrder.includes(c.key)) ordered.push(c)
    return ordered
  }, [columns, colOrder])

  const visibleColumns = useMemo(
    () => orderedColumns.filter(c => !hiddenCols.has(c.key)),
    [orderedColumns, hiddenCols]
  )

  // Effective (possibly resized) px width per column, and the table total so
  // that `table-layout: fixed` honours widths and the container scrolls.
  const effWidth = (c: Column<T>) => widths[c.key] ?? (c.width ? parseInt(c.width, 10) || 120 : 120)
  const totalWidth = visibleColumns.reduce((s, c) => s + effWidth(c), 0)

  const handleDrop = (targetKey: string) => {
    if (!dragKey || dragKey === targetKey) { setDragKey(null); return }
    const base = colOrder.length ? [...colOrder] : columns.map(c => c.key)
    const from = base.indexOf(dragKey)
    const to = base.indexOf(targetKey)
    if (from !== -1 && to !== -1) {
      base.splice(from, 1)
      base.splice(to, 0, dragKey)
      setColOrder(base)
      onColOrderChange?.(base)
    }
    setDragKey(null)
  }

  const startResize = (e: React.MouseEvent, key: string) => {
    e.preventDefault()
    e.stopPropagation()
    const startX = e.clientX
    const th = (e.currentTarget as HTMLElement).parentElement as HTMLElement
    const startWidth = widths[key] ?? th?.getBoundingClientRect().width ?? 100
    const base = widths
    let finalW = startWidth
    const onMove = (ev: MouseEvent) => {
      finalW = Math.max(40, startWidth + (ev.clientX - startX))
      setWidths(prev => ({ ...prev, [key]: finalW }))
    }
    const onUp = () => {
      window.removeEventListener('mousemove', onMove)
      window.removeEventListener('mouseup', onUp)
      // Persist outside any setState updater to avoid updating the parent
      // store while React is mid-render.
      onColWidthsChange?.({ ...base, [key]: finalW })
    }
    window.addEventListener('mousemove', onMove)
    window.addEventListener('mouseup', onUp)
  }

  const sorted = useMemo(() => {
    if (!sortKey || !sortDir) return data
    return [...data].sort((a, b) => {
      const av = a[sortKey], bv = b[sortKey]
      if (av == null && bv == null) return 0
      if (av == null) return 1
      if (bv == null) return -1
      const cmp = av < bv ? -1 : av > bv ? 1 : 0
      return sortDir === 'desc' ? -cmp : cmp
    })
  }, [data, sortKey, sortDir])

  const handleSort = (key: string) => {
    let nextKey: string | null = key
    let nextDir: SortDir = 'desc'
    if (sortKey === key) {
      if (sortDir === 'desc') {
        nextDir = 'asc'
      } else {
        nextKey = null
        nextDir = null
      }
    }
    setSortKey(nextKey)
    setSortDir(nextDir)
    onSortChange?.(nextKey, nextDir)
  }

  const toggleCol = (key: string) => {
    setHiddenCols(prev => {
      const next = new Set(prev)
      if (next.has(key)) next.delete(key)
      else next.add(key)
      return next
    })
  }

  if (!data.length) {
    return <p className="text-center text-foreground-muted py-8">{emptyMessage}</p>
  }

  return (
    <div>
      {showColumnPicker && (
        <div className="relative mb-2 flex justify-end">
          <button
            onClick={() => setPickerOpen(!pickerOpen)}
            onBlur={() => setTimeout(() => setPickerOpen(false), 150)}
            className="px-2 py-1 text-xs rounded border border-border text-foreground-muted hover:text-foreground hover:border-foreground-muted"
          >
            Columns ({visibleColumns.length}/{columns.length})
          </button>
          {pickerOpen && (
            <div className="absolute right-0 top-full mt-1 z-50 bg-background-panel border border-border rounded shadow-lg py-1 max-h-64 overflow-y-auto w-48">
              {columns.map(col => (
                <label
                  key={col.key}
                  className="flex items-center gap-2 px-3 py-1 text-xs cursor-pointer hover:bg-background-elevated"
                  onMouseDown={e => e.preventDefault()}
                >
                  <input
                    type="checkbox"
                    checked={!hiddenCols.has(col.key)}
                    onChange={() => toggleCol(col.key)}
                    className="rounded border-border"
                  />
                  {col.header}
                </label>
              ))}
            </div>
          )}
        </div>
      )}
      <div className="overflow-x-auto rounded border border-border">
        <table className="text-sm" style={{ tableLayout: 'fixed', width: `${totalWidth}px`, minWidth: '100%' }}>
          <thead>
            <tr className="bg-background-elevated border-b border-border">
              {visibleColumns.map(col => (
                <th
                  key={col.key}
                  draggable
                  onDragStart={() => setDragKey(col.key)}
                  onDragOver={e => e.preventDefault()}
                  onDrop={() => handleDrop(col.key)}
                  className={`relative px-3 py-2 font-medium text-foreground-muted whitespace-nowrap ${
                    col.sortable !== false ? 'cursor-pointer hover:text-foreground select-none' : ''
                  } ${alignClass[col.align || 'left']} ${dragKey === col.key ? 'opacity-50' : ''}`}
                  style={{ width: `${effWidth(col)}px` }}
                  onClick={() => col.sortable !== false && handleSort(col.key)}
                  title="Drag to reorder"
                >
                  {col.header}
                  {sortKey === col.key && (
                    <span className="ml-1">{sortDir === 'asc' ? '↑' : '↓'}</span>
                  )}
                  <span
                    onMouseDown={e => startResize(e, col.key)}
                    onClick={e => e.stopPropagation()}
                    className="absolute top-0 right-0 h-full w-1.5 cursor-col-resize hover:bg-accent-cyan/40"
                    title="Drag to resize column"
                  />
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {sorted.map(row => (
              <tr
                key={keyFn(row)}
                className={`border-b border-border/50 hover:bg-background-elevated/50 ${
                  onRowClick ? 'cursor-pointer' : ''
                } ${rowClassName ? rowClassName(row) : ''}`}
                onClick={() => onRowClick?.(row)}
              >
                {visibleColumns.map(col => (
                  <td key={col.key} className={`px-3 py-2 overflow-hidden whitespace-nowrap ${alignClass[col.align || 'left']}`}>
                    {col.render ? col.render(row) : (row[col.key] ?? '-')}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}
