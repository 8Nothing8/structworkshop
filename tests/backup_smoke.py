"""备份策略冒烟：mccore.backup（off / 按天留存 / 保留最近 N 次）+ /api/settings + 编辑器保存接线。

在临时仓库（STRUCTWORKSHOP_ROOT）里跑，所以不会碰到真实仓库的 `.cache/backups`。

覆盖：
1. 默认策略 = count 20；设置文件读写 + 范围/模式校验（非法值报错）。
2. count：同一个文件按次数留底，超出的最旧份数删掉，文件夹空了要一起收掉。
3. daily：同一个文件一天只留一份（当天再存覆盖当天那份）+ 按天数清理。
4. off：不新建备份（backup_file → None），也不删已有备份。
5. 结构保存（HTTP）真的按策略走：响应里的 backup 路径/有无随策略变化。
6. /api/settings 的 GET / POST / clear 三个接口 + 保存后自动按新策略清理。
7. UI 接线（静态）：设置页在「结构编辑器」后面、settings.js 被下发、tab 呼叫 onShow。

Usage:  python tests/backup_smoke.py
"""
from __future__ import annotations

import json
import os
import shutil
import socket
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "packages"))

TMP = Path(tempfile.mkdtemp(prefix="structworkshop_backup_"))
os.environ["STRUCTWORKSHOP_ROOT"] = str(TMP)
(TMP / "pyproject.toml").write_text("", encoding="utf-8")
(TMP / "packages").mkdir()
(TMP / "packs" / "demo" / "modules" / "rooms").mkdir(parents=True)
(TMP / "packs" / "demo" / "pack.json").write_text(
    json.dumps({"id": "demo", "name": "demo", "version": "1.0.0"}),
    encoding="utf-8")
(TMP / "builds").mkdir()

import numpy as np  # noqa: E402

from mccore import backup as BK  # noqa: E402
from mccore import structure_io as S  # noqa: E402
from mcstudio.server import App, make_handler  # noqa: E402

AIR = {"Name": "minecraft:air"}
STONE = {"Name": "minecraft:stone"}
PORT = {"host": "127.0.0.1", "port": 0}
OK, FAIL = [], []


def check(name, cond, extra=""):
    (OK if cond else FAIL).append(name)
    print(("  PASS " if cond else "  FAIL ") + name + (f"  {extra}" if extra else ""))


def call(method, path, body=None):
    url = f"http://127.0.0.1:{PORT['port']}{path}"
    data = json.dumps(body).encode("utf-8") if body is not None else None
    headers = {"Content-Type": "application/json"} if data else {}
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            payload = r.read()
            return r.status, (json.loads(payload.decode("utf-8"))
                              if "json" in r.headers.get("Content-Type", "") else payload)
    except urllib.error.HTTPError as e:
        payload = e.read()
        try:
            return e.code, json.loads(payload.decode("utf-8"))
        except Exception:                                   # noqa: BLE001
            return e.code, payload


def write_src(name="demo.schem", size=8):
    p = TMP / "builds" / name
    v = np.zeros((size, size, size), dtype=np.uint16)
    v[0:2, :, :] = 1
    S.write_structure(str(p), v, [AIR, STONE], (0, 0, 0), (size, size, size),
                      name=Path(name).stem)
    return p


def versions(name="scratch.bin", kind="structures"):
    return BK._versions(kind).get(name, [])


def scratch() -> Path:
    """用来反复改写的临时源文件（只给 backup_file 当输入，不是合法结构）。"""
    p = TMP / "builds" / "scratch.bin"
    p.write_bytes(b"v0")
    return p


def reset_backups():
    shutil.rmtree(BK.backups_root(), ignore_errors=True)


def day(ts: str) -> float:
    return time.mktime(time.strptime(ts, "%Y%m%d"))


