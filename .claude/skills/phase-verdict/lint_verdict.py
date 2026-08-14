#!/usr/bin/env python3
"""lint_verdict.py — phase-verdict skill 的机器验收门。

用法:
  python3 .claude/skills/phase-verdict/lint_verdict.py \
      docs/superpowers/notes/2026-08-14-phase173-preamble-boost-refuted.md \
      --env IEEE80211_PREAMBLE_BOOST --phase 173

检查项（FAIL = 归档链违规，exit 1；WARN = 仅提示）:
  1. verdict 文件名 slug 以判定词结尾（-confirmed/-not-confirmed/-refuted；旧命名 WARN）
  2. 文件内含逐字 "VERDICT:" 行（禁止转述）
  3. 文件声明分子分母窗口（含「窗口」或「分子分母」）
  4. .claude/rules/env-vars.md 含该 env var
  5. REFUTED 时：CLAUDE.md 禁止方向表含该方向 + hookify warn-refuted-direction
     pattern 含该 env var（缺任一 = FAIL，护栏漏洞）
  6. memory 主题文件存在且含 metadata: type: project；MEMORY.md 索引行 <200 字符
"""
import argparse
import glob
import os
import re
import sys

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
MEMDIR = os.path.expanduser("~/.claude/projects/-home-hy-gr-ieee802-11/memory")

fails, warns = [], []


def check(ok, msg, warn_only=False):
    (warns if warn_only else fails).append(msg) if not ok else None
    print(("WARN " if (not ok and warn_only) else "FAIL " if not ok else "ok   ") + msg)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("verdict_file")
    ap.add_argument("--env", required=True, help="e.g. IEEE80211_PREAMBLE_BOOST")
    ap.add_argument("--phase", required=True, type=int)
    a = ap.parse_args()

    vf = a.verdict_file
    check(os.path.isfile(vf), f"verdict 文件存在: {vf}")
    if not os.path.isfile(vf):
        sys.exit(1)
    text = open(vf, encoding="utf-8").read()
    base = os.path.basename(vf)

    m = re.match(r"\d{4}-\d{2}-\d{2}-phase(\d+[a-z]?)-(.+)\.md$", base)
    check(bool(m), "文件名匹配 YYYY-MM-DD-phase<N>-<slug>.md")
    if m:
        slug = m.group(2)
        check(slug.endswith(("-confirmed", "-not-confirmed", "-refuted")),
              f"slug 以判定词结尾（当前: ...-{slug}）", warn_only=True)

    verdict_word = None
    for w in ("NOT CONFIRMED", "REFUTED", "CONFIRMED"):  # 顺序敏感：NOT CONFIRMED 含 CONFIRMED
        if re.search(rf"\b{w}\b", text):
            verdict_word = w
            break
    check(verdict_word is not None, "正文含判定词（CONFIRMED / NOT CONFIRMED / REFUTED）")
    if m and verdict_word:
        slug = m.group(2)
        slug_word = ("NOT CONFIRMED" if slug.endswith("-not-confirmed")
                     else "REFUTED" if slug.endswith("-refuted")
                     else "CONFIRMED" if slug.endswith("-confirmed") else None)
        check(slug_word is None or slug_word == verdict_word,
              f"文件名判定词与正文一致（slug={slug_word} vs 正文={verdict_word}）",
              warn_only=True)
    check(bool(re.search(r"^.*VERDICT:.*$", text, re.M)),
          "含逐字 VERDICT: 行（禁止转述）")
    check(("分子分母" in text) or ("窗口" in text),
          "声明分子分母各自统计窗口")

    envvars = open(os.path.join(REPO, ".claude/rules/env-vars.md"), encoding="utf-8").read()
    check(a.env in envvars, f"env-vars.md 含 {a.env}")

    if verdict_word == "REFUTED":
        claude_md = open(os.path.join(REPO, "CLAUDE.md"), encoding="utf-8").read()
        check(a.env in claude_md or f"P{a.phase}" in claude_md,
              f"CLAUDE.md 禁止方向表含 P{a.phase}/{a.env}")
        hookify = os.path.join(REPO, ".claude/hookify.warn-refuted-direction.local.md")
        suffix = a.env.replace("IEEE80211_", "")
        check(os.path.isfile(hookify) and suffix in open(hookify, encoding="utf-8").read(),
              f"hookify warn-refuted-direction pattern 含 {suffix}（护栏同步）")

    mem = glob.glob(os.path.join(MEMDIR, f"project_p{a.phase}_*.md"))
    check(bool(mem), f"memory 主题文件 project_p{a.phase}_*.md 存在")
    if mem:
        check("metadata:" in open(mem[0], encoding="utf-8").read()
              and "type: project" in open(mem[0], encoding="utf-8").read(),
              "memory frontmatter 含 metadata: type: project")
    idx = os.path.join(MEMDIR, "MEMORY.md")
    if os.path.isfile(idx):
        lines = [l for l in open(idx, encoding="utf-8") if f"P{a.phase}" in l or f"p{a.phase}" in l]
        check(bool(lines), f"MEMORY.md 含 P{a.phase} 索引行")
        for l in lines:
            check(len(l.strip()) <= 200, f"索引行 <200 字符（当前 {len(l.strip())}）", warn_only=True)

    print(f"\n{'PASS' if not fails else 'FAIL'}: {len(fails)} fail(s), {len(warns)} warn(s)")
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
