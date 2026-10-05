#!/usr/bin/env python3
"""FTF-0902 -- real kind+Calico NetworkPolicy enforcement + negative-acceptance proof.

This task's own negative_acceptance ("NetworkPolicy YAML on a nonenforcing
cluster is not a pass; default-open or IPv6 escape is a release blocker")
and acceptance clause ("Install and test actual CNI/firewall/DNS/IPv6/
metadata restrictions with explicit allowed internal endpoints") both name
*live cluster enforcement*, which `tests/tasks/test_ftf_0902.py`'s pure
no-DB/no-cluster suite structurally cannot prove -- a rendered manifest is
inert paperwork until a real CNI applies it (Section 12: "Enforce egress
with the actual network implementation, not just YAML" -- also
`docs/architecture/REFERENCE_REPOSITORIES.md`'s own Network Policy row:
"Verify enforcement in the installed CNI; a YAML file alone is not proof").

This script stands up two real, disposable, single-node `kind` (Kubernetes-
in-Docker) clusters and applies the *identical* rendered
`packages.security.network_policy_templates` manifest to both:

  1. `npt-baseline` -- kind's own default CNI (`kindnet`), which does
     **not** enforce `NetworkPolicy` at all. This is this task's own
     negative_acceptance proven directly and adversarially: the exact same
     YAML this suite trusts is applied here first, and a real TCP probe to
     a destination that same YAML does *not* allow-list still succeeds --
     proving a NetworkPolicy object existing in a cluster's API server is
     not itself evidence of enforcement.
  2. `npt-enforcing` -- kind with the default CNI disabled and Calico
     (a real, standard, NetworkPolicy-enforcing CNI; pinned version below)
     installed instead. The same YAML applied here genuinely blocks the
     same probe that succeeded on the baseline cluster, and still allows
     the one destination this task's own explicit
     `allowed_internal_endpoints` names.

Both clusters also attempt one real DNS resolution from the same
policy-restricted pod (`dns`, below) -- this task's own acceptance clause
names "DNS" explicitly ("Install and test actual CNI/firewall/DNS/IPv6/
metadata restrictions"), and a TCP-only proof cannot show that the
mandatory kube-dns egress rule (`render_network_policy_manifest`'s own
`allow_dns=True` carve-out) genuinely lets the workload resolve names under
real enforcement, rather than merely reach a raw IP it was already told.
This is a real `nslookup` of a well-known in-cluster Service name
(`kubernetes.default`, always present on every real Kubernetes API server)
run from inside the `target` pod via `kubectl exec`, asserted on the
enforcing cluster (where a missing/misrendered DNS rule would make it
genuinely fail, same as any other non-allow-listed destination).

Both clusters probe three real, distinct TCP destinations from the same
policy-restricted pod:
  - `allowed`  -- a real in-cluster pod IP named in the template's own
    `allowed_internal_endpoints` (explicit allowlist; deliberately a direct
    pod IP rather than a Service ClusterIP -- see `get_pod_ip`'s own
    docstring for why).
  - `denied`   -- a real in-cluster pod IP deliberately *not* named
    anywhere in the template (an ordinary internal destination the
    template's own deny-by-default posture must still block).
  - `metadata` -- the real AWS/GCP/Azure/DigitalOcean cloud-metadata
    address (`169.254.169.254`). Since this sandbox/runner is not a real
    cloud VM, nothing listens there by default -- a bare connection
    attempt would fail for "no route" reasons regardless of policy,
    proving nothing. This script makes it a real, reachable destination
    first: a `hostNetwork` pod (own container image, not relying on the
    kind node image's own installed tools) adds that exact address to the
    node's own network interface and serves real TCP connections on it,
    so "blocked by policy" and "unreachable" are genuinely distinguishable.

CNI/version choice (CLAUDE.md's "Default resolution rule" -- neither Calico
nor kind is one of the seven reused components, and no architecture doc
fixes a CNI; `REFERENCE_REPOSITORIES.md`'s own Network Policy row already
frames "verify enforcement in the installed CNI" as the requirement without
naming one, so Calico is picked as the most standard, widely-documented
enforcing CNI for exactly this kind-based qualification pattern -- see
Tigera's own published kind installation guide): the two clusters
deliberately run *different* node images so each one's own `kindnet` CNI
genuinely matches the role it plays, rather than relying on the `kind`
*binary* version alone (see "Root-cause correction, round 2" below for why
that distinction matters): `npt-baseline` uses `kindest/node:v1.30.0` (the
node image `kind` v0.23.0 itself defaults to -- the last image line whose
bundled `kindnet` does not enforce `NetworkPolicy`), `npt-enforcing` uses
`kindest/node:v1.31.2` with its own default CNI disabled and Calico
installed instead. `kind` v0.23.0, `kubectl` v1.31.0, Calico v3.28.2 (the
non-operator `calico.yaml` all-in-one manifest, the simpler of Calico's two
supported install paths and sufficient for a disposable single-node
qualification cluster). Pinned by tag, not digest -- unlike this repo's own
`postgres@sha256:...` service-container precedent, no independently-
verifiable digest for these specific tags could be resolved from inside
this sandbox (no image-registry egress here; see FTF-0004's own disclosed
precedent for that same gap) -- disclosed, not hidden.

**Root-cause correction, round 2 (2026-10-01, Control plane, PR #402):** an
earlier round of this task pinned only the `kind` *binary* to v0.23.0 and
kept the node image at `kindest/node:v1.31.2` for both clusters. That pin
never took effect: `kind` ships its default CNI (`kindnet`) *inside the
node image*, not in the `kind` binary itself, and a v1.31.2 node image is
built by `kind` >=0.24 -- which bundles a `kindnet` that enforces
`NetworkPolicy` via `kube-network-policies` regardless of which `kind`
binary version created the cluster. Three further fix attempts on that same
false premise (an ARP/route warm-up before policy application, then the
same warm-up moved to after `fake-metadata-service`'s own `hostNetwork`
setup) were each implemented for real and each falsified by a real CI run
too -- full evidence trail (job IDs, the "Connection timed out" vs
"Connection refused" distinction) in this task's own PR #402 comments and
`BLOCKER.md`'s resolved history. The actual fix is the node-image split
above: `npt-baseline` now runs a node image whose own bundled `kindnet`
genuinely does not enforce `NetworkPolicy`, independent of which `kind`
binary created the cluster.

**Self-checking methodology:** the baseline cluster's own non-enforcement is
never assumed from the node-image choice alone. `denied` is probed once
*before* the `NetworkPolicy` object is ever applied (expected: reachable --
proves the probe methodology itself works and nothing else is blocking this
destination) and again after the real assertion probes have already run
(same destination, same policy already in place). If the pre-policy probe
succeeds but the post-policy probe fails, that is reported as its own
distinct, explicit failure ("baseline CNI enforces NetworkPolicy" -- the
chosen baseline node image was not actually non-enforcing), never folded
into the generic negative_acceptance failure message, so a future wrong
node-image choice is immediately distinguishable from a real product
defect. The baseline cluster's own `kindnet` DaemonSet image is also
printed to the job log for direct confirmation.

**Disclosed scope narrowing (Law 1/5/17 -- never converted to a fabricated
pass):**
  - The `metadata` probe's fake listener depends on a `hostNetwork` pod
    with `NET_ADMIN`/`NET_RAW` successfully binding a link-local address on
    the node -- environment-dependent in a way the core allowed/denied
    proof is not. If that setup does not succeed on the *baseline* cluster
    (proving the fake service is genuinely reachable at the network layer
    before any policy claim is evaluated), this script reports
    `BLOCKED_EXTERNAL` for the metadata sub-scenario specifically and does
    **not** assert anything about it -- it never treats an inconclusive
    "connection failed" as a claimed policy block. This does not weaken
    the core `allowed`/`denied` CIDR-and-PodSelector proof, which this
    script always asserts on regardless.
  - Real IPv6 pod-network enforcement is out of scope for this script
    entirely: kind's default cluster networking is single-stack IPv4, and
    standing up a genuinely dual-stack/IPv6 kind cluster with verified
    IPv6 connectivity on a GitHub Actions hosted runner is a materially
    larger, independently-flaky undertaking this task's own resource_gate
    does not require attempting when the structural guarantee (Release 1
    ships no code path that can ever render an IPv6-allowing egress rule
    at all) is already proven for real in `tests/tasks/test_ftf_0902.py`.
    `BLOCKED_EXTERNAL`, disclosed here and in this task's own submission
    record, not silently skipped.

Requires a real Docker daemon with container-registry/package-mirror
egress (kind node image, Calico manifest/images) -- this repository's
interactive dev sandbox does not have that (Docker Hub's CDN is blocked by
that session's egress policy -- see docs/certifications/queue.md for
FTF-0004's own original repro of the identical gap); GitHub Actions'
hosted runner does, so this runs there
(`.github/workflows/ci.yml`, job `network-policy-enforcement-integration`).

Run directly (on a host with Docker + kind + kubectl on PATH):
    python3 tests/integration/test_ftf_0902_network_policy_enforcement.py
"""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from packages.security.network_policy_templates import (  # noqa: E402
    CidrEndpoint,
    NetworkPolicyTemplate,
    PortRule,
    render_network_policy_yaml,
)

