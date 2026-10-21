import { useState, type ReactNode } from "react";

export interface Column<Row> {
  key: string;
  /** The header says the unit, e.g. "Duration (s)". */
  header: string;
  render: (row: Row) => ReactNode;
  sortValue?: (row: Row) => string | number;
}

export interface DataTableProps<Row> {
  caption: string;
  columns: Column<Row>[];
  rows: Row[];
  rowKey: (row: Row) => string;
  empty: ReactNode;
}

/** Sortable table in a keyboard-scrollable region so it never breaks the layout on narrow viewports. */
export function DataTable<Row>({ caption, columns, rows, rowKey, empty }: DataTableProps<Row>) {
  const [sort, setSort] = useState<{ key: string; dir: "ascending" | "descending" } | null>(null);
  if (rows.length === 0) return <>{empty}</>;
  const col = sort ? columns.find((c) => c.key === sort.key) : undefined;
  const view =
    col?.sortValue && sort
      ? [...rows].sort((a, b) => {
          const x = col.sortValue!(a);
          const y = col.sortValue!(b);
          const c = x < y ? -1 : x > y ? 1 : 0;
          return sort.dir === "ascending" ? c : -c;
        })
      : rows;
  return (
    <div role="region" aria-label={caption} tabIndex={0} style={{ overflowX: "auto" }}>
      <table style={{ fontSize: "var(--font-table)" }}>
        <caption>{caption}</caption>
        <thead>
          <tr>
            {columns.map((c) => (
              <th
                key={c.key}
                scope="col"
                aria-sort={sort?.key === c.key ? sort.dir : c.sortValue ? "none" : undefined}
              >
                {c.sortValue ? (
                  <button
                    type="button"
                    style={{
                      minBlockSize: "var(--target-min)",
                      minInlineSize: "var(--target-min)",
                    }}
                    onClick={() =>
                      setSort({
                        key: c.key,
                        dir:
                          sort?.key === c.key && sort.dir === "ascending"
                            ? "descending"
                            : "ascending",
                      })
                    }
                  >
                    {c.header}
                    {sort?.key === c.key ? (
                      <span aria-hidden="true">{sort.dir === "ascending" ? " ▲" : " ▼"}</span>
                    ) : null}
                  </button>
                ) : (
                  c.header
                )}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {view.map((r) => (
            <tr key={rowKey(r)}>
              {columns.map((c) => (
                <td key={c.key} data-label={c.header}>
                  {c.render(r)}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
