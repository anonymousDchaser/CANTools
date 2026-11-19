# -*- coding: utf-8 -*-
"""推送前检查：扫描待推送内容里有没有被 DLP 透明加密（密文）的文件。

为什么需要这个脚本（2026-11-19 实测结论）：
    - 密文**不一定**带 ``%TSD-Header-###%`` 头，靠搜头会漏；唯一可靠判据是「UTF-8 解码失败」。
    - 密文文件被 ``git add`` 会**原样入库**，而 ``git status`` 仍报 clean
      （工作区密文 == 仓库密文），所以 clean 不能证明内容健康。
    - 实际踩过：``tools/ci_gui_probe.py`` 以 4208 字节密文形态躺在 4 个提交里，
      CI 一直成功、打包也正常，因为没人读它。

用法::

    python tools/check_encrypted_blobs.py             # 检查 HEAD（推送前）
    python tools/check_encrypted_blobs.py --rev v1.5.0
    python tools/check_encrypted_blobs.py --worktree  # 检查工作区（手动解密后复核）

退出码：0 = 全部明文；1 = 发现密文或读取失败。
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT = os.path.dirname(HERE)

# 文本类扩展名：这些文件必须是 UTF-8 明文，解码失败即视为密文
TEXT_EXT = {
    ".py", ".pyw", ".md", ".txt", ".json", ".yml", ".yaml", ".toml", ".ini",
    ".cfg", ".iss", ".spec", ".csv", ".tsv", ".asc", ".dbc", ".bat", ".cmd",
    ".ps1", ".sh", ".html", ".htm", ".xml", ".qss", ".js", ".css", ".svg",
    ".gitignore", ".gitattributes", ".editorconfig", "",
}

_GIT_CANDIDATES = [
    r"C:/Program Files/Git/cmd/git.EXE",
    r"C:/Program Files (x86)/Git/cmd/git.EXE",
    "git",
]


def find_git() -> str:
    """找一个可用的 git 可执行文件（bash 的 PATH 常被 shim 破坏，优先绝对路径）。"""
    for cand in _GIT_CANDIDATES:
        if os.path.sep in cand or cand.endswith(".EXE") or cand.endswith(".exe"):
            if os.path.isfile(cand):
                return cand
        else:
            from shutil import which
            if which(cand):
                return cand
    print("找不到 git 可执行文件，请检查安装路径", file=sys.stderr)
    sys.exit(2)


def git(args, git_exe: str, cwd: str, binary: bool = False):
    r = subprocess.run([git_exe, "-C", cwd] + args, capture_output=True)
    if r.returncode != 0:
        msg = r.stderr.decode("utf-8", "replace").strip()
        print(f"git {' '.join(args)} 失败：{msg}", file=sys.stderr)
        sys.exit(2)
    return r.stdout if binary else r.stdout.decode("utf-8", "replace")


def is_text_path(path: str) -> bool:
    return os.path.splitext(path)[1].lower() in TEXT_EXT


def scan_rev(rev: str, git_exe: str, toplevel: str):
    raw = git(["ls-tree", "-r", "-z", "--full-tree", rev], git_exe, toplevel, binary=True)
    entries = []
    for rec in raw.split(b"\0"):
        if not rec:
            continue
        meta, path = rec.split(b"\t", 1)
        parts = meta.split()
        if parts[1] == b"blob":
            entries.append((parts[2].decode(), path.decode("utf-8", "replace")))

    batch = subprocess.run(
        [git_exe, "-C", toplevel, "cat-file", "--batch"],
        input="".join(e[0] + "\n" for e in entries).encode(),
        capture_output=True,
    ).stdout

    pos = 0
    checked = 0
    bad = []
    for sha, path in entries:
        nl = batch.index(b"\n", pos)
        header = batch[pos:nl].decode("utf-8", "replace")
        pos = nl + 1
        parts = header.split()
        if len(parts) < 3:
            continue
        size = int(parts[2])
        blob = batch[pos:pos + size]
        pos += size + 1
        if not is_text_path(path):
            continue
        checked += 1
        try:
            blob.decode("utf-8")
        except UnicodeDecodeError as exc:
            bad.append((path, size, blob[:12].hex(), str(exc)[:70]))
    return checked, bad


def scan_worktree(toplevel: str):
    skip = {".git", "build", "dist", "__pycache__", ".workbuddy", ".pytest_cache",
            "node_modules", ".release", ".venv", "venv"}
    checked = 0
    bad = []
    for root, dirs, files in os.walk(toplevel):
        dirs[:] = [d for d in dirs if d not in skip]
        for name in files:
            if not is_text_path(name):
                continue
            full = os.path.join(root, name)
            checked += 1
            try:
                with open(full, "rb") as fh:
                    data = fh.read()
            except OSError as exc:
                bad.append((os.path.relpath(full, toplevel), -1, "", f"读取失败 {exc}"))
                continue
            try:
                data.decode("utf-8")
            except UnicodeDecodeError as exc:
                bad.append((os.path.relpath(full, toplevel), len(data), data[:12].hex(),
                            str(exc)[:70]))
    return checked, bad


def main() -> int:
    ap = argparse.ArgumentParser(description="检查有没有被 DLP 加密（密文）的文件")
    ap.add_argument("--rev", default="HEAD", help="要检查的提交/标签，默认 HEAD")
    ap.add_argument("--worktree", action="store_true", help="检查工作区文件而不是提交")
    args = ap.parse_args()

    git_exe = find_git()
    toplevel = git(["rev-parse", "--show-toplevel"], git_exe, PROJECT).strip()

    if args.worktree:
        checked, bad = scan_worktree(toplevel)
        scope = "工作区"
    else:
        checked, bad = scan_rev(args.rev, git_exe, toplevel)
        scope = f"提交 {args.rev}"

    print(f"检查范围：{scope}（仓库根 {toplevel}）")
    print(f"文本文件已校验：{checked}")
    if not bad:
        print("结果：全部为 UTF-8 明文，可以推送。")
        return 0

    print(f"结果：发现 {len(bad)} 个密文/异常文件，**不要推送**：")
    for path, size, head, why in bad:
        abs_path = os.path.join(toplevel, path.replace("/", os.sep))
        print(f"  [密文] {abs_path}")
        print(f"         size={size} head={head}")
        print(f"         {why}")
    print()
    print("处置方式（二选一）：")
    print("  1) 能自动解密 → 解密 → 用 --worktree 复核 → 重新提交并推送；")
    print("  2) 不能自动解密 → 把上面的绝对路径清单交给用户，"
          "由用户右键文件 →「文件加密系统」→「解密文件」，解密后通知再推。")
    return 1


if __name__ == "__main__":
    sys.exit(main())
