"""行尾冒烟测试：仓库文本文件必须全是 LF，且引擎的写入点也只写 LF。

  1. `.gitattributes` / `.editorconfig` 都声明 LF
  2. git 跟踪 + 未跟踪未忽略的文本文件里没有 CRLF（工作区字节 == git blob 字节，
     这是 pack.json / mckb manifest 那些 sha256 能对上的前提）
  3. mccore.paths.write_text_lf 写出来的是 LF（mckb 再导出的也是同一个函数）
  4. 文本写入点不直接用 Path.write_text（会随平台写出 CRLF）

Usage:  python tests/lf_smoke.py
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "packages"))
sys.path.insert(0, str(ROOT / "tools"))

from mccore.paths import repo_root, write_text_lf  # noqa: E402

#: 允许直接 write_text 的例外（不进 git 的缓存 / 非文本 / 注释里提到）
WRITE_TEXT_ALLOW = {
    "tools/lf_check.py",        # 它自己就是行尾守卫（按字节读写）
}


def crlf_files() -> list[tuple[str, int]]:
    from lf_check import scan  # noqa: PLC0415
    return scan(repo_root())


def direct_write_text() -> list[str]:
    """找出「文本模式 write_text 且没带 newline」的写入点（Windows 上会写 CRLF）。

    行尾标了 ``# lf-ok`` 的算已知例外（只写 .cache 之类的非 git 产物）。
    """
    import ast  # noqa: PLC0415

    def walk(node, fn: str, src_lines: list[str], out: list[str]) -> None:
        if isinstance(node, ast.Call):
            f = node.func
            name = (f.attr if isinstance(f, ast.Attribute)
                    else (f.id if isinstance(f, ast.Name) else ""))
            if name == "write_text":
                kws = [ast.unparse(k) for k in node.keywords]
                args = " ".join([ast.unparse(a) for a in node.args] + kws)
                if "newline=" not in args:
                    span = src_lines[node.lineno - 1:node.end_lineno]
                    if any("lf-ok" in ln for ln in span):
                        return
                    out.append(f"{fn}:{node.lineno}  {ast.unparse(node)}")
        for ch in ast.iter_child_nodes(node):
            walk(ch, fn, src_lines, out)

    hits: list[str] = []
    for p in sorted((repo_root() / "packages").rglob("*.py")):
        if "__pycache__" in str(p):
            continue
        rel = p.relative_to(repo_root()).as_posix()
        if rel in WRITE_TEXT_ALLOW:
            continue
        src = p.read_text(encoding="utf-8", errors="replace")
        walk(ast.parse(src), rel, src.splitlines(), hits)
    return hits


def main() -> int:
    # 1) 配置层
    ga = (repo_root() / ".gitattributes").read_text(encoding="utf-8")
    assert "* text=auto eol=lf" in ga, ".gitattributes 丢了 eol=lf"
    ec = (repo_root() / ".editorconfig").read_text(encoding="utf-8")
    assert "end_of_line = lf" in ec, ".editorconfig 没声明 LF"
    print("PASS 1/4  .gitattributes + .editorconfig 都锁 LF")

    # 2) 工作区
    hits = crlf_files()
    assert not hits, f"有 {len(hits)} 个 CRLF 文本文件：{[h[0] for h in hits[:5]]}"
    print("PASS 2/4  工作区文本文件全 LF（sha256 类校验的前提）")

    # 3) 写入点只写 LF
    tmp = Path(tempfile.mkdtemp(prefix="structworkshop_lf_"))
    try:
        p = tmp / "a.md"
        write_text_lf(p, "行一\n行二\n")
        assert p.read_bytes() == "行一\n行二\n".encode(), "write_text_lf 写成了 CRLF"
        from mckb.extract import write_text_lf as kb_lf
        assert kb_lf is write_text_lf, "mckb 应当再导出 mccore 的规范实现"
        q = tmp / "b.json"
        kb_lf(q, '{"a":1}\n')
        assert q.read_bytes() == b'{"a":1}\n'
        print("PASS 3/4  write_text_lf（mccore / mckb 同一实现）只写 LF")
    finally:
        import shutil  # noqa: PLC0415
        shutil.rmtree(tmp, ignore_errors=True)

    # 4) 没有绕过 helper 的文本写入点
    bad = direct_write_text()
    assert not bad, "这些写入点没锁行尾（改用 mccore.paths.write_text_lf）:\n  " + "\n  ".join(bad)
    print("PASS 4/4  引擎里没有裸 .write_text(…)(无 newline=) 的文本写入点")

    print(f"\n仓库根 {repo_root()}  文本文件 sha256 稳定：✓")
    print("ALL PASS: 行尾层（LF 不变式）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
