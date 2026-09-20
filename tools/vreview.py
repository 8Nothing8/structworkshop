"""视觉评审：直接调视觉模型看图（渲染图 / 参考图），不经过任何 agent 编排层。

用法:
  python tools/vreview.py --prompt-file p.txt a.png b.png [--max-tokens 1600]
  python tools/vreview.py --prompt "..." a.png

凭据（**代码里不写任何 key / 绝对路径**，三选一）:
  STRUCTWORKSHOP_VISION_API_KEY=sk-...        # 或 DEEPSEEK_API_KEY
  STRUCTWORKSHOP_AUTH_FILE=~/my/auth.json     # JSON: {"deepseek": {"key": "sk-..."}}
  ~/.config/structworkshop/auth.json          # 默认位置
端点/模型也可换（任何 OpenAI 兼容接口）:
  STRUCTWORKSHOP_VISION_API  /  STRUCTWORKSHOP_VISION_MODEL
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "packages"))
from mcqa.vision_review import review  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("images", nargs="+")
    ap.add_argument("--prompt")
    ap.add_argument("--prompt-file")
    ap.add_argument("--max-tokens", type=int, default=1600)
    ap.add_argument("--model", default=None)
    a = ap.parse_args()
    prompt = a.prompt
    if a.prompt_file:
        prompt = Path(a.prompt_file).read_text(encoding="utf-8")
    if not prompt:
        prompt = ("Review these Minecraft renders. Describe what you see, then "
                  "list the top problems and a score out of 10.")
    kw = {"max_tokens": a.max_tokens}
    if a.model:
        kw["model"] = a.model
    txt, usage = review(a.images, prompt, **kw)
    print(txt)
    print("\n[usage] %s" % usage)
    return 0


if __name__ == "__main__":
    sys.exit(main())
