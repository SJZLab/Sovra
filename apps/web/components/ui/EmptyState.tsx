import type { ReactNode } from "react";

/** Explains why it is empty and gives the first action (NN/g; section 72.2: never a fabricated model). */
export function EmptyState({
  title,
  why,
  action,
}: {
  title: string;
  why: string;
  action: ReactNode;
}) {
  return (
    <section aria-label={title}>
      <h2>{title}</h2>
      <p>{why}</p>
      <div>{action}</div>
    </section>
  );
}
