# Vendored: ui-ux-pro-max (third-party skill)

- Upstream: https://github.com/nextlevelbuilder/ui-ux-pro-max-skill
- Pinned commit: `56ea9cf174ed86786187daa9de0e6247cb0747e4` (2026-10-15), path `.claude/skills/ui-ux-pro-max/`
- License: MIT (`LICENSE`, copied verbatim from the upstream repository root)
- Installed: 2026-10-16, at the owner's request (live: "Make sure we are installing ui/ux pro max skill").
- Copied: `SKILL.md`, `references/`, `data/`, `scripts/*.py`. Not copied: upstream `scripts/tests/` and their
  fixtures, the CLI, gallery and the other bundled skills.
- Local change: `SKILL.md` invokes `${CLAUDE_PROJECT_DIR}/.claude/skills/ui-ux-pro-max/scripts/search.py`
  instead of `${CLAUDE_PLUGIN_ROOT}/...` (it is a project skill here, not a plugin). Nothing else is edited.
- Reviewed before vendoring: the scripts are stdlib-only and open no network connection, subprocess
  or eval/exec. Search runs offline over the CSV data.
- Authority: this skill's output is a recommendation (its own SKILL.md says the same). It never overrides
  CLAUDE.md, FINE_TUNING_FACTORY_RELEASE_1.md §72 (the fixed Sovra tokens, Radix/shadcn component
  pattern, WCAG 2.2 AA) or docs/design/UX_STANDARD.md. Do not use `--design-system` to pick a new palette,
  font or style: Sovra's visual system is fixed by §72.1. Use the `ux`, `chart`, `icons`, `react`/`nextjs`/
  `shadcn` stack and accessibility searches, and the pre-delivery checklists.
- Updating: replace the copied files from a newer pinned upstream commit, update the commit above, and
  re-run the secret scan; it is excluded from ruff/mypy as third-party code.
