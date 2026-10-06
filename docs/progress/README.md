# Relay shift logs (one file per shift)

Each relay shift writes its log as a NEW file here, named `<YYYY-MM-DDTHHMMZ>-<lane>.md`, instead of prepending to
`PROGRESS.md`. `PROGRESS.md` stays the Control-plane summary. New files never conflict across parallel PRs.
