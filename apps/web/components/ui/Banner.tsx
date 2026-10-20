export type BannerKind = "info" | "success" | "warning" | "error";
const ICON: Record<BannerKind, string> = { info: "ℹ", success: "✓", warning: "!", error: "✕" };

/** Status banner: text + icon, announced politely (errors assertively). */
export function Banner({ kind, children }: { kind: BannerKind; children: string }) {
  return (
    <div role={kind === "error" ? "alert" : "status"} data-kind={kind}>
      <span aria-hidden="true">{ICON[kind]}</span> {children}
    </div>
  );
}
