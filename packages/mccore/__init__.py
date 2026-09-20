"""structworkshop core: .schem/.schem IO, module packs, assembly, staged builds, params/registry."""

from .paths import configure_stdio as _configure_stdio

# import 即生效：把本进程的 stdout/stderr 切成 UTF-8（见 paths.configure_stdio）。
# 所有入口（`python -m mccore.*` / `tools/` / `tests/`）都会 import 到这里，一处就够 ——
# 中文 / emoji 往管道里打时，Windows 的 cp1252（GitHub runner 的默认）会直接
# UnicodeEncodeError 把命令打成退出码 1。
_configure_stdio()
