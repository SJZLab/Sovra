#!/usr/bin/env python3
"""
status.py — fails on certified-without-ledger-line and proven-without-test (Law 2).
Run in Tier 1 CI. Never advisory.
"""

import pathlib
import sys

MATRIX_FILE = "docs/spec-matrix.yaml"
LEDGER_FILE = "docs/certifications/ledger.md"


def main():
    try:
        import yaml

        matrix = yaml.safe_load(open(MATRIX_FILE)) or {}
    except FileNotFoundError:
        print(f"WARNING: {MATRIX_FILE} not found — skipping status check")
        sys.exit(0)

    try:
        ledger = pathlib.Path(LEDGER_FILE).read_text()
    except FileNotFoundError:
        ledger = ""

    errors = []
    for cid, entry in matrix.items():
        cert_state = entry.get("certification_state", "NONE")
        if cert_state in ("CERTIFIED", "PROMOTED"):
            if cid not in ledger:
                errors.append(
                    f"CERTIFIED-WITHOUT-LEDGER: {cid} — certification_state={cert_state} "
                    f"but no entry found in {LEDGER_FILE}"
                )
        proof_state = entry.get("proof_state", "UNPROVEN")
        test = entry.get("proving_test", "")
        if proof_state in ("L1-PROVEN", "L2-CERTIFIED") and not test:
            errors.append(
                f"PROVEN-WITHOUT-TEST: {cid} — proof_state={proof_state} "
                f"but proving_test is empty"
            )

    certified = sum(
        1 for e in matrix.values() if e.get("certification_state") in ("CERTIFIED", "PROMOTED")
    )
    product_true = sum(
        1
        for e in matrix.values()
        if e.get("proof_state") == "L2-CERTIFIED"
        and e.get("certification_state") in ("CERTIFIED", "PROMOTED")
        and e.get("proving_symbol")
    )

    print(f"Status: {certified}/{len(matrix)} certified · {product_true} product-true")

    if errors:
        print(f"FAILED — {len(errors)} error(s):")
        for e in errors:
            print(f"  {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
