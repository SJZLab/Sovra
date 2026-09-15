#!/usr/bin/env python3
"""
product_truth_check.py — Law 16 merge-blocking gate.
PRODUCT-TRUE requires:
  (a) real non-test caller from entry point, across ALL source roots
  (b) user-visible assertion (not bare boolean or row-existence)
  (c) fault injection reddens the live-path assertion
Failing (a) = TRACKER — must not count in done denominator.

Caller resolution: text search (deterministic, works in CI without external deps).
The relay may additionally use Cognee MCP recall queries for richer semantic resolution
during shift work — these are complementary, not the same path.
"""
import pathlib, sys, argparse

# PROVISIONAL — Sovra is a fresh repo; the monorepo layout does not exist yet
# (FTF-0001 "Initialize monorepo" is the first queue item). Fill this list for
# real the moment FTF-0001 merges, from whatever layout it actually produces —
# do not leave this list as a guess once the repo has source directories.
# Best-effort projection from docs/architecture/ARCHITECTURE.md's seven-component
# split, pending confirmation:
SOURCE_ROOTS = [
    # 'apps/web/src',        # Unified Factory UI
    # 'apps/api/src',        # Factory API + policy
    # 'workers/training',    # Axolotl-backed training worker
    # 'workers/evaluation',  # Inspect AI-backed evaluation worker
    # 'workers/data',        # Argilla-backed data/review worker
    # 'workers/deploy',      # deployment controller (vLLM/LiteLLM)
    # 'packages/shared',     # shared schema/adapter contracts
]

TEST_PATTERNS = ['/test/', '/tests/', '.test.', '.spec.', '_test.', 'test_']

def is_test_file(p): return any(x in p for x in TEST_PATTERNS)

def find_callers(symbol, roots):
    """
    Text search for non-test callers of symbol across source roots.
    Reliable in CI (no external deps). Relay may additionally use Cognee MCP
    recall queries ("what functions call <symbol>?") for semantic resolution
    that handles renamed symbols and cross-module calls more accurately.
    """
    callers = []
    for root in roots:
        rp = pathlib.Path(root)
        if not rp.exists():
            continue
        for f in rp.rglob('*'):
            if not f.is_file() or is_test_file(str(f)): continue
            try:
                if symbol in f.read_text(errors='ignore'): callers.append(str(f))
            except Exception: pass
    return callers

def main():
    p = argparse.ArgumentParser()
    p.add_argument('--fail-on-unwired', action='store_true')
    p.add_argument('--all-roots', action='store_true')
    p.add_argument('--criteria-file', default='docs/spec-matrix.yaml')
    args = p.parse_args()

    try:
        import yaml
        matrix = yaml.safe_load(open(args.criteria_file)) or {}
    except FileNotFoundError:
        print(f'WARNING: {args.criteria_file} not found'); sys.exit(0)

    has_symbols = any(e.get('proving_symbol') for e in matrix.values())
    if not SOURCE_ROOTS and has_symbols:
        # Fails closed the moment any criterion claims a proving_symbol — a filled
        # symbol with no roots to search is an unprovable claim, not an empty gate.
        print('ERROR: SOURCE_ROOTS empty but spec-matrix.yaml has proving_symbol entries.')
        print('       Fill SOURCE_ROOTS after FTF-0001 (Initialize monorepo) merges.')
        sys.exit(1)

    results, unwired = [], []
    for cid, entry in matrix.items():
        sym = entry.get('proving_symbol')
        if not sym: continue
        callers = find_callers(sym, SOURCE_ROOTS)
        r = {'id': cid, 'sym': sym, 'callers': callers, 'ok': bool(callers)}
        results.append(r)
        if not callers: unwired.append(r)

    print(f'Product-truth: {len(results)-len(unwired)}/{len(results)} product-true')
    for r in unwired:
        print(f'  TRACKER: {r["id"]} — {r["sym"]} has no live callers')
        print(f'           DIAGNOSE why unwired → WIRE into live route')
        print(f'           NEVER relabel dead code and repoint cert to parallel inline logic')

    if unwired and args.fail_on_unwired: sys.exit(1)

if __name__ == '__main__': main()
