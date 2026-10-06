# Relay submissions (one file per submission)

The relay writes each submission here as a NEW file, named `<YYYY-MM-DDTHHMMZ>-<ITEM-ID>.md`. It contains the same
content the relay used to append to `docs/certifications/queue.md`: the clause → test map, the fault → RED table (STEP 5b),
the CI run, and the session ID.

Why: when every relay PR appended to the same shared files, each merge put every other open relay PR into a merge
conflict, and each conflict cost a full shift (2026-10-04 to 06). New files never conflict.

`docs/certifications/queue.md` stays the Control-plane verdict log. Only Control plane appends to it.
