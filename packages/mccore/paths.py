"""Canonical filesystem locations for the structworkshop repo.

Everything resolves against the repo root (the directory holding
``pyproject.toml`` + ``packages/``), found by walking up from this file or the
current working directory, or overridden with ``STRUCTWORKSHOP_ROOT``
（改名前的 ``MCFORGE_ROOT`` 仍然认，只是新名优先）。
"""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

#: 改名（mcforge → structworkshop）前的环境变量前缀：只作兜底，新名优先。
LEGACY_ENV_PREFIX = "MCFORGE_"


def env_first(*names: str) -> str | None:
    """按顺序取第一个非空环境变量（新名在前、旧名兜底）；都没有返回 None。"""
    for name in names:
        val = os.environ.get(name)
        if val is not None and val.strip() != "":
            return val
    return None


@lru_cache(maxsize=1)
def repo_root() -> Path:
    env = env_first("STRUCTWORKSHOP_ROOT", LEGACY_ENV_PREFIX + "ROOT")
    if env:
        return Path(env).expanduser().resolve()
    for start in (Path(__file__).resolve(), Path.cwd().resolve()):
        for p in (start, *start.parents):
            if (p / "pyproject.toml").is_file() and (p / "packages").is_dir():
                return p
    raise RuntimeError(
        "找不到 structworkshop 仓库根目录(含 pyproject.toml + packages/);"
        "可用环境变量 STRUCTWORKSHOP_ROOT 指定")


@lru_cache(maxsize=1)
def packs_dir() -> Path:
    """``<repo>/packs`` —— 资产包根目录。

    这是**本地内容**：仓库只带引擎与组合定义，资产包（模块 + 预览图）不在里面。
    所以这个目录在全新 checkout 上可能**不存在** —— 读路径请用 :func:`pack_dirs`
    （它已经判过 ``is_dir()``），写路径请先调 :func:`ensure_packs_dir`。
    """
    return repo_root() / "packs"


def ensure_packs_dir() -> Path:
    """确保 ``packs/`` 存在（**只在写路径上调**）。

    不放进 :func:`packs_dir` 里是因为那会让「列目录」这种只读操作产生副作用：
    全新 checkout 上什么都不干就会多出一个空 ``packs/``，还会被提交进 git。
    """
    d = packs_dir()
    d.mkdir(parents=True, exist_ok=True)
    return d


def has_packs() -> bool:
    """仓库里有任何资产包吗（用于把「0 个包」和「目录都没有」区分开）。"""
    return bool(pack_dirs())


@lru_cache(maxsize=1)
def compositions_dir() -> Path:
    """``<repo>/compositions`` —— 组合（一类建筑）的根目录。

    与 ``packs/`` 同口径：这是**可选内容**（你可以把一整套 compositions 拷进来/
    拿出去），所以目录本身可能**不存在**。读路径一律用 :func:`composition_dirs`
    （已判 ``is_dir()``），写路径才调 :func:`ensure_compositions_dir`。
    """
    return repo_root() / "compositions"


def composition_dirs() -> list[Path]:
    """所有组合目录（有 ``structure.json`` 的），目录不存在/为空 -> ``[]``。

    这是**唯一该用来列组合的入口** —— 直接写 ``compositions_dir().iterdir()``
    会在「没有 compositions」时抛 FileNotFoundError（引擎就变成依赖它了）。
    """
    d = compositions_dir()
    if not d.is_dir():
        return []
    return sorted(p for p in d.iterdir()
                  if p.is_dir() and (p / "structure.json").is_file())


def ensure_compositions_dir() -> Path:
    """确保 ``compositions/`` 存在（**只在写路径上调**，同 :func:`ensure_packs_dir`）。"""
    d = compositions_dir()
    d.mkdir(parents=True, exist_ok=True)
    return d


def has_compositions() -> bool:
    """仓库里有没有组合（用于把「一个都没有」和「目录都没有」区分开）。"""
    return bool(composition_dirs())


@lru_cache(maxsize=1)
def cache_dir() -> Path:
    d = repo_root() / ".cache" / "mcassets"
    d.mkdir(parents=True, exist_ok=True)
    return d


def pack_modules(pack: str) -> Path:
    """<repo>/packs/<pack>/modules  (created on demand)."""
    return packs_dir() / pack / "modules"


def pack_dirs() -> list[Path]:
    """All packs that declare themselves with pack.json (空/不存在 -> [])。"""
    d = packs_dir()
    if not d.is_dir():
        return []
    return sorted(p for p in d.iterdir()
                  if p.is_dir() and (p / "pack.json").is_file())


def pack_module_dirs() -> list[Path]:
    return [p / "modules" for p in pack_dirs() if (p / "modules").is_dir()]


def child_env(env: dict | None = None) -> dict:
    """构造传给子进程的环境：保证子进程 import 到的引擎就是**本仓库这一份**。

    为什么必须有：``python -m mccore.compose`` 会 fork 出
    ``compositions/<id>/build.py``，而子进程**只继承环境变量、不继承 sys.path**。
    如果这台机器上还装过**另一个 checkout** 的 ``pip install -e .``（很常见：
    老工作目录 + 新 clone），子进程会 import 到那一份 —— ``repo_root()`` 指向
    别的目录，产物就写进别人的仓库里了（实测踩过：新 clone 里跑 compose，
    .schem 落到了旧仓库）。把本仓库的 ``packages/`` 放在 ``PYTHONPATH`` 最前面，
    子进程就只认这里。

    用法::

        subprocess.run(argv, cwd=str(repo_root()), env=child_env())

    ``env`` 传 None 时基于 ``os.environ``；已有的 ``PYTHONPATH`` 条目会被保留
    （只是被挤到后面），所以外部追加的路径不会丢。
    """
    e = dict(os.environ if env is None else env)
    pkgs = str(repo_root() / "packages")
    old = [p for p in e.get("PYTHONPATH", "").split(os.pathsep) if p]
    e["PYTHONPATH"] = os.pathsep.join(
        [pkgs] + [p for p in old
                  if os.path.normcase(os.path.normpath(p))
                  != os.path.normcase(os.path.normpath(pkgs))])
    # 子进程要往管道里打中文：不指定就随终端代码页（Windows 上是 GBK）
    e.setdefault("PYTHONIOENCODING", "utf-8")
    return e


def write_text_lf(path, text: str) -> None:
    """以 **LF** 行尾写文本（跨检出稳定的唯一写法）。

    仓库用 ``.gitattributes`` 把文本统一成 LF，而 Python 的文本模式在 Windows 上
    默认把 ``\n`` 翻成 CRLF —— 于是「本机写的文件」与「clone 出来的文件」
    字节不同，凡是按字节算 sha256 的地方（``pack.json`` 的校验和、``mckb`` 的 manifest）
    都会在新 clone 上全红。这里统一按 LF 写，一处解决。

    （``mckb.extract`` 里有同名的实现，先有了它才修好语料库那个坑；这里补上引擎侧的。）
    """
    Path(path).write_text(text, encoding="utf-8", newline="\n")
