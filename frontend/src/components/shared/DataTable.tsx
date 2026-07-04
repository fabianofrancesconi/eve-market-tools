import { useState, useMemo } from 'react'

interface Column<T> {
  key: string
  header: string
  render?: (row: T) => React.ReactNode
  sortable?: boolean
  align?: 'left' | 'right' | 'center'
  width?: string
}

interface Props<T> {
  data: T[]
  columns: Column<T>[]
  onRowClick?: (row: T) => void
  keyFn: (row: T) => string | number
  emptyMessage?: string
}

type SortDir = 'asc' | 'desc' | null

const alignClass = { left: 'text-left', right: 'text-right', center: 'text-center' } as const

export function DataTable<T extends Record<string, any>>({
  data, columns, onRowClick, keyFn, emptyMessage = 'No data'
}: Props<T>) {
  const [sortKey, setSortKey] = useState<string | null>(null)
  const [sortDir, setSortDir] = useState<SortDir>(null)

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
    if (sortKey !== key) {
      setSortKey(key)
      setSortDir('desc')
    } else if (sortDir === 'desc') {
      setSortDir('asc')
    } else {
      setSortKey(null)
      setSortDir(null)
    }
  }

  if (!data.length) {
    return <p className="text-center text-foreground-muted py-8">{emptyMessage}</p>
  }

  return (
    <div className="overflow-x-auto rounded border border-border">
      <table className="w-full text-sm">
        <thead>
          <tr className="bg-background-elevated border-b border-border">
            {columns.map(col => (
              <th
                key={col.key}
                className={`px-3 py-2 font-medium text-foreground-muted whitespace-nowrap ${
                  col.sortable !== false ? 'cursor-pointer hover:text-foreground select-none' : ''
                } ${alignClass[col.align || 'left']}`}
                style={col.width ? { width: col.width } : undefined}
                onClick={() => col.sortable !== false && handleSort(col.key)}
              >
                {col.header}
                {sortKey === col.key && (
                  <span className="ml-1">{sortDir === 'asc' ? '↑' : '↓'}</span>
                )}
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
              }`}
              onClick={() => onRowClick?.(row)}
            >
              {columns.map(col => (
                <td key={col.key} className={`px-3 py-2 ${alignClass[col.align || 'left']}`}>
                  {col.render ? col.render(row) : (row[col.key] ?? '-')}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
