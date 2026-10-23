import { makeT } from "./common";

/** FTF-04A18 system strings. Candidate names and runtimes are user data and are rendered in <bdi>. */
export const STRINGS_04A18 = {
  loadCandidates: { en: "Load detected agent candidates", ar: "تحميل الوكلاء المرشحين المكتشفين" },
  loadingCandidates: { en: "Loading detected agents…", ar: "جارٍ تحميل الوكلاء المكتشفين…" },
  reloadCandidates: { en: "Reload candidates", ar: "إعادة تحميل المرشحين" },
  listLabel: { en: "Detected agent candidates", ar: "الوكلاء المرشحون المكتشفون" },
  noCandidatesTitle: { en: "No agent candidates detected", ar: "لم يتم اكتشاف أي وكيل مرشح" },
  noCandidatesWhy: {
    en: "Source analysis found no agent in this snapshot. Check the repository and branch, or analyse a newer snapshot, then reload.",
    ar: "لم يعثر تحليل المصدر على أي وكيل في هذه اللقطة. تحقق من المستودع والفرع أو حلّل لقطة أحدث ثم أعد التحميل.",
  },
  selectCandidate: { en: "Select this candidate", ar: "اختيار هذا المرشح" },
  selectingCandidate: { en: "Recording your selection…", ar: "جارٍ تسجيل اختيارك…" },
  runtime: { en: "Runtime", ar: "بيئة التشغيل" },
  selectedMark: { en: "Selected", ar: "محدد" },
  selectedAt: { en: "at", ar: "في" },
  selectedPrefix: { en: "Selected", ar: "تم اختيار" },
  unknownCandidate: { en: "the chosen candidate", ar: "المرشح المختار" },
} as const;

export const t04a18 = makeT(STRINGS_04A18);
