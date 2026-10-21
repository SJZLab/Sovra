import { dirFor, phrase, type Locale } from "./phrases";

export interface Crumb {
  label: string;
  href?: string;
}

/** WCAG 2.2 2.5.8: each link's own hit area reaches 24px of block size, and a margin between
 * adjacent crumbs keeps 24px of clear space between two targets. */
const LINK_STYLE = {
  display: "inline-block",
  paddingBlock: "4px",
  marginInlineEnd: "var(--space-2)",
};
const CURRENT_STYLE = { display: "inline-block", paddingBlock: "4px" };

/** The user's location is always visible; the last crumb is the current page, never a link
 * even when the caller also gave it an href. */
export function Breadcrumbs({ items, locale = "en" }: { items: Crumb[]; locale?: Locale }) {
  return (
    <nav aria-label={phrase("breadcrumbNav", locale)} lang={locale} dir={dirFor(locale)}>
      <ol>
        {items.map((c, i) => {
          const last = i === items.length - 1;
          return (
            <li key={`${i}-${c.label}`}>
              {last || !c.href ? (
                <span aria-current={last ? "page" : undefined} style={CURRENT_STYLE}>
                  {c.label}
                </span>
              ) : (
                <a href={c.href} style={LINK_STYLE}>
                  {c.label}
                </a>
              )}
            </li>
          );
        })}
      </ol>
    </nav>
  );
}
