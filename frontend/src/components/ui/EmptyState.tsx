/**
 * Empty state placeholder used when a list query returns zero results.
 *
 * Intentionally minimal — title and supporting copy only. Callers can
 * pass optional action JSX (e.g., a "clear filters" link) via children.
 */

interface EmptyStateProps {
  title: string;
  description?: string;
  children?: React.ReactNode;
}

export default function EmptyState({
  title,
  description,
  children,
}: EmptyStateProps) {
  return (
    <div className="flex flex-col items-center justify-center gap-2 rounded-lg border border-dashed border-border bg-surface/50 px-6 py-12 text-center">
      <p className="text-lg font-semibold text-foreground">{title}</p>
      {description ? (
        <p className="max-w-prose text-sm text-muted">{description}</p>
      ) : null}
      {children}
    </div>
  );
}