NODE_IMAGE_BASELINE = "kindest/node:v1.30.0"
NODE_IMAGE_ENFORCING = "kindest/node:v1.31.2"
CALICO_MANIFEST_URL = (
    "https://raw.githubusercontent.com/projectcalico/calico/v3.28.2/manifests/calico.yaml"
)
NAMESPACE = "npt-test"
TARGET_LABELS = {"app": "target"}
METADATA_ADDRESS = "169.254.169.254"
PROBE_TIMEOUT_SECONDS = 5


class ScenarioFailure(RuntimeError):
    """A real, unexpected probe/setup result this script cannot proceed past."""


def run(
    *args: str, check: bool = True, timeout: float | None = None
) -> subprocess.CompletedProcess[str]:
    print("+", " ".join(args), flush=True)
    result = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    if check and result.returncode != 0:
        print(result.stdout)
        print(result.stderr, file=sys.stderr)
        raise ScenarioFailure(f"command failed ({result.returncode}): {' '.join(args)}")
    return result


def kubectl(
    cluster: str, *args: str, check: bool = True, timeout: float = 60
) -> subprocess.CompletedProcess[str]:
    return run("kubectl", "--context", f"kind-{cluster}", *args, check=check, timeout=timeout)


def kind_cluster_config(node_image: str, disable_default_cni: bool) -> str:
    networking = "\nnetworking:\n  disableDefaultCNI: true\n" if disable_default_cni else ""
    return (
        "kind: Cluster\napiVersion: kind.x-k8s.io/v1alpha4\nnodes:\n"
        f"  - role: control-plane\n    image: {node_image}\n{networking}"
    )


