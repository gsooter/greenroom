/**
 * Initial-load skeleton for the For-You grid.
 *
 * Renders a fixed number of card-shaped placeholders so the page reserves
 * the same vertical space the real grid will occupy. Avoids the layout
 * shift of toggling from a one-line "loading…" string to a multi-row grid.
 */

import LoadingSkeleton from "@/components/ui/LoadingSkeleton";

interface RecommendationGridSkeletonProps {
  count?: number;
}

export default function RecommendationGridSkeleton({
  count = 6,
}: RecommendationGridSkeletonProps): JSX.Element {
  return (
    <ul
      aria-hidden
      className="grid grid-cols-1 gap-6 sm:grid-cols-2 lg:grid-cols-3"
    >
      {Array.from({ length: count }, (_, i) => (
        <li key={i} className="flex flex-col gap-3">
          <LoadingSkeleton className="aspect-[4/3] w-full rounded-lg" />
          <LoadingSkeleton className="h-4 w-3/4 rounded" />
          <LoadingSkeleton className="h-3 w-1/2 rounded" />
          <div className="mt-1 flex gap-2">
            <LoadingSkeleton className="h-5 w-20 rounded-full" />
            <LoadingSkeleton className="h-5 w-24 rounded-full" />
          </div>
        </li>
      ))}
    </ul>
  );
}
