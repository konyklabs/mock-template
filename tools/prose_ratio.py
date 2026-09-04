"""Measure prose (docstring + comment lines) as a share of all lines under src/.

Usage: python tools/prose_ratio.py [--max-file PCT] [--max-total PCT] [--top N] [PATH]
Exit 1 when a threshold is exceeded.
"""

from __future__ import annotations

import argparse
import io
import sys
import tokenize
from pathlib import Path


def measure(path: Path) -> tuple[int, int, int]:
    """Return (code, docstring, comment) line counts for one file."""
    text = path.read_text(encoding="utf-8")
    total = text.count("\n") + (0 if text.endswith("\n") or not text else 1)
    doc_lines: set[int] = set()
    comment_lines: set[int] = set()
    blank_lines: set[int] = set()
    for i, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            blank_lines.add(i)
    prev_type = tokenize.NEWLINE
    for tok in tokenize.generate_tokens(io.StringIO(text).readline):
        if tok.type == tokenize.COMMENT:
            comment_lines.add(tok.start[0])
        elif tok.type == tokenize.STRING and prev_type in (
            tokenize.NEWLINE,
            tokenize.INDENT,
            tokenize.DEDENT,
            tokenize.ENCODING,
        ):
            for n in range(tok.start[0], tok.end[0] + 1):
                doc_lines.add(n)
        if tok.type not in (tokenize.NL, tokenize.COMMENT, tokenize.INDENT, tokenize.DEDENT):
            prev_type = tok.type
    prose = len(doc_lines | comment_lines)
    code = total - len(blank_lines) - prose
    return max(code, 0), len(doc_lines), len(comment_lines - doc_lines)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("path", nargs="?", default="src")
    ap.add_argument("--max-file", type=float, default=None)
    ap.add_argument("--max-total", type=float, default=None)
    ap.add_argument("--top", type=int, default=15)
    args = ap.parse_args()
    rows = []
    for p in sorted(Path(args.path).rglob("*.py")):
        code, doc, com = measure(p)
        rows.append((p, code, doc, com, p.read_text(encoding="utf-8").count("\n")))
    tcode = sum(r[1] for r in rows)
    tprose = sum(r[2] + r[3] for r in rows)
    total = sum(r[4] for r in rows)
    pct = 100.0 * tprose / total if total else 0.0
    failed = False
    over = []
    for p, code, doc, com, n in rows:
        fpct = 100.0 * (doc + com) / n if n else 0.0
        if args.max_file is not None and n >= 40 and fpct > args.max_file:
            over.append((fpct, p, code, doc + com))
    for fpct, p, code, prose in sorted(over, reverse=True)[: args.top]:
        print(f"OVER {fpct:5.1f}%  {p}  code={code} prose={prose}")
    if over:
        failed = True
    if args.max_file is None:
        ranked = sorted(rows, key=lambda r: -(r[2] + r[3]) / max(r[4], 1))
        for p, code, doc, com, n in ranked[: args.top]:
            print(f"{100.0 * (doc + com) / max(n, 1):5.1f}%  {p}  lines={n} code={code} doc={doc} comment={com}")
    print(f"TOTAL lines={total} code={tcode} prose={tprose} prose%={pct:.1f}")
    if args.max_total is not None and pct > args.max_total:
        print(f"FAIL prose {pct:.1f}% > {args.max_total}%")
        failed = True
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
