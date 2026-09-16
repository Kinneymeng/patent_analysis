from __future__ import annotations

import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if "--self-check" in args:
        from patent_summarizer.portable_self_check import run_portable_self_check

        report_path = None
        if "--self-check-output" in args:
            index = args.index("--self-check-output")
            if index + 1 >= len(args):
                return 2
            report_path = Path(args[index + 1])
        return run_portable_self_check(report_path)

    from patent_summarizer.gui import launch

    launch()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

