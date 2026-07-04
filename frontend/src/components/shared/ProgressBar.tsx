interface Props {
  progress: number
  message?: string
}

export function ProgressBar({ progress, message }: Props) {
  return (
    <div className="space-y-1">
      <div
        className="h-2 rounded-full bg-background-elevated overflow-hidden"
        role="progressbar"
        aria-valuenow={Math.min(100, progress)}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-label={message || 'Loading'}
      >
        <div
          className="h-full rounded-full bg-accent-cyan transition-all duration-300"
          style={{ width: `${Math.min(100, progress)}%` }}
        />
      </div>
      {message && <p className="text-xs text-foreground-muted">{message}</p>}
    </div>
  )
}
