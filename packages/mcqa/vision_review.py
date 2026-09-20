"""直接调 OpenAI 兼容的视觉模型做评审（一组 PNG → 一段意见），不经过任何编排层。

Why this exists
---------------
Going through an agent/tool layer to show images to a model is slow and
flaky: every hop can add minutes of overhead or drop the image before it is
ever read. The API itself is simple (`input: ["text","image"]`,
OpenAI-compatible), so this calls it directly with base64 image blocks --
fast, deterministic, no orchestration in between.

Credentials come from the environment, never from a hardcoded path:
see `api_key()` below.

Usage:
  python -m mcqa.vision_review img1.png [img2.png ...] --prompt "..." \
      [--max-tokens 1200] [--model deepseek-v4-flash-vision-exp]
"""
from __future__ import annotations

import base64
import json
import os
import sys
import urllib.request
from pathlib import Path

#: 默认端点：任何 OpenAI 兼容的 `/chat/completions` 都能换。
DEFAULT_API = "https://api.deepseek.com/chat/completions"
DEFAULT_MODEL = "deepseek-v4-flash-vision-exp"

#: 直接给 key（CI / 容器里最省事）。
ENV_KEY = ("STRUCTWORKSHOP_VISION_API_KEY", "DEEPSEEK_API_KEY")
#: 给一个 JSON 文件路径，里面是 ``{"<profile>": {"key": "..."}}``。
ENV_AUTH_FILE = "STRUCTWORKSHOP_AUTH_FILE"
#: 给端点（换供应商 / 自建反代）。
ENV_API = "STRUCTWORKSHOP_VISION_API"
ENV_MODEL = "STRUCTWORKSHOP_VISION_MODEL"

#: 兜底查找的 JSON 凭据文件（**相对家目录**，不绑定任何一台机器）。
AUTH_FILE_CANDIDATES = (
    Path("~/.config/structworkshop/auth.json"),
    Path("~/.structworkshop/auth.json"),
)
#: 候选 profile 名（按顺序取第一个带 ``key`` 的）。
AUTH_PROFILES = ("deepseek", "vision", "default")


class VisionConfigError(RuntimeError):
    """没配好凭据/端点时抛出：消息里直接写该做什么。"""


def _first_env(*names: str) -> str | None:
    for n in names:
        v = os.environ.get(n)
        if v is not None and v.strip():
            return v.strip()
    return None


def _key_from_file(path: Path) -> str | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    for profile in AUTH_PROFILES:
        block = data.get(profile)
        if isinstance(block, dict) and block.get("key"):
            return str(block["key"]).strip()
    # 也接受最朴素的 {"key": "..."} / {"api_key": "..."}
    for flat in ("key", "api_key", "apiKey"):
        if isinstance(data.get(flat), str) and data[flat].strip():
            return data[flat].strip()
    return None


def api_key() -> str:
    """视觉模型的 API key。

    解析顺序（先命中先赢，任何一步都不依赖具体某台机器）:

      1. ``STRUCTWORKSHOP_VISION_API_KEY`` / ``DEEPSEEK_API_KEY`` 环境变量
      2. ``STRUCTWORKSHOP_AUTH_FILE`` 指向的 JSON 文件
      3. ``~/.config/structworkshop/auth.json`` 或 ``~/.structworkshop/auth.json``

    都没配就报错，并把该设哪个变量写清楚 —— 而不是去读一个写死的绝对路径。
    """
    env_key = _first_env(*ENV_KEY)
    if env_key:
        return env_key
    explicit = _first_env(ENV_AUTH_FILE)
    paths = ([Path(explicit).expanduser()] if explicit
             else [p.expanduser() for p in AUTH_FILE_CANDIDATES])
    for p in paths:
        key = _key_from_file(p)
        if key:
            return key
    tried = ", ".join(str(p) for p in paths)
    raise VisionConfigError(
        "没有找到视觉模型 API key。三选一：\n"
        f"  1) 设环境变量 {ENV_KEY[0]}（或 {ENV_KEY[1]}）\n"
        f"  2) 设 {ENV_AUTH_FILE}=<你的 auth.json 路径>\n"
        f"  3) 在任一路径放一份 JSON：{tried}\n"
        '    内容形如 {"deepseek": {"key": "sk-..."}}\n'
        f"端点用 {ENV_API} 覆盖（默认 {DEFAULT_API}）。")


def api_url() -> str:
    """OpenAI 兼容的 chat/completions 端点，可用环境变量覆盖。"""
    return _first_env(ENV_API) or DEFAULT_API


def default_model() -> str:
    return _first_env(ENV_MODEL) or DEFAULT_MODEL


def img_block(path: str) -> dict:
    with open(path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode()
    return {"type": "image_url",
            "image_url": {"url": "data:image/png;base64," + b64}}


def review(paths: list[str], prompt: str, model: str | None = None,
           max_tokens: int = 1600, timeout: int = 300,
           reasoning_effort: str | None = "none") -> tuple[str, dict]:
    model = model or default_model()
    content = [{"type": "text", "text": prompt}]
    content += [img_block(p) for p in paths]
    body = {
        "model": model,
        "messages": [{"role": "user", "content": content}],
        "max_tokens": max_tokens,
        "temperature": 0.2,
        "stream": False,
    }
    if reasoning_effort:
        body["reasoning_effort"] = reasoning_effort
    req = urllib.request.Request(
        api_url(), data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json",
                 "Authorization": "Bearer " + api_key()},
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        d = json.loads(r.read().decode())
    if "choices" not in d:
        raise RuntimeError("API error: %s" % json.dumps(d, ensure_ascii=False)[:600])
    msg = d["choices"][0]["message"]
    txt = msg.get("content") or ""
    if not txt and msg.get("reasoning_content"):
        txt = msg["reasoning_content"]
    return txt, d.get("usage", {})


def main() -> None:
    args = sys.argv[1:]
    prompt = None
    if "--prompt" in args:
        i = args.index("--prompt")
        prompt = args[i + 1]
        del args[i:i + 2]
    model = default_model()
    if "--model" in args:
        i = args.index("--model")
        model = args[i + 1]
        del args[i:i + 2]
    mt = 1600
    if "--max-tokens" in args:
        i = args.index("--max-tokens")
        mt = int(args[i + 1])
        del args[i:i + 2]
    re_eff = "none"
    if "--reasoning-effort" in args:
        i = args.index("--reasoning-effort")
        re_eff = args[i + 1]
        del args[i:i + 2]
    paths = [a for a in args if not a.startswith("--")]
    if not paths:
        print(__doc__)
        sys.exit(1)
    if prompt is None:
        prompt = ("Review these orthographic renders of a Minecraft skyscraper. "
                  "Describe what you actually see in each, then list the top "
                  "problems and a score out of 10.")
    txt, usage = review(paths, prompt, model=model, max_tokens=mt,
                        reasoning_effort=re_eff)
    print(txt)
    print("\n[usage] %s" % json.dumps(usage, ensure_ascii=False))


if __name__ == "__main__":
    main()
