export interface Crumb {
  label: string;
  href?: string;
}

/** The user's location is always visible; the last crumb is the current page. */
export function Breadcrumbs({ items }: { items: Crumb[] }) {
  return (
    <nav aria-label="Breadcrumb">
      <ol>
        {items.map((c, i) => {
          const last = i === items.length - 1;
          return (
            <li key={`${i}-${c.label}`}>
              {last || !c.href ? (
                <span aria-current={last ? "page" : undefined}>{c.label}</span>
              ) : (
                <a href={c.href}>{c.label}</a>
              )}
            </li>
          );
        })}
      </ol>
    </nav>
  );
}
