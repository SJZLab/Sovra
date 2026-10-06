# Control Plane

The control-plane operating modes (OPERATE · CERTIFY · UNBLOCK · PARKED · AUDIT · REPORT) live in
[docs/CONTROL_PLANE_MODES.md](CONTROL_PLANE_MODES.md). Groundwork v0.7.0 refers to this path; this file is
the alias, so the two can never diverge.

Rules this alias restates from Groundwork v0.7.0:
- The control plane never executes queue items itself. It dispatches through routines
  (`create_trigger` / owner-created routines) and certifies results.
- Relay, certifier and watchdog always run as separate scheduled sessions.
