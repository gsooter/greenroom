/** Empty state placeholder for lists with no data. */
export default function EmptyState({ message }: { message: string }) {
  return <div>{message}</div>;
}
