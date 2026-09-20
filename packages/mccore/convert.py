"""结构格式转换 / 存量迁移。

    python -m mccore.convert packs/modern-arch/modules        # .litematic -> .schem(原地)
    python -m mccore.convert builds --recursive --remove-source
    python -m mccore.convert x.schem --to litematic            # 反向导出
    python -m mccore.convert a.litematic --out /tmp/out        # 指定输出目录/文件

默认:目标格式 .schem,保留源文件,转换后做往返逐格校验(失败则删除产物)。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from mccore import structure_io as S


def gather(paths: list[str]) -> list[Path]:
    out: list[Path] = []
    for raw in paths:
        p = Path(raw).expanduser()
        if not p.exists():
            raise SystemExit(f"没有路径: {p}")
        if p.is_dir():
            out.extend(S.supported(p))
        elif S.is_structure(p):
            out.append(p)
        else:
            raise SystemExit(f"不是结构文件: {p}(支持 .schem/.litematic)")
    return sorted(set(out))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="结构格式转换(.litematic <-> .schem),默认转到 .schem")
    ap.add_argument("paths", nargs="+", help="文件或目录(目录递归)")
    ap.add_argument("--to", default="schem", choices=("schem", "litematic"),
                    help="目标格式(默认 schem)")
    ap.add_argument("--out", default=None, help="输出目录或文件名(默认原地同名)")
    ap.add_argument("--remove-source", action="store_true",
                    help="成功后删除源文件")
    ap.add_argument("--no-overwrite", action="store_true",
                    help="目标已存在时跳过(默认覆盖)")
    ap.add_argument("--no-verify", action="store_true",
                    help="跳过往返逐格校验")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--quiet", action="store_true")
    a = ap.parse_args(argv)

    files = gather(a.paths)
    if not files:
        raise SystemExit("没有可转换的结构文件")
    target = "." + a.to
    out_root = Path(a.out).expanduser() if a.out else None
    if out_root is not None and len(files) > 1 and out_root.suffix:
        raise SystemExit("--out 是文件名时只能转换单个文件")

    done, skipped, failed = [], [], []
    for src in files:
        if src.suffix.lower() == target and out_root is None:
            skipped.append((src, "已是目标格式"))
            continue
        dst = None
        if out_root is not None:
            dst = out_root / src.with_suffix(target).name if out_root.is_dir() \
                or a.out.endswith(("/", "\\")) else out_root
        if a.dry_run:
            done.append((src, dst or src.with_suffix(target)))
            continue
        try:
            dst, stats = S.convert(src, dst, to=target,
                                   overwrite=not a.no_overwrite,
                                   remove_source=a.remove_source,
                                   verify=not a.no_verify)
        except FileExistsError as e:
            skipped.append((src, str(e)))
            continue
        except Exception as e:  # noqa: BLE001
            failed.append((src, str(e)))
            continue
        done.append((src, dst))
        if not a.quiet:
            extra = " (校验 OK)" if stats.get("verified") else ""
            print(f"  {src} -> {dst}{extra}")
    print(f"\n转换 {len(done)} / 跳过 {len(skipped)} / 失败 {len(failed)}")
    for src, why in skipped:
        print(f"  skip: {src} ({why})")
    for src, why in failed:
        print(f"  FAIL: {src}: {why}", file=sys.stderr)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