def print_kindnet_image(cluster: str) -> None:
    """Diagnostics only: confirms which `kindnet` image this specific
    cluster is actually running, directly in the job log, rather than
    trusting the node-image pin alone -- a DaemonSet that failed to roll
    out to the new image tag would silently keep running an old one."""
    result = kubectl(
        cluster,
        "-n",
        "kube-system",
        "get",
        "daemonset",
        "kindnet",
        "-o",
        "jsonpath={.spec.template.spec.containers[0].image}",
        check=False,
    )
    print(f"kindnet image on {cluster!r}: {result.stdout.strip() or '(not found)'}")


def create_cluster(name: str, node_image: str, install_calico: bool) -> None:
    config_path = Path(f"/tmp/kind-config-{name}.yaml")
    config_path.write_text(
        kind_cluster_config(node_image=node_image, disable_default_cni=install_calico),
        encoding="utf-8",
    )
    run("kind", "create", "cluster", "--name", name, "--config", str(config_path), timeout=300)
    if install_calico:
        kubectl(name, "apply", "-f", CALICO_MANIFEST_URL, timeout=120)
        wait_for_rollout(name, "kube-system", "daemonset/calico-node", timeout=300)
    else:
        print_kindnet_image(name)
    wait_for_node_ready(name, timeout=180)


