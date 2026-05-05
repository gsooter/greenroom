/**
 * Tiny inline SVG that signals "this link opens an external site."
 *
 * Used next to text on anchors that have ``target="_blank"`` so users
 * (and screen readers, via the optional aria-label slot) know the link
 * leaves the app. Sized in em so it tracks the surrounding font size.
 */

interface ExternalLinkIconProps {
  className?: string;
}

export default function ExternalLinkIcon({
  className,
}: ExternalLinkIconProps): JSX.Element {
  return (
    <svg
      data-testid="external-link-icon"
      aria-hidden="true"
      focusable="false"
      width="1em"
      height="1em"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      className={className}
    >
      <path d="M14 4h6v6" />
      <path d="M20 4 10 14" />
      <path d="M19 13v5a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V7a2 2 0 0 1 2-2h5" />
    </svg>
  );
}
