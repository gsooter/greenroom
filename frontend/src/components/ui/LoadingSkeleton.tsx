/**
 * Loading skeleton placeholder — a single rectangular pulse element.
 *
 * Compose several together to approximate the final layout while the
 * real content is being fetched. Accepts a `className` so callers can
 * control the shape (height, width, rounding) without forking the
 * component.
 */

interface LoadingSkeletonProps {
  className?: string;
}

export default function LoadingSkeleton({
  className = "h-4 w-full rounded",
}: LoadingSkeletonProps) {
  return (
    <div
      aria-hidden
      className={"animate-pulse bg-border/80 dark:bg-border " + className}
    />
  );
}