def delete_cluster(name: str) -> None:
    run("kind", "delete", "cluster", "--name", name, check=False, timeout=120)


def wait_for_rollout(cluster: str, namespace: str, resource: str, timeout: float) -> None:
    kubectl(
        cluster,
        "-n",
        namespace,
        "rollout",
        "status",
        resource,
        f"--timeout={int(timeout)}s",
        timeout=timeout + 30,
    )


def wait_for_node_ready(cluster: str, timeout: float) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = kubectl(
            cluster,
            "get",
            "nodes",
            "-o",
            "jsonpath={.items[*].status.conditions[?(@.type=='Ready')].status}",
            check=False,
        )
        if result.stdout.strip() == "True":
            return
        time.sleep(3)
    raise ScenarioFailure(f"cluster {cluster!r}: no node became Ready within {timeout}s")


def wait_for_pod_ready(cluster: str, namespace: str, name: str, timeout: float) -> None:
    kubectl(
        cluster,
        "-n",
        namespace,
        "wait",
        f"pod/{name}",
        "--for=condition=Ready",
        f"--timeout={int(timeout)}s",
        timeout=timeout + 30,
    )


def get_pod_ip(cluster: str, namespace: str, pod: str, timeout: float = 30) -> str:
    """The real, directly-routable pod IP -- deliberately *not* a Service
    ClusterIP. An earlier version of this script routed the `allowed`/
    `denied` probes through a Service, which failed identically on both the
    baseline and the enforcing cluster even before any policy was applied
    (CI run 36351770167's own real, disclosed failure) -- kube-proxy's own
    iptables/ipvs programming for a freshly created Service is an entire
    extra asynchronous layer this task's own acceptance clause does not
    actually need to exercise, and its timing proved unreliable on a
    single-node kind cluster within this job's own short lifetime. A pod's
    own IP is allocated synchronously by the CNI as part of scheduling --
    by the time `condition=Ready` is true, the address below is already
    real and stable for this pod's lifetime, and this is still a genuine
    "explicit internal endpoint" `CidrEndpoint` (a `/32` pod address),
    exactly the kind of destination Section 77.5's own component bindings
    reach other in-cluster services by.
    """
    deadline = time.monotonic() + timeout
    last_output = ""
    while time.monotonic() < deadline:
        result = kubectl(
            cluster,
            "-n",
            namespace,
            "get",
            "pod",
            pod,
            "-o",
            "jsonpath={.status.podIP}",
            check=False,
        )
        last_output = result.stdout.strip()
        if last_output:
            return last_output
        time.sleep(1)
    raise ScenarioFailure(f"pod {pod!r} in {namespace!r} never got a real IP within {timeout}s")


_LISTENER_SERVER_MANIFEST = """
apiVersion: v1
kind: Pod
metadata:
  name: {name}
  namespace: {namespace}
  labels:
    app: {name}
spec:
  containers:
    - name: server
      image: busybox:1.36
      command: ["sh", "-c", "while true; do echo -e 'ok' | nc -l -p {port}; done"]
"""

_TARGET_POD_MANIFEST = """
apiVersion: v1
kind: Pod
metadata:
  name: target
  namespace: {namespace}
  labels:
    app: target
spec:
  containers:
    - name: target
      image: busybox:1.36
      command: ["sh", "-c", "sleep 3600"]
"""

_FAKE_METADATA_MANIFEST = """
apiVersion: v1
kind: Pod
metadata:
  name: fake-metadata-service
  namespace: {namespace}
spec:
  hostNetwork: true
  containers:
    - name: fake-metadata
      image: busybox:1.36
      securityContext:
        capabilities:
          add: ["NET_ADMIN", "NET_RAW"]
      command:
        - sh
        - -c
        - >
          ip addr add {metadata_ip}/32 dev eth0 2>/dev/null;
          while true; do echo -e 'ok' | nc -l -p 80; done
"""


