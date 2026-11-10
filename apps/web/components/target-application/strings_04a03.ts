import { makeT } from "./common";

/** FTF-04A03 system strings. User data (repository names) is never in here; it is rendered in <bdi>. */
export const STRINGS_04A03 = {
  tokenLabel: { en: "GitHub token", ar: "رمز GitHub" },
  tokenRequired: {
    en: "Enter a GitHub token to load repositories.",
    ar: "أدخل رمز GitHub لتحميل المستودعات.",
  },
  loadRepositories: { en: "Load repositories", ar: "تحميل المستودعات" },
  loadingRepositories: { en: "Loading repositories…", ar: "جارٍ تحميل المستودعات…" },
  loadMore: { en: "Load more", ar: "تحميل المزيد" },
  privateMark: { en: "private", ar: "خاص" },
  selectedMark: { en: "Selected", ar: "محدد" },
  selectedNotice: { en: "Selected:", ar: "المحدد:" },
  listLabel: { en: "Accessible repositories", ar: "المستودعات المتاحة" },
  listNote: {
    en: "{count} repositories loaded. Choose one to continue.",
    ar: "تم تحميل {count} مستودعًا. اختر واحدًا للمتابعة.",
  },
  differentToken: { en: "Use a different token", ar: "استخدام رمز آخر" },
} as const;

export const t04a03 = makeT(STRINGS_04A03);
