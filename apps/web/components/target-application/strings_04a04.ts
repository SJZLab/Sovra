import { makeT } from "./common";

/** FTF-04A04 system strings. Branch names, refs and SHAs are user data and are rendered in <bdi>. */
export const STRINGS_04A04 = {
  loadBranches: { en: "Load branches", ar: "تحميل الفروع" },
  loadingBranches: { en: "Loading branches…", ar: "جارٍ تحميل الفروع…" },
  loadMore: { en: "Load more", ar: "تحميل المزيد" },
  branchListLabel: { en: "Branches", ar: "الفروع" },
  noBranchesTitle: { en: "No branches found", ar: "لم يتم العثور على فروع" },
  noBranchesWhy: {
    en: "This token can see no branches in the repository. You can still resolve a tag or a commit SHA below.",
    ar: "لا يرى هذا الرمز أي فروع في المستودع. لا يزال بإمكانك تحليل وسم أو معرّف إيداع أدناه.",
  },
  enterRefManually: { en: "Enter a ref manually", ar: "إدخال مرجع يدويًا" },
  refLabel: { en: "Branch, tag, or commit SHA", ar: "فرع أو وسم أو معرّف إيداع" },
  refHelper: {
    en: "Choose a branch above or type one. Resolving pins it to an exact commit.",
    ar: "اختر فرعًا أعلاه أو اكتب واحدًا. يؤدي التحليل إلى تثبيته على إيداع محدد.",
  },
  refRequired: {
    en: "Enter a branch, tag, or commit SHA first.",
    ar: "أدخل فرعًا أو وسمًا أو معرّف إيداع أولًا.",
  },
  resolve: { en: "Resolve", ar: "تحليل" },
  resolving: { en: "Resolving…", ar: "جارٍ التحليل…" },
  resolvedPrefix: { en: "Resolved", ar: "تم تحليل" },
  resolvedTo: { en: "to", ar: "إلى" },
  selectedMark: { en: "Selected", ar: "محدد" },
} as const;

export const t04a04 = makeT(STRINGS_04A04);
