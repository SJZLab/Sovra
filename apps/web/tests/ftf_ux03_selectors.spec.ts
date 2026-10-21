// @vitest-environment jsdom
//
// FTF-UX03 round 2 (QUEUE 0g): behavioural pins for the three GitHub/candidate selectors rebuilt on
// the UX01 components -- FTF-04A03 (repository selector), FTF-04A04 (branch/ref resolver) and
// FTF-04A18 (candidate selection). Every test renders the real component with a mocked `fetch` and
// asserts what a user sees or what the component sends; none greps source text. Exact Arabic strings
// are literals on purpose: a catalogue entry swapped for English must turn a test RED.
import { createElement, type ReactElement } from "react";

import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import {
  CandidateSelectionUI,
  bindListing as bindCandidateListing,
  bindSelection,
  formatSelectedAt,
} from "../components/target-application/candidate_selection_ui_no_auto_selection";
import {
  GitHubBranchRefSelector,
  bindBranchListing,
  bindResolvedRef,
} from "../components/target-application/github_branch_ref_selector_immutable_sha_resolver";
import {
  GitHubAccessibleRepositorySelector,
  bindListing as bindRepositoryListing,
} from "../components/target-application/github_accessible_repository_selector_ui";
import { failureMessage, type Failure } from "../components/target-application/common";

const API = "http://localhost:8000";
const ORG = "11111111-1111-1111-1111-111111111111";
const ORG_B = "99999999-9999-9999-9999-999999999999";
const PROJECT = "22222222-2222-2222-2222-222222222222";
const CONNECTION = "33333333-3333-3333-3333-333333333333";
const SNAPSHOT = "44444444-4444-4444-4444-444444444444";
const CAND_A = "55555555-5555-5555-5555-555555555555";
const CAND_B = "66666666-6666-6666-6666-666666666666";
const SHA = "c".repeat(40);

type Fetch = ReturnType<typeof vi.fn>;
const fetchMock = () => fetch as unknown as Fetch;

