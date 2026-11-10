import { dirFor, resolveLocale, type Locale } from "./phrases";

export type BannerKind = "info" | "success" | "warning" | "error";
const ICON: Record<BannerKind, string> = { info: "ℹ", success: "✓", warning: "!", error: "✕" };

/** Status banner: text + icon, announced politely (errors assertively). The text is the caller's,
 * so `locale` only sets `lang`/`dir` for it (an unknown locale says "en"). */
export function Banner({
  kind,
  children,
  locale = "en",
}: {
  kind: BannerKind;
  children: string;
  locale?: Locale;
}) {
  return (
    <div
      role={kind === "error" ? "alert" : "status"}
      data-kind={kind}
      lang={resolveLocale(locale)}
      dir={dirFor(locale)}
    >
      <span aria-hidden="true">{ICON[kind]}</span> {children}
    </div>
  );
}