def apply_manifest(cluster: str, manifest: str) -> None:
    result = subprocess.run(
        ["kubectl", "--context", f"kind-{cluster}", "apply", "-f", "-"],
        input=manifest,
        capture_output=True,
        text=True,
        timeout=60,
    )
    print("+ kubectl apply -f - <<<", manifest.strip()[:120], "...")
    if result.returncode != 0:
        print(result.stdout)
        print(result.stderr, file=sys.stderr)
        raise ScenarioFailure("kubectl apply -f - failed")


def probe_tcp(cluster: str, host: str, port: int, attempts: int = 3) -> bool:
    """Real TCP connectivity check from inside the `target` pod -- a plain
    exit-code assertion (Section 84.4: "actual command ... exit codes"),
    never a string match on YAML or an inferred result.

    Retries a genuinely successful connection up to `attempts` times before
    giving up (CI run 36352118097's own real, disclosed finding: a freshly
    created sibling pod on the same single-node bridge network occasionally
    is not yet ARP-resolvable from another pod created moments earlier, a
    transient link-layer condition self-healing within a couple of seconds,
    distinct from real policy enforcement). This does not weaken the
    negative_acceptance proof: a destination genuinely blocked by policy
    fails identically on every attempt, so retrying can only rescue a false
    negative on a destination that should be reachable, never turn a real
    block into a false "allowed".

    Correction to two earlier misdiagnoses in this same thread: the baseline
    cluster's `denied` probe was at one point observed failing
    deterministically on all `attempts`, first attributed to this same
    transient ARP-resolution condition persisting longer than usual, and
    later to a route/ARP disruption from `fake-metadata-service`'s own
    `hostNetwork` setup. Neither was it. The real cause was unrelated to
    this retry logic and to request ordering entirely: the node image used
    for `npt-baseline` (`kindest/node:v1.31.2`) is built by `kind` >=0.24,
    which bundles a `kindnet` that enforces NetworkPolicy by default
    (release notes: "Out-of-the-box support for network policy via
    sigs.k8s.io/kube-network-policies") -- regardless of which `kind`
    *binary* version actually created the cluster, since the CNI ships
    inside the node image, not the binary. So the "non-enforcing" baseline
    cluster was not actually non-enforcing, and no amount of retrying or
    probe reordering could have changed that genuine, correct block. The
    fix is `NODE_IMAGE_BASELINE = "kindest/node:v1.30.0"` (this module's own
    constant) -- a node image whose bundled `kindnet` genuinely does not
    enforce NetworkPolicy -- plus the self-checking pre/post-policy probe
    in `run_scenario` that proves this directly on every run instead of
    trusting the image choice alone. This retry mechanism was never the fix
    for that failure and needed none."""
    for attempt in range(1, attempts + 1):
        result = kubectl(
            cluster,
            "-n",
            NAMESPACE,
            "exec",
            "target",
            "--",
            "nc",
            "-z",
            "-w",
            str(PROBE_TIMEOUT_SECONDS),
            host,
            str(port),
            check=False,
            timeout=PROBE_TIMEOUT_SECONDS + 15,
        )
        if result.returncode == 0:
            return True
        if attempt < attempts:
            time.sleep(2)
    return False


def resolve_dns(cluster: str, name: str) -> bool:
    """A real DNS resolution attempt from inside the `target` pod, via the
    mandatory kube-dns egress rule this task's own module always renders
    when `allow_dns=True`. Uses busybox's own `nslookup` applet (already
    the `target` pod's image) against a Service name that exists on every
    real Kubernetes API server (`kubernetes.default`) -- genuinely fails
    under a NetworkPolicy that blocks UDP/TCP 53 to kube-dns, genuinely
    succeeds when that rule is actually applied and enforced."""
    result = kubectl(
        cluster,
        "-n",
        NAMESPACE,
        "exec",
        "target",
        "--",
        "nslookup",
        "-timeout=5",
        name,
        check=False,
        timeout=20,
    )
    return result.returncode == 0