beforeEach(() => {
  vi.stubGlobal("fetch", vi.fn());
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

function ok(body: unknown) {
  return { ok: true, status: 200, json: async () => body };
}
function refused(status: number, body: unknown = { detail: "stale_candidate" }) {
  return { ok: false, status, json: async () => body };
}
/** A request that stays in flight until `release` is called, to observe the loading state. */
function inFlight() {
  let release: (value: unknown) => void = () => undefined;
  const promise = new Promise((resolve) => {
    release = resolve;
  });
  return { promise, release };
}

// ---------------------------------------------------------------------------------------------
// Renderers
// ---------------------------------------------------------------------------------------------
function repoSelector(locale?: string, org = ORG): ReactElement {
  return createElement(GitHubAccessibleRepositorySelector, {
    apiBaseUrl: API,
    organizationId: org,
    projectId: PROJECT,
    ...(locale ? { locale: locale as "en" | "ar" } : {}),
  });
}
function branchSelector(locale?: string, repository = "acme/app"): ReactElement {
  return createElement(GitHubBranchRefSelector, {
    apiBaseUrl: API,
    organizationId: ORG,
    projectId: PROJECT,
    credential: "ghp_token",
    repositoryIdentity: repository,
    targetApplicationConnectionId: CONNECTION,
    ...(locale ? { locale: locale as "en" | "ar" } : {}),
  });
}
function candidateUI(locale?: string, snapshot = SNAPSHOT): ReactElement {
  return createElement(CandidateSelectionUI, {
    apiBaseUrl: API,
    organizationId: ORG,
    projectId: PROJECT,
    connectionId: CONNECTION,
    snapshotId: snapshot,
    ...(locale ? { locale: locale as "en" | "ar" } : {}),
  });
}

const REPO_PAGE = (over: Record<string, unknown> = {}) => ({
  repositories: [{ repository_identity: "acme/one", private: true, default_branch: "main" }],
  page: 1,
  per_page: 30,
  has_next_page: false,
  ...over,
});
const BRANCH_PAGE = (over: Record<string, unknown> = {}) => ({
  branches: [{ name: "main", list_time_commit_sha: "a".repeat(40) }],
  page: 1,
  per_page: 30,
  has_next_page: false,
  ...over,
});
const CANDIDATE = (over: Record<string, unknown> = {}) => ({
  id: CAND_A,
  display_name: "TriageAgent",
  framework_or_runtime: "python",
  confidence_score: 0.5,
  discovery_status: "candidate",
  ...over,
});
const CANDIDATE_PAGE = (over: Record<string, unknown> = {}) => ({
  items: [
    CANDIDATE(),
    CANDIDATE({ id: CAND_B, display_name: "SupportAgent", confidence_score: 0.9 }),
  ],
  limit: 50,
  offset: 0,
  total: 2,
  ...over,
});
const SELECTION = (over: Record<string, unknown> = {}) => ({
  candidate_id: CAND_B,
  target_source_snapshot_id: SNAPSHOT,
  selected_by: "user-1",
  selected_at: "2026-10-02T12:00:00+00:00",
  ...over,
});

async function loadRepositories(locale?: string, body: unknown = REPO_PAGE()) {
  fetchMock().mockResolvedValueOnce(ok(body));
  render(repoSelector(locale));
  fireEvent.change(screen.getByTestId("github-credential-input"), { target: { value: "tok" } });
  fireEvent.click(screen.getByTestId("github-load-repositories-button"));
  await waitFor(() => expect(fetch).toHaveBeenCalled());
}

// ---------------------------------------------------------------------------------------------
// Point 1: every control is a UX01 control (no native unstyled <button>, 24 px target via Button)
// ---------------------------------------------------------------------------------------------
describe("[UX03-R2-P1] built on the UX01 components", () => {
  test("04A03: every button in every state is a UX01 Button (carries data-variant)", async () => {
    await loadRepositories(undefined, REPO_PAGE({ has_next_page: true }));
    await screen.findByTestId("github-repository-option-acme/one");
    fireEvent.click(screen.getByTestId("github-repository-option-acme/one"));
    const buttons = Array.from(document.querySelectorAll("button"));
    expect(buttons.length).toBeGreaterThanOrEqual(3);
    for (const button of buttons) expect(button.getAttribute("data-variant")).toBeTruthy();
  });

  test("04A04: every button in every state is a UX01 Button", async () => {
    fetchMock().mockResolvedValueOnce(ok(BRANCH_PAGE({ has_next_page: true })));
    render(branchSelector());
    fireEvent.click(screen.getByTestId("github-load-branches-button"));
    await screen.findByTestId("github-branch-option-main");
    const buttons = Array.from(document.querySelectorAll("button"));
    expect(buttons.length).toBeGreaterThanOrEqual(4);
    for (const button of buttons) expect(button.getAttribute("data-variant")).toBeTruthy();
  });

  test("04A18: every button in every state is a UX01 Button", async () => {
    fetchMock().mockResolvedValueOnce(ok(CANDIDATE_PAGE()));
    render(candidateUI());
    fireEvent.click(screen.getByTestId("candidate-load-button"));
    await screen.findByTestId(`candidate-row-${CAND_A}`);
    const buttons = Array.from(document.querySelectorAll("button"));
    expect(buttons.length).toBe(3);
    for (const button of buttons) expect(button.getAttribute("data-variant")).toBeTruthy();
  });

  test("04A03: the token input is a UX01 Field -- visible label above, required marker, helper wired", () => {
    render(repoSelector());
    const input = screen.getByTestId("github-credential-input") as HTMLInputElement;
    const label = document.querySelector(`label[for="${input.id}"]`);
    expect(label?.textContent).toContain("GitHub token");
    expect(label?.textContent).toContain("(required)");
    expect(input.getAttribute("aria-required")).toBe("true");
    const helper = document.getElementById(input.getAttribute("aria-describedby")!.split(" ")[0]!);
    expect(helper?.textContent).toContain("read access to repository metadata");
  });

  test("04A04: the ref input is a UX01 Field and a blank ref disables Resolve WITH the reason shown", () => {
    render(branchSelector());
    const input = screen.getByTestId("github-ref-input") as HTMLInputElement;
    expect(document.querySelector(`label[for="${input.id}"]`)?.textContent).toContain("(required)");
    const resolve = screen.getByTestId("github-resolve-ref-button") as HTMLButtonElement;
    expect(resolve.disabled).toBe(true);
    expect(screen.getByTestId("disabled-reason").textContent).toBe(
      "Enter a branch, tag, or commit SHA first.",
    );
    fireEvent.change(input, { target: { value: "main" } });
    expect(resolve.disabled).toBe(false);
    expect(screen.queryByTestId("disabled-reason")).toBeNull();
  });

  test("04A03: submitting a blank token shows an ErrorSummary linked to the field, sends nothing, and the field is described by its error", async () => {
    render(repoSelector());
    fireEvent.click(screen.getByTestId("github-load-repositories-button"));
    expect(fetch).not.toHaveBeenCalled();
    const summary = await screen.findByRole("link", {
      name: "Enter a GitHub token to load repositories.",
    });
    expect(summary.getAttribute("href")).toBe("#github-credential");
    const input = screen.getByTestId("github-credential-input");
    expect(input.getAttribute("aria-invalid")).toBe("true");
    const described = input.getAttribute("aria-describedby")!.split(" ");
    const texts = described.map((id) => document.getElementById(id)?.textContent ?? "");
    expect(texts.some((text) => text === "Enter a GitHub token to load repositories.")).toBe(true);
  });
});

// ---------------------------------------------------------------------------------------------
// Point 2: bilingual, lang + dir on every state, user data isolated
// ---------------------------------------------------------------------------------------------
describe("[UX03-R2-P2] bilingual with lang and dir on every state", () => {
  function rootOf(testId: string) {
    return screen.getByTestId(testId);
  }

  test("04A03: English is ltr/en and Arabic is rtl/ar before and after a search and on an error", async () => {
    const en = render(repoSelector("en"));
    expect(rootOf("github-repository-selector").getAttribute("dir")).toBe("ltr");
    expect(rootOf("github-repository-selector").getAttribute("lang")).toBe("en");
    en.unmount();
    await loadRepositories("ar");
    await screen.findByTestId("github-repository-option-acme/one");
    expect(rootOf("github-repository-selector").getAttribute("dir")).toBe("rtl");
    expect(rootOf("github-repository-selector").getAttribute("lang")).toBe("ar");
  });

  test("04A03: an unknown locale renders English text and says lang=en (not the unknown value)", () => {
    render(repoSelector("fr"));
    expect(rootOf("github-repository-selector").getAttribute("lang")).toBe("en");
    expect(screen.getByTestId("github-load-repositories-button").textContent).toBe(
      "Load repositories",
    );
  });

  test("04A03: every system string is Arabic -- label, helper, button, load more, private, selected, list note", async () => {
    fetchMock().mockResolvedValueOnce(ok(REPO_PAGE({ has_next_page: true })));
    render(repoSelector("ar"));
    expect(document.querySelector("label")?.textContent).toContain("رمز GitHub");
    expect(document.querySelector("label")?.textContent).toContain("(مطلوب)");
    expect(screen.getByTestId("github-load-repositories-button").textContent).toBe(
      "تحميل المستودعات",
    );
    fireEvent.change(screen.getByTestId("github-credential-input"), { target: { value: "tok" } });
    fireEvent.click(screen.getByTestId("github-load-repositories-button"));
    const option = await screen.findByTestId("github-repository-option-acme/one");
    expect(option.textContent).toContain("(خاص)");
    expect(screen.getByTestId("github-load-more-button").textContent).toBe("تحميل المزيد");
    // F5: the list note is not emptied or English.
    expect(screen.getByTestId("github-repository-list-note").textContent).toBe(
      "تم تحميل 1 مستودعًا. اختر واحدًا للمتابعة.",
    );
    fireEvent.click(option);
    expect(screen.getByTestId("github-repository-selected-notice").textContent).toContain(
      "المحدد:",
    );
  });

  test("04A03: repository names are isolated in <bdi> so an RTL page cannot reorder them", async () => {
    await loadRepositories("ar");
    const option = await screen.findByTestId("github-repository-option-acme/one");
    expect(option.querySelector("bdi")?.textContent).toBe("acme/one");
  });

  test("04A03 (F1/F2): the Arabic empty state has Arabic title, reason and action -- none English", async () => {
    await loadRepositories("ar", REPO_PAGE({ repositories: [] }));
    const section = await screen.findByRole("region", { name: "لم يتم العثور على مستودعات متاحة" });
    expect(section.textContent).toContain(
      "لا يمنح GitHub هذا الرمز صلاحية الوصول إلى أي مستودع. تحقق من نطاق الرمز وصلاحيات التثبيت ثم أعد التحميل.",
    );
    expect(section.querySelector("button")?.textContent).toBe("استخدام رمز آخر");
  });

  test("04A04: Arabic strings, rtl/ar, bdi around branch names and the resolved ref/sha", async () => {
    fetchMock().mockResolvedValueOnce(ok(BRANCH_PAGE({ has_next_page: true })));
    render(branchSelector("ar"));
    const root = screen.getByTestId("github-branch-ref-selector");
    expect(root.getAttribute("dir")).toBe("rtl");
    expect(root.getAttribute("lang")).toBe("ar");
    expect(screen.getByTestId("github-load-branches-button").textContent).toBe("تحميل الفروع");
    expect(document.querySelector("label")?.textContent).toContain("فرع أو وسم أو معرّف إيداع");
    fireEvent.click(screen.getByTestId("github-load-branches-button"));
    const option = await screen.findByTestId("github-branch-option-main");
    expect(option.querySelector("bdi")?.textContent).toBe("main");
    expect(screen.getByTestId("github-branch-load-more-button").textContent).toBe("تحميل المزيد");
    fetchMock().mockResolvedValueOnce(ok({ ref: "main", resolved_commit_sha: SHA }));
    fireEvent.click(option);
    fireEvent.click(screen.getByTestId("github-resolve-ref-button"));
    const notice = await screen.findByTestId("github-resolved-ref-notice");
    expect(notice.textContent).toContain("تم تحليل");
    expect(notice.querySelectorAll("bdi").length).toBe(2);
  });

  test("04A04 (F1/F2): the Arabic no-branches empty state is Arabic with a real action", async () => {
    fetchMock().mockResolvedValueOnce(ok(BRANCH_PAGE({ branches: [] })));
    render(branchSelector("ar"));
    fireEvent.click(screen.getByTestId("github-load-branches-button"));
    const section = await screen.findByRole("region", { name: "لم يتم العثور على فروع" });
    expect(section.textContent).toContain(
      "لا يرى هذا الرمز أي فروع في المستودع. لا يزال بإمكانك تحليل وسم أو معرّف إيداع أدناه.",
    );
    expect(section.querySelector("button")?.textContent).toBe("إدخال مرجع يدويًا");
  });

  test("04A18 (F15): the locale decides direction -- ar is rtl, en is ltr (never forced)", async () => {
    render(candidateUI("ar"));
    expect(screen.getByTestId("candidate-selection-ui").getAttribute("dir")).toBe("rtl");
    expect(screen.getByTestId("candidate-selection-ui").getAttribute("lang")).toBe("ar");
    cleanup();
    render(candidateUI("en"));
    expect(screen.getByTestId("candidate-selection-ui").getAttribute("dir")).toBe("ltr");
    expect(screen.getByTestId("candidate-selection-ui").getAttribute("lang")).toBe("en");
  });

  test("04A18: Arabic strings for the load button, select button, runtime, selected mark and confirmation, names in <bdi>", async () => {
    fetchMock().mockResolvedValueOnce(ok(CANDIDATE_PAGE()));
    render(candidateUI("ar"));
    expect(screen.getByTestId("candidate-load-button").textContent).toBe(
      "تحميل الوكلاء المرشحين المكتشفين",
    );
    fireEvent.click(screen.getByTestId("candidate-load-button"));
    const row = await screen.findByTestId(`candidate-row-${CAND_B}`);
    expect(row.querySelector("bdi")?.textContent).toBe("SupportAgent");
    expect(row.textContent).toContain("بيئة التشغيل");
    expect(screen.getByTestId(`candidate-select-button-${CAND_B}`).textContent).toBe(
      "اختيار هذا المرشح",
    );
    fetchMock().mockResolvedValueOnce(ok(SELECTION()));
    fireEvent.click(screen.getByTestId(`candidate-select-button-${CAND_B}`));
    const confirmation = await screen.findByTestId("candidate-selection-confirmation");
    expect(confirmation.textContent).toContain("تم اختيار");
    expect(confirmation.querySelector("bdi")?.textContent).toBe("SupportAgent");
    expect(row.textContent).toContain("محدد");
  });

  test("04A18 (F1/F2): the Arabic empty state is Arabic with a real reload action", async () => {
    fetchMock().mockResolvedValueOnce(ok(CANDIDATE_PAGE({ items: [], total: 0 })));
    render(candidateUI("ar"));
    fireEvent.click(screen.getByTestId("candidate-load-button"));
    const section = await screen.findByRole("region", { name: "لم يتم اكتشاف أي وكيل مرشح" });
    expect(section.textContent).toContain(
      "لم يعثر تحليل المصدر على أي وكيل في هذه اللقطة. تحقق من المستودع والفرع أو حلّل لقطة أحدث ثم أعد التحميل.",
    );
    expect(section.querySelector("button")?.textContent).toBe("إعادة تحميل المرشحين");
  });

  test("failure sentences are localized: every failure kind has Arabic and English text with a next step", () => {
    const failures: Failure[] = [
      { kind: "network" },
      { kind: "malformed" },
      { kind: "http", status: 401, detail: null },
      { kind: "http", status: 403, detail: null },
      { kind: "http", status: 503, detail: null },
      { kind: "http", status: 409, detail: "stale_candidate" },
      { kind: "http", status: 418, detail: null },
    ];
    for (const failure of failures) {
      const en = failureMessage(failure, "en");
      const ar = failureMessage(failure, "ar");
      expect(en).not.toBe(ar);
      expect(/[؀-ۿ]/.test(ar)).toBe(true);
      expect(/[؀-ۿ]/.test(en)).toBe(false);
      expect(en.length).toBeGreaterThan(30);
    }
    expect(
      failureMessage({ kind: "http", status: 409, detail: "stale_candidate" }, "ar"),
    ).toContain("stale_candidate");
  });
});

// ---------------------------------------------------------------------------------------------
// Point 3: errors are caught, announced, recoverable; nothing unmounts or is fabricated (Law 17)
// ---------------------------------------------------------------------------------------------
describe("[UX03-R2-P3] failures are announced and never blank or fabricate a state", () => {
  test("04A03: a rejected fetch shows an announced, recoverable error and the form stays", async () => {
    fetchMock().mockRejectedValueOnce(new TypeError("Failed to fetch"));
    render(repoSelector());
    fireEvent.change(screen.getByTestId("github-credential-input"), { target: { value: "tok" } });
    fireEvent.click(screen.getByTestId("github-load-repositories-button"));
    const error = await screen.findByTestId("github-repository-error");
    expect(error.querySelector('[role="alert"]')).toBeTruthy();
    expect(error.textContent).toContain(
      "Could not reach the server. Check your connection, then try again.",
    );
    expect(screen.getByTestId("github-credential-form")).toBeTruthy();
    expect(
      (screen.getByTestId("github-load-repositories-button") as HTMLButtonElement).disabled,
    ).toBe(false);
    expect(screen.queryByTestId("github-repository-selected-notice")).toBeNull();
  });

  test("04A03: a malformed 200 is announced and does NOT unmount the component or touch the loaded list", async () => {
    await loadRepositories();
    await screen.findByTestId("github-repository-option-acme/one");
    fetchMock().mockResolvedValueOnce(ok({ nope: true }));
    fireEvent.click(screen.getByTestId("github-load-repositories-button"));
    const error = await screen.findByTestId("github-repository-error");
    expect(error.textContent).toContain("cannot read");
    expect(screen.getByTestId("github-repository-selector")).toBeTruthy();
  });

  test("04A03: a non-JSON 5xx and a non-JSON 200 are retryable errors, never success", async () => {
    fetchMock().mockResolvedValueOnce({
      ok: false,
      status: 502,
      json: async () => {
        throw new SyntaxError("<html>");
      },
    });
    render(repoSelector());
    fireEvent.change(screen.getByTestId("github-credential-input"), { target: { value: "tok" } });
    fireEvent.click(screen.getByTestId("github-load-repositories-button"));
    expect((await screen.findByTestId("github-repository-error")).textContent).toContain(
      "The service had a problem (502)",
    );
    fetchMock().mockResolvedValueOnce({
      ok: true,
      status: 200,
      json: async () => {
        throw new SyntaxError("Unexpected end");
      },
    });
    fireEvent.click(screen.getByTestId("github-load-repositories-button"));
    await waitFor(() =>
      expect(screen.getByTestId("github-repository-error").textContent).toContain("cannot read"),
    );
    expect(screen.queryByTestId("github-repository-list-note")).toBeNull();
  });

  test("04A04: a rejected branch fetch and a malformed resolve reply are announced; no SHA is ever shown", async () => {
    fetchMock().mockRejectedValueOnce(new Error("boom"));
    render(branchSelector());
    fireEvent.click(screen.getByTestId("github-load-branches-button"));
    const error = await screen.findByTestId("github-branch-ref-error");
    expect(error.querySelector('[role="alert"]')).toBeTruthy();
    expect(error.textContent).toContain("Could not reach the server");
    fireEvent.change(screen.getByTestId("github-ref-input"), { target: { value: "main" } });
    fetchMock().mockResolvedValueOnce(ok({ ref: "main", resolved_commit_sha: "not-a-sha" }));
    fireEvent.click(screen.getByTestId("github-resolve-ref-button"));
    await waitFor(() =>
      expect(screen.getByTestId("github-branch-ref-error").textContent).toContain("cannot read"),
    );
    expect(screen.queryByTestId("github-resolved-ref-notice")).toBeNull();
  });

  test("04A04: a rejected resolve fetch is announced and the input keeps the typed ref", async () => {
    render(branchSelector());
    fireEvent.change(screen.getByTestId("github-ref-input"), { target: { value: "v1.2" } });
    fetchMock().mockRejectedValueOnce(new Error("down"));
    fireEvent.click(screen.getByTestId("github-resolve-ref-button"));
    await screen.findByTestId("github-branch-ref-error");
    expect((screen.getByTestId("github-ref-input") as HTMLInputElement).value).toBe("v1.2");
    expect(screen.queryByTestId("github-resolved-ref-notice")).toBeNull();
  });

  test("04A18: a 5xx on the list keeps the previously loaded candidates and offers the same button again", async () => {
    fetchMock().mockResolvedValueOnce(ok(CANDIDATE_PAGE()));
    render(candidateUI());
    fireEvent.click(screen.getByTestId("candidate-load-button"));
    await screen.findByTestId(`candidate-row-${CAND_A}`);
    fetchMock().mockResolvedValueOnce(refused(503, "<html>"));
    fireEvent.click(screen.getByTestId("candidate-load-button"));
    const error = await screen.findByTestId("candidate-selection-error");
    expect(error.textContent).toContain("The service had a problem (503)");
    expect(screen.getByTestId(`candidate-row-${CAND_A}`)).toBeTruthy();
    expect((screen.getByTestId("candidate-load-button") as HTMLButtonElement).disabled).toBe(false);
  });

  test("04A18: a conflict names its reason and a next step; the select buttons stay usable", async () => {
    fetchMock().mockResolvedValueOnce(ok(CANDIDATE_PAGE()));
    render(candidateUI());
    fireEvent.click(screen.getByTestId("candidate-load-button"));
    await screen.findByTestId(`candidate-row-${CAND_A}`);
    fetchMock().mockResolvedValueOnce(refused(409, { detail: "stale_candidate" }));
    fireEvent.click(screen.getByTestId(`candidate-select-button-${CAND_A}`));
    const error = await screen.findByTestId("candidate-selection-error");
    expect(error.textContent).toContain("stale_candidate");
    expect(error.textContent).toContain("try again");
    expect(screen.queryByTestId("candidate-selection-confirmation")).toBeNull();
    expect(
      (screen.getByTestId(`candidate-select-button-${CAND_A}`) as HTMLButtonElement).disabled,
    ).toBe(false);
  });
});

// ---------------------------------------------------------------------------------------------
// Point 4: loading and empty states
// ---------------------------------------------------------------------------------------------
describe("[UX03-R2-P4] loading and empty states", () => {
  test("04A03 (F13): while a search is in flight the region is aria-busy and no empty state shows -- even after an earlier empty result", async () => {
    await loadRepositories(undefined, REPO_PAGE({ repositories: [] }));
    await screen.findByRole("region", { name: "No accessible repositories found" });
    const pending = inFlight();
    fetchMock().mockReturnValueOnce(pending.promise);
    fireEvent.click(screen.getByTestId("github-load-repositories-button"));
    await waitFor(() => expect(document.querySelector('[aria-busy="true"]')).toBeTruthy());
    expect(screen.queryByRole("region", { name: "No accessible repositories found" })).toBeNull();
    expect(
      (screen.getByTestId("github-load-repositories-button") as HTMLButtonElement).disabled,
    ).toBe(true);
    await act(async () => {
      pending.release(ok(REPO_PAGE()));
    });
  });

  test("04A03 (F12): the empty state's action is a real button that clears the token and focuses the field", async () => {
    await loadRepositories(undefined, REPO_PAGE({ repositories: [] }));
    const section = await screen.findByRole("region", { name: "No accessible repositories found" });
    const action = section.querySelector("button")!;
    expect(action.textContent).toBe("Use a different token");
    fireEvent.click(action);
    const input = screen.getByTestId("github-credential-input") as HTMLInputElement;
    expect(input.value).toBe("");
    expect(document.activeElement).toBe(input);
    expect(screen.queryByRole("region", { name: "No accessible repositories found" })).toBeNull();
  });

  test("04A04: zero branches shows the empty state with a real action that focuses the ref field; no empty state before loading or while loading", async () => {
    render(branchSelector());
    expect(screen.queryByRole("region", { name: "No branches found" })).toBeNull();
    fetchMock().mockResolvedValueOnce(ok(BRANCH_PAGE({ branches: [] })));
    fireEvent.click(screen.getByTestId("github-load-branches-button"));
    const section = await screen.findByRole("region", { name: "No branches found" });
    const action = section.querySelector("button")!;
    expect(action.textContent).toBe("Enter a ref manually");
    fireEvent.click(action);
    expect(document.activeElement).toBe(screen.getByTestId("github-ref-input"));
    const pending = inFlight();
    fetchMock().mockReturnValueOnce(pending.promise);
    fireEvent.click(screen.getByTestId("github-load-branches-button"));
    await waitFor(() => expect(document.querySelector('[aria-busy="true"]')).toBeTruthy());
    expect(screen.queryByRole("region", { name: "No branches found" })).toBeNull();
    await act(async () => {
      pending.release(ok(BRANCH_PAGE()));
    });
  });

  test("04A04: a load error is not reported as 'no branches'", async () => {
    fetchMock().mockResolvedValueOnce(refused(500, {}));
    render(branchSelector());
    fireEvent.click(screen.getByTestId("github-load-branches-button"));
    await screen.findByTestId("github-branch-ref-error");
    expect(screen.queryByRole("region", { name: "No branches found" })).toBeNull();
  });

  test("04A18: zero candidates shows the empty state with a working Reload action; none before loading, while loading or on error", async () => {
    render(candidateUI());
    expect(screen.queryByRole("region", { name: "No agent candidates detected" })).toBeNull();
    fetchMock().mockResolvedValueOnce(ok(CANDIDATE_PAGE({ items: [], total: 0 })));
    fireEvent.click(screen.getByTestId("candidate-load-button"));
    const section = await screen.findByRole("region", { name: "No agent candidates detected" });
    const reload = section.querySelector("button")!;
    expect(reload.textContent).toBe("Reload candidates");
    const pending = inFlight();
    fetchMock().mockReturnValueOnce(pending.promise);
    fireEvent.click(reload);
    await waitFor(() => expect(document.querySelector('[aria-busy="true"]')).toBeTruthy());
    expect(screen.queryByRole("region", { name: "No agent candidates detected" })).toBeNull();
    await act(async () => {
      pending.release(ok(CANDIDATE_PAGE()));
    });
    await screen.findByTestId(`candidate-row-${CAND_A}`);
    fetchMock().mockResolvedValueOnce(refused(500, {}));
    fireEvent.click(screen.getByTestId("candidate-load-button"));
    await screen.findByTestId("candidate-selection-error");
    expect(screen.queryByRole("region", { name: "No agent candidates detected" })).toBeNull();
  });

  test("04A18: selecting shows a busy state and disables every select button until the reply", async () => {
    fetchMock().mockResolvedValueOnce(ok(CANDIDATE_PAGE()));
    render(candidateUI());
    fireEvent.click(screen.getByTestId("candidate-load-button"));
    await screen.findByTestId(`candidate-row-${CAND_A}`);
    const pending = inFlight();
    fetchMock().mockReturnValueOnce(pending.promise);
    fireEvent.click(screen.getByTestId(`candidate-select-button-${CAND_A}`));
    await waitFor(() => expect(document.querySelector('[aria-busy="true"]')).toBeTruthy());
    expect(
      (screen.getByTestId(`candidate-select-button-${CAND_B}`) as HTMLButtonElement).disabled,
    ).toBe(true);
    await act(async () => {
      pending.release(ok(SELECTION({ candidate_id: CAND_A })));
    });
    await screen.findByTestId("candidate-selection-confirmation");
  });
});

// ---------------------------------------------------------------------------------------------
// Point 6 (04A18) severity-2 decisions
// ---------------------------------------------------------------------------------------------
describe("[UX03-R2-P6] 04A18 accessible names and confirmation", () => {
  test("each row's select button has a distinct accessible name that contains its visible text and the candidate's name", async () => {
    fetchMock().mockResolvedValueOnce(ok(CANDIDATE_PAGE()));
    render(candidateUI());
    fireEvent.click(screen.getByTestId("candidate-load-button"));
    await screen.findByTestId(`candidate-row-${CAND_A}`);
    const a = screen.getByTestId(`candidate-select-button-${CAND_A}`);
    const b = screen.getByTestId(`candidate-select-button-${CAND_B}`);
    expect(a.getAttribute("aria-label")).toBe("Select this candidate: TriageAgent");
    expect(b.getAttribute("aria-label")).toBe("Select this candidate: SupportAgent");
    expect(a.getAttribute("aria-label")).toContain(a.textContent!);
    expect(screen.getByRole("button", { name: "Select this candidate: SupportAgent" })).toBe(b);
  });

  test("the confirmation shows the candidate's name and a localized time -- never the UUID or the ISO string", async () => {
    fetchMock().mockResolvedValueOnce(ok(CANDIDATE_PAGE()));
    render(candidateUI());
    fireEvent.click(screen.getByTestId("candidate-load-button"));
    await screen.findByTestId(`candidate-row-${CAND_B}`);
    fetchMock().mockResolvedValueOnce(ok(SELECTION()));
    fireEvent.click(screen.getByTestId(`candidate-select-button-${CAND_B}`));
    const text = (await screen.findByTestId("candidate-selection-confirmation")).textContent!;
    expect(text).toContain("SupportAgent");
    expect(text).not.toContain(CAND_B);
    expect(text).not.toContain("2026-10-02T12:00:00");
    expect(text).toContain(formatSelectedAt("2026-10-02T12:00:00+00:00", "en"));
  });

  test("the time is formatted for the viewer's locale: Arabic differs from English and an invalid time formats to empty", () => {
    const en = formatSelectedAt("2026-10-02T12:00:00+00:00", "en");
    const ar = formatSelectedAt("2026-10-02T12:00:00+00:00", "ar");
    expect(en).not.toBe("");
    expect(ar).not.toBe(en);
    expect(formatSelectedAt("not a date", "en")).toBe("");
  });

  test("the candidate's confidence stays labelled in both languages (exact strings)", async () => {
    fetchMock().mockResolvedValueOnce(ok(CANDIDATE_PAGE()));
    render(candidateUI("ar"));
    fireEvent.click(screen.getByTestId("candidate-load-button"));
    await screen.findByTestId(`candidate-row-${CAND_B}`);
    expect(screen.getByTestId(`candidate-confidence-${CAND_B}`).textContent).toBe(
      "درجة الثقة: 0.9",
    );
  });
});

// ---------------------------------------------------------------------------------------------
// UI CHECKLIST (a): generated strict-parse tables -- a wrong JSON type, a value outside an enum or a
// missing required field is malformed and offers no action control.
// ---------------------------------------------------------------------------------------------
type Mutation = { name: string; mutate: (body: Record<string, unknown>) => unknown };

function fieldMutations(
  fields: Record<string, { wrong: unknown[] }>,
  deleteToo = true,
): Mutation[] {
  const out: Mutation[] = [];
  for (const [field, { wrong }] of Object.entries(fields)) {
    for (const value of wrong) {
      out.push({
        name: `${field}=${JSON.stringify(value)}`,
        mutate: (body) => ({ ...body, [field]: value }),
      });
    }
    if (deleteToo) {
      out.push({
        name: `${field} missing`,
        mutate: (body) => {
          const copy = { ...body };
          delete copy[field];
          return copy;
        },
      });
    }
  }
  return out;
}

describe("[UX03-R2-STRICT] generated strict-parse tables", () => {
  const REPO_FIELDS = {
    repositories: { wrong: ["x", {}, null, 5] },
    page: { wrong: ["1", 1.5, null, 2] },
    per_page: { wrong: ["30", 1.5, null] },
    has_next_page: { wrong: ["true", 1, null] },
  };
  for (const m of fieldMutations(REPO_FIELDS)) {
    test(`04A03 listing: ${m.name} is malformed, shows no repository and no select control`, async () => {
      await loadRepositories(undefined, m.mutate(REPO_PAGE()));
      const error = await screen.findByTestId("github-repository-error");
      expect(error.textContent).toContain("cannot read");
      expect(document.querySelectorAll('[data-testid^="github-repository-option-"]').length).toBe(
        0,
      );
      expect(screen.queryByTestId("github-load-more-button")).toBeNull();
    });
  }
  const REPO_ENTRY_FIELDS = {
    repository_identity: { wrong: [5, "", "  ", null] },
    private: { wrong: ["false", 0, null] },
    default_branch: { wrong: [5, null] },
  };
  for (const m of fieldMutations(REPO_ENTRY_FIELDS)) {
    test(`04A03 entry: ${m.name} is malformed and nothing from that reply is listed`, async () => {
      const entry = m.mutate({
        repository_identity: "acme/one",
        private: false,
        default_branch: "main",
      });
      await loadRepositories(undefined, REPO_PAGE({ repositories: [entry] }));
      await screen.findByTestId("github-repository-error");
      expect(document.querySelectorAll('[data-testid^="github-repository-option-"]').length).toBe(
        0,
      );
    });
  }
  test("04A03 entry: a non-object entry is malformed", () => {
    expect(bindRepositoryListing(1, REPO_PAGE({ repositories: [5] }))).toBeNull();
    expect(bindRepositoryListing(1, REPO_PAGE({ repositories: [null] }))).toBeNull();
    expect(bindRepositoryListing(1, null)).toBeNull();
    expect(bindRepositoryListing(1, [])).toBeNull();
    expect(bindRepositoryListing(1, REPO_PAGE())).not.toBeNull();
  });

  const BRANCH_FIELDS = {
    branches: { wrong: ["x", {}, null] },
    page: { wrong: ["1", null, 3] },
    per_page: { wrong: ["30", null] },
    has_next_page: { wrong: ["false", null] },
  };
  for (const m of fieldMutations(BRANCH_FIELDS)) {
    test(`04A04 listing: ${m.name} is malformed, lists no branch`, async () => {
      fetchMock().mockResolvedValueOnce(ok(m.mutate(BRANCH_PAGE())));
      render(branchSelector());
      fireEvent.click(screen.getByTestId("github-load-branches-button"));
      await screen.findByTestId("github-branch-ref-error");
      expect(document.querySelectorAll('[data-testid^="github-branch-option-"]').length).toBe(0);
      expect(screen.queryByTestId("github-branch-load-more-button")).toBeNull();
    });
  }
  for (const m of fieldMutations({
    name: { wrong: [5, "", null] },
    list_time_commit_sha: { wrong: [5, null] },
  })) {
    test(`04A04 entry: ${m.name} is malformed`, () => {
      const entry = m.mutate({ name: "main", list_time_commit_sha: "a".repeat(40) });
      expect(bindBranchListing(1, BRANCH_PAGE({ branches: [entry] }))).toBeNull();
    });
  }
  for (const m of fieldMutations({
    ref: { wrong: ["other", 5, null, ""] },
    resolved_commit_sha: {
      wrong: ["xyz", "a".repeat(39), "a".repeat(41), "g".repeat(40), 5, null],
    },
  })) {
    test(`04A04 resolve reply: ${m.name} is not bound to the request`, () => {
      expect(
        bindResolvedRef("main", m.mutate({ ref: "main", resolved_commit_sha: SHA })),
      ).toBeNull();
    });
  }
  test("04A04 resolve reply: a 64-hex object id and an exact echo bind", () => {
    expect(
      bindResolvedRef("main", { ref: "main", resolved_commit_sha: "f".repeat(64) }),
    ).not.toBeNull();
    expect(bindResolvedRef("main", { ref: "main", resolved_commit_sha: SHA })).toEqual({
      ref: "main",
      resolved_commit_sha: SHA,
    });
  });

  const CAND_LIST_FIELDS = {
    items: { wrong: ["x", {}, null] },
    limit: { wrong: ["50", 1.5, null] },
    offset: { wrong: ["0", null] },
    total: { wrong: ["2", null] },
  };
  for (const m of fieldMutations(CAND_LIST_FIELDS)) {
    test(`04A18 listing: ${m.name} is malformed, lists no candidate and no select control`, async () => {
      fetchMock().mockResolvedValueOnce(ok(m.mutate(CANDIDATE_PAGE())));
      render(candidateUI());
      fireEvent.click(screen.getByTestId("candidate-load-button"));
      await screen.findByTestId("candidate-selection-error");
      expect(document.querySelectorAll('[data-testid^="candidate-select-button-"]').length).toBe(0);
    });
  }
  const CAND_FIELDS = {
    id: { wrong: [5, "", null] },
    display_name: { wrong: [5, "", " ", null] },
    framework_or_runtime: { wrong: [5, {}] },
    confidence_score: { wrong: ["0.5", null, -0.1, 1.1, Number.NaN] },
    discovery_status: { wrong: ["selected", "", 5, null] },
  };
  for (const m of fieldMutations(CAND_FIELDS)) {
    test(`04A18 entry: ${m.name} is malformed and the whole reply is refused`, async () => {
      fetchMock().mockResolvedValueOnce(ok(CANDIDATE_PAGE({ items: [m.mutate(CANDIDATE())] })));
      render(candidateUI());
      fireEvent.click(screen.getByTestId("candidate-load-button"));
      await screen.findByTestId("candidate-selection-error");
      expect(document.querySelectorAll('[data-testid^="candidate-select-button-"]').length).toBe(0);
    });
  }
  test("04A18 entry: framework_or_runtime may be null (documented) and the listing binds", () => {
    expect(
      bindCandidateListing(CANDIDATE_PAGE({ items: [CANDIDATE({ framework_or_runtime: null })] })),
    ).not.toBeNull();
  });

  for (const m of fieldMutations({
    candidate_id: { wrong: [CAND_A, 5, null, ""] },
    target_source_snapshot_id: { wrong: ["55555555-0000-0000-0000-000000000000", 5, null] },
    selected_by: { wrong: [5, "", " ", null] },
    selected_at: { wrong: ["yesterday", 5, null, ""] },
  })) {
    test(`04A18 select reply: ${m.name} is not bound -- nothing is confirmed and the callback never fires`, async () => {
      fetchMock().mockResolvedValueOnce(ok(CANDIDATE_PAGE()));
      const onSelected = vi.fn();
      render(
        createElement(CandidateSelectionUI, {
          apiBaseUrl: API,
          organizationId: ORG,
          projectId: PROJECT,
          connectionId: CONNECTION,
          snapshotId: SNAPSHOT,
          onSelected,
        }),
      );
      fireEvent.click(screen.getByTestId("candidate-load-button"));
      await screen.findByTestId(`candidate-row-${CAND_B}`);
      fetchMock().mockResolvedValueOnce(ok(m.mutate(SELECTION())));
      fireEvent.click(screen.getByTestId(`candidate-select-button-${CAND_B}`));
      await screen.findByTestId("candidate-selection-error");
      expect(screen.queryByTestId("candidate-selection-confirmation")).toBeNull();
      expect(onSelected).not.toHaveBeenCalled();
    });
  }
  test("04A18 select reply: the exact bound reply binds", () => {
    expect(bindSelection(CAND_B, SNAPSHOT, SELECTION())).toEqual(SELECTION());
    expect(bindSelection(CAND_B, SNAPSHOT, [])).toBeNull();
  });
});

// ---------------------------------------------------------------------------------------------
// UI CHECKLIST (b): the request carries exactly what was asked, and a reply only counts when it
// answers that request. One fault per field sent.
// ---------------------------------------------------------------------------------------------
describe("[UX03-R2-BIND] requests carry every field and replies bind to them", () => {
  test("04A03: the request body is exactly {credential, page}; 'Load more' asks for page 2 and a page-1 reply to it is refused", async () => {
    await loadRepositories(undefined, REPO_PAGE({ has_next_page: true }));
    await screen.findByTestId("github-load-more-button");
    expect(JSON.parse(fetchMock().mock.calls[0]![1].body)).toEqual({ credential: "tok", page: 1 });
    fetchMock().mockResolvedValueOnce(ok(REPO_PAGE({ page: 1 })));
    fireEvent.click(screen.getByTestId("github-load-more-button"));
    await screen.findByTestId("github-repository-error");
    expect(JSON.parse(fetchMock().mock.calls[1]![1].body)).toEqual({ credential: "tok", page: 2 });
    expect(document.querySelectorAll('[data-testid^="github-repository-option-"]').length).toBe(1);
  });

  test("04A04: the branches request carries credential, repository and page; the resolve request carries credential, repository, ref and connection", async () => {
    fetchMock().mockResolvedValueOnce(ok(BRANCH_PAGE()));
    render(branchSelector(undefined, "acme/app"));
    fireEvent.click(screen.getByTestId("github-load-branches-button"));
    await screen.findByTestId("github-branch-option-main");
    expect(JSON.parse(fetchMock().mock.calls[0]![1].body)).toEqual({
      credential: "ghp_token",
      repository_identity: "acme/app",
      page: 1,
    });
    fetchMock().mockResolvedValueOnce(ok({ ref: "main", resolved_commit_sha: SHA }));
    fireEvent.click(screen.getByTestId("github-branch-option-main"));
    fireEvent.click(screen.getByTestId("github-resolve-ref-button"));
    await screen.findByTestId("github-resolved-ref-notice");
    expect(JSON.parse(fetchMock().mock.calls[1]![1].body)).toEqual({
      credential: "ghp_token",
      repository_identity: "acme/app",
      ref: "main",
      target_application_connection_id: CONNECTION,
    });
  });

  test("04A04: a resolve reply that echoes a different ref is refused, not shown as the typed ref's commit", async () => {
    render(branchSelector());
    fireEvent.change(screen.getByTestId("github-ref-input"), { target: { value: "main" } });
    const onResolve = vi.fn();
    cleanup();
    render(
      createElement(GitHubBranchRefSelector, {
        apiBaseUrl: API,
        organizationId: ORG,
        projectId: PROJECT,
        credential: "t",
        repositoryIdentity: "acme/app",
        targetApplicationConnectionId: CONNECTION,
        defaultBranch: "main",
        onResolve,
      }),
    );
    fetchMock().mockResolvedValueOnce(ok({ ref: "develop", resolved_commit_sha: SHA }));
    fireEvent.click(screen.getByTestId("github-resolve-ref-button"));
    await screen.findByTestId("github-branch-ref-error");
    expect(screen.queryByTestId("github-resolved-ref-notice")).toBeNull();
    expect(onResolve).not.toHaveBeenCalled();
  });

  test("04A04 (stale race): a reply for a ref that is no longer in the input is ignored and never shown or reported", async () => {
    const onResolve = vi.fn();
    render(
      createElement(GitHubBranchRefSelector, {
        apiBaseUrl: API,
        organizationId: ORG,
        projectId: PROJECT,
        credential: "t",
        repositoryIdentity: "acme/app",
        targetApplicationConnectionId: CONNECTION,
        defaultBranch: "main",
        onResolve,
      }),
    );
    const pending = inFlight();
    fetchMock().mockReturnValueOnce(pending.promise);
    fireEvent.click(screen.getByTestId("github-resolve-ref-button"));
    fireEvent.change(screen.getByTestId("github-ref-input"), { target: { value: "develop" } });
    await act(async () => {
      pending.release(ok({ ref: "main", resolved_commit_sha: SHA }));
    });
    expect(screen.queryByTestId("github-resolved-ref-notice")).toBeNull();
    expect(onResolve).not.toHaveBeenCalled();
    expect((screen.getByTestId("github-ref-input") as HTMLInputElement).value).toBe("develop");
  });

  test("04A18: the select request POSTs the clicked candidate's URL and a reply for a different candidate is refused", async () => {
    fetchMock().mockResolvedValueOnce(ok(CANDIDATE_PAGE()));
    render(candidateUI());
    fireEvent.click(screen.getByTestId("candidate-load-button"));
    await screen.findByTestId(`candidate-row-${CAND_A}`);
    fetchMock().mockResolvedValueOnce(ok(SELECTION({ candidate_id: CAND_B })));
    fireEvent.click(screen.getByTestId(`candidate-select-button-${CAND_A}`));
    await screen.findByTestId("candidate-selection-error");
    expect(fetchMock().mock.calls[1]![0]).toBe(
      `${API}/api/v1/organizations/${ORG}/projects/${PROJECT}/target-connections/${CONNECTION}/snapshots/${SNAPSHOT}/candidates/${CAND_A}/select`,
    );
    expect(screen.queryByTestId("candidate-selection-confirmation")).toBeNull();
  });
});

// ---------------------------------------------------------------------------------------------
// UI CHECKLIST (c): identity -- state resets when the organization / project / snapshot changes
// ---------------------------------------------------------------------------------------------
describe("[UX03-R2-IDENTITY] state never carries over to another organization or snapshot", () => {
  test("04A03: the token, list and selection reset when the organization changes", async () => {
    fetchMock().mockResolvedValueOnce(ok(REPO_PAGE()));
    const view = render(repoSelector(undefined, ORG));
    fireEvent.change(screen.getByTestId("github-credential-input"), { target: { value: "tok" } });
    fireEvent.click(screen.getByTestId("github-load-repositories-button"));
    fireEvent.click(await screen.findByTestId("github-repository-option-acme/one"));
    expect(screen.getByTestId("github-repository-selected-notice")).toBeTruthy();
    view.rerender(repoSelector(undefined, ORG_B));
    expect((screen.getByTestId("github-credential-input") as HTMLInputElement).value).toBe("");
    expect(screen.queryByTestId("github-repository-option-acme/one")).toBeNull();
    expect(screen.queryByTestId("github-repository-selected-notice")).toBeNull();
  });

  test("04A04: the branches, typed ref and resolved commit reset when the repository changes", async () => {
    fetchMock().mockResolvedValueOnce(ok(BRANCH_PAGE()));
    const view = render(branchSelector(undefined, "acme/app"));
    fireEvent.click(screen.getByTestId("github-load-branches-button"));
    fireEvent.click(await screen.findByTestId("github-branch-option-main"));
    fetchMock().mockResolvedValueOnce(ok({ ref: "main", resolved_commit_sha: SHA }));
    fireEvent.click(screen.getByTestId("github-resolve-ref-button"));
    await screen.findByTestId("github-resolved-ref-notice");
    view.rerender(branchSelector(undefined, "acme/other"));
    expect(screen.queryByTestId("github-branch-option-main")).toBeNull();
    expect(screen.queryByTestId("github-resolved-ref-notice")).toBeNull();
    expect((screen.getByTestId("github-ref-input") as HTMLInputElement).value).toBe("");
  });

  test("04A18: candidates and the confirmation reset when the snapshot changes", async () => {
    fetchMock().mockResolvedValueOnce(ok(CANDIDATE_PAGE()));
    const view = render(candidateUI(undefined, SNAPSHOT));
    fireEvent.click(screen.getByTestId("candidate-load-button"));
    await screen.findByTestId(`candidate-row-${CAND_B}`);
    fetchMock().mockResolvedValueOnce(ok(SELECTION()));
    fireEvent.click(screen.getByTestId(`candidate-select-button-${CAND_B}`));
    await screen.findByTestId("candidate-selection-confirmation");
    view.rerender(candidateUI(undefined, "77777777-7777-7777-7777-777777777777"));
    expect(screen.queryByTestId(`candidate-row-${CAND_B}`)).toBeNull();
    expect(screen.queryByTestId("candidate-selection-confirmation")).toBeNull();
  });

  test("04A18: a late list reply from the previous snapshot is not shown under the new one", async () => {
    const pending = inFlight();
    fetchMock().mockReturnValueOnce(pending.promise);
    const view = render(candidateUI(undefined, SNAPSHOT));
    fireEvent.click(screen.getByTestId("candidate-load-button"));
    view.rerender(candidateUI(undefined, "77777777-7777-7777-7777-777777777777"));
    await act(async () => {
      pending.release(ok(CANDIDATE_PAGE()));
    });
    expect(screen.queryByTestId(`candidate-row-${CAND_A}`)).toBeNull();
  });
});

// ---------------------------------------------------------------------------------------------
// Existing guarantees, re-pinned through the rebuilt components
// ---------------------------------------------------------------------------------------------
describe("[UX03-R2-KEEP] no auto-selection, selection marked by more than colour", () => {
  test("04A03: nothing is selected after a load; a click selects with a visible check mark, aria-pressed and a status notice", async () => {
    await loadRepositories();
    const option = await screen.findByTestId("github-repository-option-acme/one");
    expect(option.getAttribute("aria-pressed")).toBe("false");
    expect(screen.queryByTestId("github-repository-selected-notice")).toBeNull();
    fireEvent.click(option);
    expect(option.getAttribute("aria-pressed")).toBe("true");
    expect(option.textContent).toContain("✓");
    expect(screen.getByTestId("github-repository-selected-notice").getAttribute("role")).toBe(
      "status",
    );
  });

  test("04A04: a typed ref clears a resolved commit immediately", async () => {
    render(
      createElement(GitHubBranchRefSelector, {
        apiBaseUrl: API,
        organizationId: ORG,
        projectId: PROJECT,
        credential: "t",
        repositoryIdentity: "acme/app",
        targetApplicationConnectionId: CONNECTION,
        defaultBranch: "main",
      }),
    );
    fetchMock().mockResolvedValueOnce(ok({ ref: "main", resolved_commit_sha: SHA }));
    fireEvent.click(screen.getByTestId("github-resolve-ref-button"));
    await screen.findByTestId("github-resolved-ref-notice");
    fireEvent.change(screen.getByTestId("github-ref-input"), { target: { value: "mai" } });
    expect(screen.queryByTestId("github-resolved-ref-notice")).toBeNull();
  });

  test("04A18: the list keeps the backend's order and nothing is confirmed on load", async () => {
    fetchMock().mockResolvedValueOnce(ok(CANDIDATE_PAGE()));
    render(candidateUI());
    fireEvent.click(screen.getByTestId("candidate-load-button"));
    await screen.findByTestId(`candidate-row-${CAND_A}`);
    const rows = Array.from(screen.getByTestId("candidate-list").children).map((row) =>
      row.getAttribute("data-testid"),
    );
    expect(rows).toEqual([`candidate-row-${CAND_A}`, `candidate-row-${CAND_B}`]);
    expect(screen.queryByTestId("candidate-selection-confirmation")).toBeNull();
  });
});

// ---------------------------------------------------------------------------------------------
// Pins added after the first fault-injection pass: each one covers a protection that stayed GREEN
// when removed.
// ---------------------------------------------------------------------------------------------
describe("[UX03-R2-EXTRA] 04A03 fault-injection pins", () => {
  test("an empty result followed by a failed search shows the error, not the empty state", async () => {
    await loadRepositories(undefined, REPO_PAGE({ repositories: [] }));
    await screen.findByRole("region", { name: "No accessible repositories found" });
    fetchMock().mockResolvedValueOnce(refused(500, {}));
    fireEvent.click(screen.getByTestId("github-load-repositories-button"));
    await screen.findByTestId("github-repository-error");
    expect(screen.queryByRole("region", { name: "No accessible repositories found" })).toBeNull();
  });

  test("an empty first page that still has a next page is not reported as 'no repositories'", async () => {
    await loadRepositories(undefined, REPO_PAGE({ repositories: [], has_next_page: true }));
    await screen.findByTestId("github-load-more-button");
    expect(screen.queryByRole("region", { name: "No accessible repositories found" })).toBeNull();
  });

  test("the empty state is hidden for the whole of a following search (loading), not only after it", async () => {
    await loadRepositories(undefined, REPO_PAGE({ repositories: [] }));
    await screen.findByRole("region", { name: "No accessible repositories found" });
    const pending = inFlight();
    fetchMock().mockReturnValueOnce(pending.promise);
    fireEvent.submit(screen.getByTestId("github-credential-form"));
    expect(screen.queryByRole("region", { name: "No accessible repositories found" })).toBeNull();
    await act(async () => {
      pending.release(ok(REPO_PAGE({ repositories: [] })));
    });
    await screen.findByRole("region", { name: "No accessible repositories found" });
  });

  test("a busy indicator with the right words appears after one second of waiting (NN/g 1 s rule)", async () => {
    vi.useFakeTimers();
    try {
      const pending = inFlight();
      fetchMock().mockReturnValueOnce(pending.promise);
      render(repoSelector("ar"));
      fireEvent.change(screen.getByTestId("github-credential-input"), { target: { value: "tok" } });
      fireEvent.click(screen.getByTestId("github-load-repositories-button"));
      await act(async () => {
        await vi.advanceTimersByTimeAsync(400);
      });
      expect(screen.queryByText("جارٍ تحميل المستودعات…")).toBeNull();
      await act(async () => {
        await vi.advanceTimersByTimeAsync(1000);
      });
      expect(screen.getByText("جارٍ تحميل المستودعات…")).toBeTruthy();
      await act(async () => {
        pending.release(ok(REPO_PAGE()));
        await vi.advanceTimersByTimeAsync(10);
      });
      expect(screen.queryByText("جارٍ تحميل المستودعات…")).toBeNull();
    } finally {
      vi.useRealTimers();
    }
  });

  test("the selected repository is a primary button and the others stay secondary", async () => {
    await loadRepositories(
      undefined,
      REPO_PAGE({
        repositories: [
          { repository_identity: "acme/one", private: false, default_branch: "main" },
          { repository_identity: "acme/two", private: false, default_branch: "main" },
        ],
      }),
    );
    const one = await screen.findByTestId("github-repository-option-acme/one");
    const two = screen.getByTestId("github-repository-option-acme/two");
    expect(one.getAttribute("data-variant")).toBe("secondary");
    fireEvent.click(one);
    expect(one.getAttribute("data-variant")).toBe("primary");
    expect(two.getAttribute("data-variant")).toBe("secondary");
  });

  test("the selected notice isolates the repository name in <bdi>", async () => {
    await loadRepositories("ar");
    fireEvent.click(await screen.findByTestId("github-repository-option-acme/one"));
    expect(
      screen.getByTestId("github-repository-selected-notice").querySelector("bdi")?.textContent,
    ).toBe("acme/one");
  });

  test("the repository list is a named list", async () => {
    await loadRepositories("ar");
    await screen.findByTestId("github-repository-option-acme/one");
    expect(screen.getByRole("list", { name: "المستودعات المتاحة" })).toBeTruthy();
  });

  test("Load more is disabled while the next page is loading", async () => {
    await loadRepositories(undefined, REPO_PAGE({ has_next_page: true }));
    await screen.findByTestId("github-load-more-button");
    const pending = inFlight();
    fetchMock().mockReturnValueOnce(pending.promise);
    fireEvent.click(screen.getByTestId("github-load-more-button"));
    await waitFor(() =>
      expect((screen.getByTestId("github-load-more-button") as HTMLButtonElement).disabled).toBe(
        true,
      ),
    );
    await act(async () => {
      pending.release(ok(REPO_PAGE({ page: 2 })));
    });
  });

  test("a late reply for an earlier search is ignored once a newer search has been made", async () => {
    render(repoSelector());
    fireEvent.change(screen.getByTestId("github-credential-input"), { target: { value: "first" } });
    const first = inFlight();
    fetchMock().mockReturnValueOnce(first.promise);
    fireEvent.submit(screen.getByTestId("github-credential-form"));
    fireEvent.change(screen.getByTestId("github-credential-input"), {
      target: { value: "second" },
    });
    fetchMock().mockResolvedValueOnce(
      ok(
        REPO_PAGE({
          repositories: [
            { repository_identity: "acme/newer", private: false, default_branch: "m" },
          ],
        }),
      ),
    );
    fireEvent.submit(screen.getByTestId("github-credential-form"));
    await screen.findByTestId("github-repository-option-acme/newer");
    await act(async () => {
      first.release(
        ok(
          REPO_PAGE({
            repositories: [
              { repository_identity: "acme/older", private: false, default_branch: "m" },
            ],
          }),
        ),
      );
    });
    expect(screen.queryByTestId("github-repository-option-acme/older")).toBeNull();
    expect(screen.getByTestId("github-repository-option-acme/newer")).toBeTruthy();
  });
});
