# Certification Ledger (append-only, signed)
Format per entry: criterion ID · PR · commit · validated-by · date

## FTF-0001 — Initialize monorepo
- Criterion ID: FTF-0001
- PR: https://github.com/SJZLab/Sovra/pull/3
- Merge commit: 7ab3f6ff1ac7bcc240e546b764af8d67ae0c8561
- Validated-by: control-plane
- Date: 2026-09-15
- Evidence: GitHub Actions run 34995170054 (job `gate`), conclusion
  success, on the PR's merged head; `product_truth_check.py` 0/0 as
  expected (FTF-0001 claims no proving_symbol — structural layout, not a
  live-wired code path). Full evidence trail in
  docs/certifications/queue.md history for this entry (PR #3) and PR #4
  (unrelated pre-existing workflow-YAML fix required to get real CI
  running on PR #3).

SUPERSEDED — original certification was self-certified (PR #3 and PR #5
share session identity), invalid per Law 4, found by watchdog PR #13,
corrected 2026-09-15.

## FTF-0001 — Initialize monorepo (independent re-certification)
- Criterion ID: FTF-0001
- PR: https://github.com/SJZLab/Sovra/pull/3
- Merge commit: 7ab3f6ff1ac7bcc240e546b764af8d67ae0c8561
- Validated-by: session https://claude.ai/code/session_01Rs3uk7ApCoUpnFb9hHS9Uz
  (session-ID comparison performed and recorded before this entry: this
  session's ID does not match PR #3/PR #5's session
  session_01EN6u2CfDd8VdrHDTNyuH4g, nor watchdog PR #13's session
  session_01Y7WHsvr6Xi1RnUnkifEyRZ — independent of both the original
  producer/self-certifier and the watchdog that found the finding)
- Date: 2026-09-15
- Evidence independently re-verified this session, not taken on the
  original ledger entry's word:
  1. Read PR #3's full file diff (`get_files`) — 20 files, all scaffolding
     (root pnpm/uv workspace config, seven component dirs each with
     README + manifest, tests/test_ftf_0001_monorepo_layout.py); matches
     the committed description, nothing beyond FTF-0001's own scope.
  2. Fetched GitHub Actions run 34995170054 directly via the Actions API
     (not the PR body's claim about it): workflow `CI`, event
     `pull_request`, status `completed`, conclusion `success`, real actor
     `samerzakaria`, head_sha `2f63b7f` — a genuine run, not a 0-job parse
     failure.
  3. Ran `tests/test_ftf_0001_monorepo_layout.py` locally against the
     current tree: passes clean (0 failures). Then fault-injected by
     removing `apps/api/pyproject.toml` and re-ran: test correctly
     reddens (`FAIL [FTF-0001] apps/api/pyproject.toml exists`, exit 1) —
     confirms the test can actually fail, not just always pass. File
     restored, re-ran clean again.
  4. Confirmed `product_truth_check.py`'s `0/0 product-true` is legitimate
     for this criterion: `docs/spec-matrix.yaml`'s FTF-0001 entry has
     `proving_symbol: ''` (structural layout, no live-wired code path
     claimed), which is the one case CONTROL_PLANE_MODES.md's CERTIFY
     step 4 says 0/0 is a valid pass rather than an unwired claim.
- Conclusion: the underlying L1 evidence holds up under independent
  re-verification — the prior entry's defect was procedural
  (self-certification), not substantive. Re-certified L2-CERTIFIED.
