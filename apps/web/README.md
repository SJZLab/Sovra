# apps/web — Unified Factory UI

Browser front end for Sovra (Model Factory): project setup, target-repository
connection, dataset/model selection, training/evaluation monitoring, and
approval/adoption flows. See `docs/architecture/ARCHITECTURE.md` section 1
for where this sits in the control path (`Unified Factory UI → Factory API
and policy → ...`).

No implementation yet — this directory is scaffolding from FTF-0001
(Initialize monorepo). Long-running work is driven from Temporal workflows
via the API, never from an open browser tab (ARCHITECTURE.md section 1).
