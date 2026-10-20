/** Inline guidance for the structured mandate fields (FTF-UX03, audit #15, NN/g H10). */
import type { Locale } from "../ui";

const LIST_NOTE = {
  en: "Separate items with commas.",
  ar: "افصل بين العناصر بفواصل.",
} as const;

type Help = Record<Locale, string>;

export const MANDATE_FIELD_HELP: Record<string, Help> = {
  business_purpose: {
    en: "One or two sentences on what the application is for and who it serves, in the business's own words.",
    ar: "جملة أو جملتان تشرحان الغرض من التطبيق ومن يخدمه، بلغة العمل نفسه.",
  },
  tool_side_effect_confirmation_policy: {
    en: "When the application must ask for confirmation before a tool changes something outside itself, such as sending a message or issuing a refund.",
    ar: "متى يجب على التطبيق طلب التأكيد قبل أن تغيّر أداة شيئًا خارجه، مثل إرسال رسالة أو إصدار استرداد.",
  },
  expected_outputs_and_schemas: {
    en: "The shape of what the application returns: formats, required fields and any JSON schema a consumer depends on.",
    ar: "شكل ما يعيده التطبيق: الصيغ والحقول المطلوبة وأي مخطط JSON يعتمد عليه المستهلك.",
  },
  escalation_abstention_behavior: {
    en: "What the application does when it should not answer: hand over to a person, decline, or ask for more information.",
    ar: "ما يفعله التطبيق عندما لا ينبغي له الإجابة: التحويل إلى شخص أو الرفض أو طلب مزيد من المعلومات.",
  },
  privacy_security_profile: {
    en: "The data-handling rules that apply: personal or regulated data, retention limits and what must never leave the system.",
    ar: "قواعد التعامل مع البيانات المطبقة: البيانات الشخصية أو الخاضعة للتنظيم وحدود الاحتفاظ وما لا يجوز أن يخرج من النظام.",
  },
  allowed_responsibilities: {
    en: `The tasks the application is permitted to perform. Anything not listed is out of scope. ${LIST_NOTE.en}`,
    ar: `المهام المسموح للتطبيق بتنفيذها، وما لم يُذكر فهو خارج النطاق. ${LIST_NOTE.ar}`,
  },
  critical_behaviors: {
    en: `Behaviours that must never fail, each one checked by a test before release. ${LIST_NOTE.en}`,
    ar: `السلوكيات التي يجب ألا تفشل أبدًا، ويُختبر كل منها قبل الإصدار. ${LIST_NOTE.ar}`,
  },
  required_tools: {
    en: `The tools or APIs the application must be able to call to do its job. ${LIST_NOTE.en}`,
    ar: `الأدوات أو واجهات البرمجة التي يجب أن يستطيع التطبيق استدعاءها لأداء مهمته. ${LIST_NOTE.ar}`,
  },
  required_context_data_classes: {
    en: `The classes of data the application needs as context, for example customer profile or order history. ${LIST_NOTE.en}`,
    ar: `فئات البيانات التي يحتاجها التطبيق كسياق، مثل ملف العميل أو سجل الطلبات. ${LIST_NOTE.ar}`,
  },
  supported_languages: {
    en: `Languages the application must answer in, as language names or codes such as en, ar. ${LIST_NOTE.en}`,
    ar: `اللغات التي يجب أن يجيب بها التطبيق، كأسماء أو رموز مثل en وar. ${LIST_NOTE.ar}`,
  },
  critical_safety_reliability_gates: {
    en: `Safety and reliability checks that block release if they fail. ${LIST_NOTE.en}`,
    ar: `فحوصات السلامة والموثوقية التي تمنع الإصدار عند فشلها. ${LIST_NOTE.ar}`,
  },
  latency_slo_ms: {
    en: "The response-time target in milliseconds that most requests must meet. Leave empty if there is none.",
    ar: "هدف زمن الاستجابة بالمللي ثانية الذي يجب أن تحققه معظم الطلبات. اتركه فارغًا إن لم يوجد.",
  },
  throughput_concurrency_target: {
    en: "How many requests the application must handle at the same time. Leave empty if there is no target.",
    ar: "عدد الطلبات التي يجب أن يعالجها التطبيق في الوقت نفسه. اتركه فارغًا إن لم يوجد هدف.",
  },
  cost_per_success_ceiling: {
    en: "The most you are willing to spend for one successful outcome, in your billing currency. Leave empty if there is no ceiling.",
    ar: "أقصى مبلغ مستعد لإنفاقه على نتيجة ناجحة واحدة، بعملة الفوترة. اتركه فارغًا إن لم يوجد سقف.",
  },
};