def main() -> int:
    app = App()
    httpd = ThreadingHTTPServer((PORT["host"], 0), make_handler(app))
    PORT["port"] = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        print("\n== 1. 设置读写与校验 ==")
        pol = BK.backup_policy()
        check("默认策略 = count 20 / days 7",
              pol == {"mode": "count", "count": 20, "days": 7}, pol)
        check("默认不落盘（文件还没被写过）", not BK.settings_path().is_file())
        st, r = call("GET", "/api/settings")
        check("GET /api/settings（策略 + 目录 + 统计 + 模式表）",
              st == 200 and r["backup"]["mode"] == "count"
              and r["stats"]["root"] == ".cache/backups"
              and set(r["modes"]) == {"off", "count", "daily"}
              and r["stats"]["kinds"]["structures"]["managed"] is True
              and r["stats"]["kinds"]["modules"]["managed"] is False, str(r)[:120])
        st, err = call("POST", "/api/settings", {"backup": {"mode": "nope"}})
        check("非法模式 → 400", st == 400 and "备份模式" in str(err), str(err)[:80])
        st, err = call("POST", "/api/settings", {"backup": {"count": 0}})
        check("count=0 → 400", st == 400 and "范围" in str(err), str(err)[:80])
        st, err = call("POST", "/api/settings", {"backup": {"days": 999}})
        check("days 超上限 → 400", st == 400 and "范围" in str(err), str(err)[:80])

        print("\n== 1b. 打开上限（设置页可改 + 环境变量覆盖） ==")
        lim = BK.limits_effective()
        check("默认上限 = 24MB / 2000万格 / 200万块 / 告警 800万格",
              lim["values"] == BK.LIMIT_DEFAULTS and lim["env"] == {}, lim["values"])
        check("GET /api/settings 带 structure 规格（值/默认/范围/单位）",
              set(r["structure"]["keys"]) == set(BK.LIMIT_KEYS)
              and r["structure"]["values"]["max_mb"] == 24.0
              and r["structure"]["labels"]["max_blocks"] == "方块数量上限"
              and r["structure"]["units"]["max_mb"] == "MB"
              and r["structure"]["range"]["max_cells"][1] == 2_000_000_000,
              str(r.get("structure"))[:110])
        st, err = call("POST", "/api/settings", {"structure": {"max_mb": 0.01}})
        check("max_mb 超范围 → 400", st == 400 and "范围" in str(err), str(err)[:70])
        st, err = call("POST", "/api/settings", {"structure": {"max_blocks": "x"}})
        check("max_blocks 非数字 → 400", st == 400 and "数字" in str(err), str(err)[:70])

        # 造一个 4×4×4 = 64 格、64 块的小结构，用闸门拦它（走真实 /api/structure/open）
        gate = TMP / "builds" / "gate.schem"
        gv = np.zeros((4, 4, 4), dtype=np.uint16)
        gv[:] = 1
        S.write_structure(str(gate), gv, [AIR, STONE], (0, 0, 0), (4, 4, 4), name="gate")
        call("POST", "/api/settings", {"structure": {"max_blocks": 10, "max_cells": 1000}})
        st, err = call("POST", "/api/structure/open", {"path": "builds/gate.schem"})
        check("方块数超上限 → 拒开（提示去设置页 / 环境变量）",
              st == 400 and "方块数上限" in str(err) and "设置" in str(err), str(err)[:100])
        call("POST", "/api/settings", {"structure": {"max_blocks": 100, "max_cells": 10}})
        st, err = call("POST", "/api/structure/open", {"path": "builds/gate.schem"})
        check("格数超上限 → 拒开", st == 400 and "格数上限" in str(err), str(err)[:90])
        st, r = call("POST", "/api/settings",
                     {"structure": {"max_blocks": 100, "max_cells": 1000,
                                     "warn_cells": 32}})
        check("改上限落盘且 backup 不受影响",
              st == 200 and r["structure"]["saved"]["max_blocks"] == 100
              and r["backup"]["mode"] == "count", str(r.get("structure"))[:80])
        st, r = call("POST", "/api/structure/open", {"path": "builds/gate.schem"})
        check("放宽后能开；超过告警阈值时给 notice（格数+方块数）",
              st == 200 and (r.get("notice") or {}).get("blocks") == 64
              and (r.get("notice") or {}).get("cells") == 64, str(r.get("notice"))[:90])
        if r.get("sid"):
            call("POST", f"/api/structure/{r['sid']}/close", {})
        # 环境变量优先级最高（运维 escape hatch，改了不用重启）
        os.environ["STRUCTWORKSHOP_MAX_STRUCTURE_BLOCKS"] = "1"
        st, err = call("POST", "/api/structure/open", {"path": "builds/gate.schem"})
        st2, r2 = call("GET", "/api/settings")
        env_ok = (st == 400 and "方块数上限" in str(err)
                  and r2["structure"]["source"]["max_blocks"] == "env"
                  and r2["structure"]["env"]["max_blocks"] == "1")
        os.environ.pop("STRUCTWORKSHOP_MAX_STRUCTURE_BLOCKS", None)
        check("环境变量覆盖设置文件（GET 里标 env）", env_ok, str(err)[:80])
        # 恢复默认，免得影响后面的用例（也是设置页「恢复默认」走的服务端路径）
        st, r = call("POST", "/api/settings", {"structure": dict(BK.LIMIT_DEFAULTS)})
        check("上限可恢复默认",
              st == 200 and r["structure"]["saved"] == BK.LIMIT_DEFAULTS
              and r["structure"]["source"]["max_blocks"] == "default",
              str(r["structure"]["saved"])[:90])

        print("\n== 2. 保留最近 N 次 ==")
        reset_backups()
        src = scratch()
        st, r = call("POST", "/api/settings", {"backup": {"mode": "count", "count": 3}})
        check("切到 count=3（落盘）",
              st == 200 and r["backup"] == {"mode": "count", "count": 3, "days": 7}
              and BK.settings_path().is_file(), str(r.get("backup")))
        for i in range(6):                       # 每次改点内容，隔 1 秒一个时间戳
            src.write_bytes(src.read_bytes() + bytes([i]))
            bk = BK.backup_file(src, "structures", when=time.time() + i)
        v = versions()
        check("存 6 次 → 只留最新 3 份",
              len(v) == 3 and [p.stat().st_size for p in v] ==
              sorted([p.stat().st_size for p in v], reverse=True),
              [p.parent.name for p in v])
        check("最旧那份的目录被收掉了（不留空目录）",
              len(list((BK.backups_root() / "structures").iterdir())) == 3,
              str([d.name for d in (BK.backups_root() / "structures").iterdir()]))

        print("\n== 3. 按天留存 ==")
        reset_backups()
        call("POST", "/api/settings", {"backup": {"mode": "daily", "days": 30}})
        for i in range(3):                       # 同一天存 3 次
            src.write_bytes(bytes(10 + i))
            BK.backup_file(src, "structures", when=day("20260910") + i * 60)
        v = versions()
        check("同一天存 3 次 → 只留 1 份（覆盖当天那份）",
              len(v) == 1 and v[0].parent.name.startswith("20260910"), v)
        for d in ("20260911", "20260913", "20260914"):     # 再补 3 天
            src.write_bytes(bytes(20))
            BK.backup_file(src, "structures", when=day(d))
        check("按天累积：4 天 → 4 份（每天一份）", len(versions()) == 4,
              sorted(p.parent.name[:8] for p in versions()))
        call("POST", "/api/settings", {"backup": {"mode": "daily", "days": 3},
                                       "prune": False})
        out = BK.prune(today="20260914")
        left = sorted(p.parent.name[:8] for p in versions())
        check("days=3 + today=0914 → 只留 13/14（含今天 3 个自然日）",
              left == ["20260913", "20260914"] and out["removed"] == 2,
              {"left": left, "pruned": out})

        print("\n== 4. 不备份 ==")
        call("POST", "/api/settings", {"backup": {"mode": "off"}})
        before = len(versions())
        src.write_bytes(bytes(30))
        check("off → 不再新建备份",
              BK.backup_file(src, "structures") is None and len(versions()) == before,
              {"before": before, "after": len(versions())})
        p2 = BK.prune()
        check("off → prune 不动已有备份（并说明原因）",
              p2["removed"] == 0 and "skipped" in p2, p2)

        print("\n== 5. 结构保存按策略走（HTTP） ==")
        reset_backups()
        call("POST", "/api/settings", {"backup": {"mode": "count", "count": 20}})
        write_src("demo.schem")
        st, s = call("POST", "/api/structure/open", {"path": "builds/demo.schem"})
        sid = s["sid"]
        st, r1 = call("POST", f"/api/structure/{sid}/save", {})
        check("首次保存（文件已在，先留底）→ 返回 backup 路径",
              st == 200 and r1["backup"] and Path(r1["backup"]).is_file(), str(r1)[:90])
        st, r2 = call("POST", f"/api/structure/{sid}/save-as",
                      {"path": "builds/demo2.schem"})
        check("另存到新路径（原来没文件）→ 不留底",
              st == 200 and r2["backup"] is None, str(r2)[:90])
        st, r3 = call("POST", f"/api/structure/{sid}/save-as",
                      {"path": "builds/demo2.schem"})
        check("再存一次 → 又有留底", st == 200 and r3["backup"], str(r3)[:90])
        call("POST", "/api/settings", {"backup": {"mode": "off"}})
        st, r4 = call("POST", f"/api/structure/{sid}/save", {})
        check("切到 off 后保存 → 不留底（backup=None），但保存本身成功",
              st == 200 and r4["backup"] is None and Path(r4["path"]).is_file(),
              str(r4)[:90])

        print("\n== 6. 保存设置时自动按新策略清理 + clear 接口 ==")
        reset_backups()
        call("POST", "/api/settings", {"backup": {"mode": "count", "count": 10}})
        src = scratch()
        for i in range(6):
            src.write_bytes(bytes(40 + i))
            BK.backup_file(src, "structures", when=time.time() + i)
        check("先攒 6 份（backup_file 每次留一份）", len(versions()) == 6,
              len(versions()))
        st, r = call("POST", "/api/settings", {"backup": {"mode": "count", "count": 2}})
        check("保存 count=2 → 立刻清理并报告",
              st == 200 and r["pruned"]["removed"] == 4 and len(versions()) == 2,
              {"pruned": r.get("pruned"), "left": len(versions())})
        st, r = call("POST", "/api/settings", {"backup": {"mode": "count"}, "prune": False})
        check("prune:false 可跳过清理", st == 200 and r["pruned"] is None, str(r)[:80])
        # clear：只清受策略管的类别
        mod_dir = BK.backups_root() / "modules" / "20260101-000000"
        mod_dir.mkdir(parents=True, exist_ok=True)
        (mod_dir / "old_mod.schem").write_bytes(b"keep me")
        st, r = call("POST", "/api/settings/clear", {})
        check("clear 清空 structures/tools 并回报数量",
              st == 200 and r["cleared"]["removed"] == 2 and len(versions()) == 0,
              {"cleared": r.get("cleared")})
        check("clear 不动 modules 的备份（那是删模块挪走的原件）",
              (mod_dir / "old_mod.schem").is_file(), str(mod_dir))
        st, err = call("POST", "/api/settings/clear", {"kinds": ["modules"]})
        check("clear 不允许指定 modules → 400", st == 400, str(err)[:80])

        print("\n== 7. UI 接线（静态 + 下发） ==")
        html = (ROOT / "packages" / "mcstudio" / "web" / "index.html").read_text(encoding="utf-8")
        js = (ROOT / "packages" / "mcstudio" / "web" / "settings.js").read_text(encoding="utf-8")
        app_js = (ROOT / "packages" / "mcstudio" / "web" / "app.js").read_text(encoding="utf-8")
        i_lib = html.find('data-view="library"')
        i_ed = html.find('data-view="editor"')
        i_set = html.find('data-view="settings"')
        check("设置 tab 排在「模块库 / 结构编辑器」后面",
              0 <= i_lib < i_ed < i_set, [i_lib, i_ed, i_set])
        check("设置页有模式按钮/两个数字/保存与清空入口",
              all(k in html for k in ('id="set-modes"', 'id="set-count"', 'id="set-days"',
                                      'id="btn-set-save"', 'id="btn-set-clear"',
                                      'id="set-stats"')))
        check("设置页有「打开上限」四项输入 + 保存/恢复默认入口",
              all(k in html for k in ('id="set-max-mb"', 'id="set-max-cells"',
                                      'id="set-max-blocks"', 'id="set-warn-cells"',
                                      'id="btn-set-limits-save"', 'id="btn-set-limits-reset"')))
        check("settings.js 会保存 structure 上限并展示环境变量锁定",
              "structure: v.structure" in js and "renderLimits" in js
              and "环境变量" in js)
        check("settings.js 被页面引入", "/static/settings.js" in html)
        check("切到设置 tab 会拉数据",
              "global.Settings.onShow()" in app_js and "onShow: load" in js)
        st, jsout = call("GET", "/static/settings.js")
        check("/static/settings.js 可下发", st == 200 and b"/api/settings" in jsout)
    finally:
        httpd.shutdown()
        httpd.server_close()
        time.sleep(0.1)
        shutil.rmtree(TMP, ignore_errors=True)

    print(f"\n{'ALL PASS' if not FAIL else 'FAILED'}  "
          f"({len(OK)} passed, {len(FAIL)} failed)")
    if FAIL:
        print("失败项: " + ", ".join(FAIL))
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
