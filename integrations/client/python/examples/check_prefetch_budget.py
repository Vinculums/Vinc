"""Read-only release check for default errors and one explicit budget correction."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from vinc_client import VincClient, VincInvalidArgument


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--roles-json", type=Path,
                        default=Path(__file__).resolve().parents[4] / "agents/roles.json")
    parser.add_argument("--space", default="personal")
    args = parser.parse_args()
    key = os.environ.get("VINC_API_KEY", "")
    if not key.startswith("vinc_ro_"):
        parser.error("VINC_API_KEY must contain a read-only key (vinc_ro_ prefix).")
    roles = json.loads(args.roles_json.read_text())["roles"]
    with VincClient(api_key=key, space=args.space, timeout=60) as client:
        for role in roles:
            required_size = None
            try:
                block = client.prefetch(role["id"], "Review role evidence.")
            except VincInvalidArgument as error:
                if error.code != "prefetch_budget_exceeded":
                    raise
                required_size = error.details["required_size"]
                assert f"required_size={required_size}" in error.message
                assert required_size > error.details["max_chars"]
                budget = error.details["recommended_max_chars"]
                block = client.prefetch(role["id"], "Review role evidence.", max_chars=budget)
                assert len(block) <= budget
            print(json.dumps({"role": role["id"], "required_size": required_size,
                              "context_chars": len(block), "one_correction_passed": True}))


if __name__ == "__main__":
    main()