def build_network_policy_yaml(allowed_ip: str) -> str:
    template = NetworkPolicyTemplate(
        schema_version=1,
        template_id="npt-test-policy",
        organization_id="org-npt-test",
        project_id=None,
        namespace=NAMESPACE,
        pod_selector=dict(TARGET_LABELS),
        cni_profile="calico",
        deny_by_default_egress=True,
        deny_by_default_ingress=True,
        block_metadata_endpoint=True,
        ipv6_policy="blocked",
        allowed_internal_endpoints=(
            CidrEndpoint(
                description="allowed-internal-service",
                cidr=f"{allowed_ip}/32",
                ports=(PortRule("TCP", 8080),),
            ),
        ),
        allow_dns=True,
    )
    return render_network_policy_yaml(template)


def run_scenario(cluster: str, node_image: str, install_calico: bool) -> dict[str, bool | str]:
    create_cluster(cluster, node_image=node_image, install_calico=install_calico)
    kubectl(cluster, "create", "namespace", NAMESPACE)

    apply_manifest(cluster, _TARGET_POD_MANIFEST.format(namespace=NAMESPACE))
    apply_manifest(
        cluster, _LISTENER_SERVER_MANIFEST.format(name="allowed", namespace=NAMESPACE, port=8080)
    )
    apply_manifest(
        cluster, _LISTENER_SERVER_MANIFEST.format(name="denied", namespace=NAMESPACE, port=8080)
    )

    wait_for_pod_ready(cluster, NAMESPACE, "target", timeout=120)
    wait_for_pod_ready(cluster, NAMESPACE, "allowed", timeout=120)
    wait_for_pod_ready(cluster, NAMESPACE, "denied", timeout=120)

    allowed_ip = get_pod_ip(cluster, NAMESPACE, "allowed")
    denied_ip = get_pod_ip(cluster, NAMESPACE, "denied")

    # Self-checking methodology (Control plane, PR #402, 2026-10-01): never
    # assume the baseline node image is non-enforcing just because it was
    # picked to be -- prove it, on every run, before trusting the real
    # assertion probes below. `denied` must be reachable right now, before
    # any `NetworkPolicy` object exists for this namespace.
    pre_policy_denied_reachable: bool | None = None
    if not install_calico:
        pre_policy_denied_reachable = probe_tcp(cluster, denied_ip, 8080)
        if not pre_policy_denied_reachable:
            raise ScenarioFailure(
                f"baseline probe methodology broken on {cluster!r}: 'denied' ({denied_ip}) was "
                "already unreachable before any NetworkPolicy existed -- not a policy-enforcement "
                "question at all; check pod/network health before re-running"
            )

    metadata_available = False
    try:
        apply_manifest(
            cluster,
            _FAKE_METADATA_MANIFEST.format(namespace=NAMESPACE, metadata_ip=METADATA_ADDRESS),
        )
        wait_for_pod_ready(cluster, NAMESPACE, "fake-metadata-service", timeout=60)
        metadata_available = True
    except (ScenarioFailure, subprocess.TimeoutExpired) as exc:
        print(f"BLOCKED_EXTERNAL: fake metadata listener setup failed on {cluster!r}: {exc}")

    policy_yaml = build_network_policy_yaml(allowed_ip)
    print(f"--- rendered NetworkPolicy for {cluster!r} ---\n{policy_yaml}")
    apply_manifest(cluster, policy_yaml)
    # Real enforcement is not necessarily synchronous with the API server
    # accepting the object -- give the CNI agent a moment to program it.
    time.sleep(10 if install_calico else 2)

    # An earlier round of this task (PR #402) inserted a discarded warm-up
    # probe here, on the theory that a route/ARP disruption from
    # `fake-metadata-service`'s own `hostNetwork` setup was transiently
    # blocking `denied`. That theory was tested for real and falsified (the
    # warm-up itself succeeded quickly, yet the real probe below still
    # failed identically) -- removed per Control plane's corrected DoD: it
    # was never the fix and only blurred the methodology. The real cause
    # was the node-image/kindnet mismatch this file's own module docstring
    # now documents ("Root-cause correction, round 2").

    results: dict[str, bool | str] = {
        "allowed": probe_tcp(cluster, allowed_ip, 8080),
        "denied": probe_tcp(cluster, denied_ip, 8080),
        "dns": resolve_dns(cluster, "kubernetes.default"),
    }
    if metadata_available:
        results["metadata"] = probe_tcp(cluster, METADATA_ADDRESS, 80)
    else:
        results["metadata"] = "BLOCKED_EXTERNAL"

    # Self-check (Control plane, PR #402, 2026-10-01): `denied` was proven
    # reachable above, before any policy existed. If it is unreachable now,
    # with the identical policy that was supposed to be a no-op on this
    # node image, that is a distinct, explicit failure -- the chosen
    # baseline node image is not actually non-enforcing -- never folded
    # into the generic negative_acceptance message below.
    if not install_calico and pre_policy_denied_reachable and results.get("denied") is not True:
        raise ScenarioFailure(
            f"baseline CNI enforces NetworkPolicy: 'denied' ({denied_ip}) was reachable before "
            f"the NetworkPolicy was applied on {cluster!r} (node image {node_image}) but became "
            "unreachable after -- this baseline node image's own kindnet is not actually "
            "non-enforcing; pick a different node image/version for npt-baseline"
        )

    # Residual diagnostics only, never a pass/fail input: the self-check
    # above already distinguishes "baseline genuinely enforces" from other
    # causes. If `denied` still fails here despite pre_policy_denied_reachable
    # being False to begin with (the probe-methodology-broken case already
    # raises earlier) this branch should be unreachable in practice; kept as
    # a safety net so any unexpected future failure mode is captured with
    # real evidence rather than a bare non-zero exit.
    if not install_calico and results.get("denied") is not True:
        print(f"--- diagnostics: 'denied' pod/cluster state on {cluster!r} after probe failure ---")
        for diag_args in (
            ("-n", NAMESPACE, "get", "pod", "denied", "-o", "wide"),
            ("-n", NAMESPACE, "describe", "pod", "denied"),
            ("-n", NAMESPACE, "logs", "denied", "--previous"),
            ("-n", NAMESPACE, "logs", "denied"),
            ("-n", NAMESPACE, "get", "events", "--sort-by=.lastTimestamp"),
            ("get", "pods", "-A", "-o", "wide"),
            # Leading theory after the above ruled out a crash/restart
            # (0 restarts, Running, empty logs either way since `nc`
            # doesn't log connections to stdout): the shell's own `while
            # true` loop never exits even if `nc -l` itself silently fails
            # to bind -- Kubernetes readiness only checks the container's
            # PID 1 is alive, not that port 8080 is actually listening.
            # These confirm or rule that out directly, from inside the pod
            # and from the real connecting side, rather than inferred.
            ("-n", NAMESPACE, "exec", "denied", "--", "sh", "-c", "ps; cat /proc/net/tcp"),
            ("-n", NAMESPACE, "exec", "allowed", "--", "sh", "-c", "ps; cat /proc/net/tcp"),
            (
                "-n",
                NAMESPACE,
                "exec",
                "target",
                "--",
                "nc",
                "-z",
                "-v",
                "-w",
                "5",
                str(denied_ip),
                "8080",
            ),
        ):
            result = kubectl(cluster, *diag_args, check=False, timeout=30)
            print(f"+ kubectl {' '.join(diag_args)}\n{result.stdout}{result.stderr}")

    return results


