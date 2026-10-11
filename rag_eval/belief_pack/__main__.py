"""prepare는 무과금, execute는 별도 승인 파일을 요구한다."""
import argparse
import json
from pathlib import Path

from .protocol import load_cases, make_plan, read, write_new
from .review import export_review, report
from .run import execute


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("prepare", "execute", "review", "report"))
    parser.add_argument("--cases", type=Path, required=True)
    parser.add_argument("--settings", type=Path)
    parser.add_argument("--plan", type=Path)
    parser.add_argument("--approval", type=Path)
    parser.add_argument("--journal", type=Path)
    parser.add_argument("--mapping", type=Path)
    parser.add_argument("--grades", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    a = parser.parse_args()
    cases = load_cases(a.cases)
    required = {"prepare": ("settings",), "execute": ("plan", "approval"),
                "review": ("plan", "approval", "journal"),
                "report": ("plan", "approval", "journal", "mapping", "grades")}
    for name in required[a.command]:
        if getattr(a, name) is None:
            parser.error(f"--{name} required for {a.command}")
    if a.command == "prepare":
        result = make_plan(cases, read(a.settings))
        write_new(a.out, result)
        print(json.dumps({k: result[k] for k in ("plan_sha256", "call_count", "reserved_usd")}))
        return
    plan, approval = read(a.plan), read(a.approval)
    if a.command == "execute":
        result = execute(plan, cases, approval, a.out)
    elif a.command == "review":
        result = export_review(plan, cases, approval, a.journal, a.out)
    else:
        result = report(plan, cases, approval, a.journal, read(a.mapping), read(a.grades))
        write_new(a.out, result)
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
