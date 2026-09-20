"""Smoke test for the composition -> prompts -> projects layer.

Verifies the whole loop:
  1. compose --project --prompt       生成进 projects/<name>/out/
  2. project.json                     记录提示词 / 参数 / 产物
  3. compose --project (再次)          从档案恢复参数（复现）
  4. registry collect()               能把项目暴露给 AI 发现
  5. qa_check                         产物结构合法

**为什么用 math-cube**：它 ``structure.json`` 里 ``packs: []`` —— 不依赖任何资产包。
``packs/`` 是可选内容（仓库只带引擎 + 组合定义），所以拿一个「零依赖」的组合来测
这一层，纯代码 checkout 上也能跑通。需要资产包的组合（modern-skyscraper 等）由
``tests/studio_smoke.py`` 那类会自建临时 packs 的用例覆盖。

Usage:  python tests/projects_smoke.py
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "packages"))
sys.path.insert(0, str(ROOT / "tests"))
import _support  # noqa: E402

CID = "math-cube"
NAME = "_smoke_项目测试"
#: skeleton 提示词：96³、不装展品 —— 这个用例验的是项目层，不是几何，跑快一点。
PROMPT = "skeleton"


def run(*args: str) -> str:
    # 子进程要看到本仓库的 packages/（机器上可能还装过别的 checkout）
    import os
    from mccore.paths import child_env

    r = subprocess.run([sys.executable, *args], cwd=str(ROOT),
                       capture_output=True, text=True,
                       encoding="utf-8", errors="replace",
                       env=child_env(os.environ))
    if r.returncode != 0:
        print((r.stdout or "")[-2000:])
        print((r.stderr or "")[-2000:])
        raise SystemExit(f"FAIL: {' '.join(args)} (rc={r.returncode})")
    return (r.stdout or "") + (r.stderr or "")


def main() -> int:
    # 组合是可选内容：没有就跳过（退出码 2），不是失败
    _support.need_compositions(CID, script="projects_smoke.py")
    proj = ROOT / "compositions" / CID / "projects" / NAME
    if proj.exists():
        shutil.rmtree(proj)
    try:
        # 1) 生成：提示词 + 覆盖参数 -> 项目目录
        run("-m", "mccore.compose", CID, "--project", NAME,
            "--prompt", PROMPT, "--set", "edge=96",
            "--set", f"name={NAME}")
        lt = proj / "out" / f"{NAME}.schem"
        assert lt.is_file(), f"产物没落进项目目录: {lt}"

        # 2) 档案内容
        man = json.loads((proj / "project.json").read_text(encoding="utf-8"))
        assert man["schema"] == "mccore/project@1"
        assert man["prompt"] == PROMPT, man["prompt"]
        assert man["params"]["edge"] == 96, man["params"]
        # 提示词 frontmatter 里的参数要真的参与生成
        assert man["params"]["exhibits"] == 0, man["params"]
        assert any(a.startswith("out/") for a in man["artifacts"]), man["artifacts"]
        assert man["runs"] and man["runs"][-1]["ok"], man["runs"]

        # 3) 复现：不带参数也应从档案恢复（--dry-run 看命令）
        out = run("-m", "mccore.compose", CID, "--project", NAME, "--dry-run")
        assert "--edge 96" in out, out

        # 4) registry 暴露项目
        from mccore.registry import collect
        comps = {c["id"]: c for c in collect()["compositions"]}
        names = [p["name"] for p in comps[CID].get("projects", [])]
        assert NAME in names, f"registry 未收录项目: {names}"

        # 5) 结构质检
        run("-m", "mcqa.qa_check", str(lt))
        print("PASS: projects layer "
              "(create -> record -> replay -> registry -> qa_check)")
        return 0
    finally:
        if proj.exists():
            shutil.rmtree(proj)
        # projects/ 空了就把容器目录也收掉，别留痕
        root = ROOT / "compositions" / CID / "projects"
        if root.is_dir() and not any(root.iterdir()):
            root.rmdir()


if __name__ == "__main__":
    raise SystemExit(main())