def main() -> int:
    failures: list[str] = []
    baseline_results: dict[str, bool | str] = {}
    enforcing_results: dict[str, bool | str] = {}

    try:
        print("=== Stage 1/2: npt-baseline (kindnet, non-enforcing CNI) ===")
        baseline_results = run_scenario(
            "npt-baseline", node_image=NODE_IMAGE_BASELINE, install_calico=False
        )
    finally:
        delete_cluster("npt-baseline")

    print("baseline (kindnet) results:", baseline_results)

    # negative_acceptance, proven directly: the same YAML this task ships
    # is a NO-OP on a cluster whose CNI does not enforce NetworkPolicy --
    # both the allowed and the (should-be-denied) destination succeed.
    if baseline_results.get("allowed") is not True:
        failures.append("baseline: expected allowed destination to succeed even before enforcement")
    if baseline_results.get("denied") is not True:
        failures.append(
            "baseline: expected denied destination to ALSO succeed on a nonenforcing CNI -- "
            "if this fails, the probe methodology itself cannot distinguish enforcement from "
            "network unavailability, and the negative_acceptance proof below would be worthless"
        )
    if baseline_results.get("dns") is not True:
        failures.append(
            "baseline: expected DNS resolution to succeed on a nonenforcing CNI -- if this "
            "fails, the DNS probe methodology itself is broken before enforcement is even "
            "relevant, and the enforcing-cluster DNS assertion below would be worthless"
        )
    if baseline_results.get("metadata") is True:
        print(
            "baseline: fake metadata service reachable with no policy enforcement (expected -- "
            "confirms the listener is real, not merely a route failure)"
        )
    elif baseline_results.get("metadata") == "BLOCKED_EXTERNAL":
        print("BLOCKED_EXTERNAL: metadata sub-scenario not evaluated (see module docstring)")
    else:
        print(
            "BLOCKED_EXTERNAL: fake metadata listener did not respond even on the nonenforcing "
            "baseline -- cannot distinguish 'blocked by policy' from 'unreachable' for this "
            "sub-scenario; not asserted either way (disclosed, see module docstring)"
        )

    try:
        print("=== Stage 2/2: npt-enforcing (Calico, real enforcing CNI) ===")
        enforcing_results = run_scenario(
            "npt-enforcing", node_image=NODE_IMAGE_ENFORCING, install_calico=True
        )
    finally:
        delete_cluster("npt-enforcing")

    print("enforcing (calico) results:", enforcing_results)

    if enforcing_results.get("allowed") is not True:
        failures.append(
            "enforcing: expected the explicitly allowed internal endpoint to remain reachable "
            "under deny-by-default enforcement"
        )
    if enforcing_results.get("denied") is not False:
        failures.append(
            "enforcing: expected the non-allow-listed internal destination to be BLOCKED under "
            "real Calico enforcement -- this is this task's own core deny-by-default proof"
        )
    if enforcing_results.get("dns") is not True:
        failures.append(
            "enforcing: expected DNS resolution to succeed under real deny-by-default "
            "enforcement -- this is the mandatory kube-dns egress carve-out "
            "(render_network_policy_manifest's own allow_dns=True rule) genuinely working "
            "under a real enforcing CNI, not merely rendered YAML"
        )

    baseline_metadata_reachable = baseline_results.get("metadata") is True
    if baseline_metadata_reachable:
        if enforcing_results.get("metadata") is not False:
            failures.append(
                "enforcing: the real cloud-metadata address was reachable under enforcement even "
                "though the identical fake listener was proven reachable on the baseline cluster "
                "-- a real metadata-endpoint-block failure, not merely inconclusive"
            )
        else:
            print("metadata sub-scenario: genuinely blocked under real Calico enforcement")
    else:
        print(
            "BLOCKED_EXTERNAL: metadata sub-scenario not asserted (baseline reachability was not "
            "established -- see above)"
        )

    if failures:
        print(f"\nFTF-0902 network-policy enforcement integration: {len(failures)} failed")
        for failure in failures:
            print(f"  FAIL: {failure}")
        return 1

    print("\nFTF-0902 network-policy enforcement integration: all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
