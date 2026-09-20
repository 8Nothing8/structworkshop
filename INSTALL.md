# 安装与启动（小白版）

这个文件夹就是**整个程序**（引擎 + 工作台网页 + AI 用的 skill）。
它不需要安装到系统里 —— 解压出来、双击一个脚本就能用；**删掉文件夹就是卸载**。

---

## Windows：三步

1. **装 Python**（只有第一次需要）
   打开 <https://www.python.org/downloads/> → 下载 **3.12 或 3.13**（别选最新预览版）→
   安装时**务必勾选** `Add python.exe to PATH` → 装完按 `Win + R`，输入 `cmd` 回车，
   敲 `python -V`，能看到版本号就成了。

2. **解压**到一个普通目录，例如 `D:\structworkshop`
   （**别放** `C:\Program Files`，也别放 OneDrive / 网盘同步夹 —— 程序要在文件夹里建缓存）

3. **双击 `启动工作台.bat`**
   - 第一次：自动建虚拟环境 `.venv` → 下载依赖（几十 MB）→ 启动 → 浏览器自动打开。
     大约 1–3 分钟（看网速）。
   - 以后：**秒开**（依赖已经在 `.venv` 里了）。
   - 关掉那个黑色窗口 = 退出工作台。

浏览器里的地址是 `http://127.0.0.1:8617/`；如果这个端口被占了，程序会自动换一个，
真实地址看黑窗口里打印的那行。首次渲染 3D 会顺便从 mcmeta 镜像拉方块贴图，
缓存在 `.cache\mcassets`（只需一次，之后离线也能用）。

## macOS / Linux：三步

1. 装 Python 3.10+（macOS：官网安装包或 `brew install python@3.12`；
   Debian/Ubuntu：`sudo apt install python3 python3-venv`）
2. 解压到一个可写目录（同样别放系统目录）
3. 在终端里：

```bash
chmod +x 启动工作台.sh      # 只需一次
./启动工作台.sh
```

`Ctrl + C` 退出。

---

## 它到底干了什么（为什么可以放心）

| 你担心的 | 实际情况 |
|---|---|
| 会不会弄脏系统 Python？ | **不会。** 脚本只在**本文件夹内**建 `.venv`，依赖和注册全部写在 `.venv/` 里；不写注册表、不改 PATH、不往系统 `site-packages` 装东西 |
| 会不会留下指向某个路径的残留？ | **不会。** 唯一的「路径登记」是 `.venv` 内部的注册，它随文件夹一起被删掉。脚本运行时的 `PYTHONPATH` 只在那一个进程里生效，窗口一关就没了 |
| 删掉文件夹会怎样？ | 等于彻底卸载。`.venv`（依赖，几百 MB）和 `.cache`（贴图缓存）都在文件夹里，删了就没了 |
| 移动文件夹之后还能用吗？ | 能。脚本发现 `.venv` 里的注册路径失效时会**就地补一次注册**（不下载依赖）；另外每次启动都会临时设 `PYTHONPATH` 兜底 |
| 需要联网吗？ | 第一次装依赖和第一次拉贴图需要；之后可离线。完全断网的机器：让发送方用 `python tools/make_release.py --wheels` 打包（`wheels/` 会一起发过来） |

> ⚠️ 只有一种情况会真的「污染」Python 环境：**你自己**在这个文件夹里手敲了
> `pip install -e .`（不带虚拟环境）。那会在系统 `site-packages` 里写一条指向本文件夹的
> `.pth`；之后你把文件夹删了，那条记录还在，`python -m mcstudio` 就会找不到东西。
> 清理办法：`pip uninstall -y structworkshop`（或删掉 `site-packages\__editable__.structworkshop-*.pth`）。
> **启动脚本从不做这件事。**

---

## 装了好几份，怎么启动确定的那一份？

每份副本都是**独立**的：它的 `.venv` 只认它自己。规则很简单：

| 你想启动哪一份 | 怎么做 |
|---|---|
| 某一份（推荐） | 双击**那一份**里的 `启动工作台.bat`（macOS/Linux 跑它的 `./启动工作台.sh`）。脚本会先自检：引擎不是本文件夹就报错退出，并打印实际用的是哪一份 |
| 命令行临时跑某一份 | `PYTHONPATH=<那份>/packages python -m mcstudio serve` |
| 用某一份的 venv 干活 | Windows：`<那份>\.venv\Scripts\python.exe -m mcstudio serve`<br>macOS/Linux：`<那份>/.venv/bin/python -m mcstudio serve` |

**怎么确认现在用的是哪一份**（任何 python 里都能跑）：

```bash
python tools/which_copy.py        # 引擎路径 + site-packages 里的登记 + 悬空检查
python -c "import mccore; print(mccore.__file__)"
```

工作台启动时也会打印一行 `仓库: <路径>` —— 那就是它**实际在用**的那一份。

> ⚠️ **「python 环境已经绑定上一份」是怎么发生的**：只有你**手动** `pip install -e .`
> 过才会 —— 它往系统 `site-packages` 写一条 `.pth`，于是**在任何目录**敲
> `python -m mcstudio` 都用那一份（而不是你双击的那一份）。两个办法：
> ① 清掉这个绑定：`pip uninstall -y structworkshop`（推荐 —— 之后各份井水不犯河水，
> 也不会在删目录后留下指向空路径的登记）；
> ② 想让另一份当默认：去**那一份**目录里重跑 `pip install -e .`。
>
> **用启动脚本不存在这个问题**：它每次都显式把 `PYTHONPATH` 指向自己那份的 `packages/`，
> 并且启动前用 `tools/which_copy.py --expect` 自检。

---

## 常见问题

- **双击后窗口一闪而过**
  在文件夹地址栏输入 `cmd` 回车（这样打开的窗口不会闪退），然后敲 `启动工作台.bat`，就能看到完整报错。
- **提示 `No module named mcstudio`**
  说明你没用脚本、而是自己敲了 `python -m mcstudio ...`。两种解法：
  用脚本；或者先激活虚拟环境 —— Windows `.venv\Scripts\activate`、macOS/Linux `source .venv/bin/activate`，
  之后再敲命令。想临时跑一次也可以设 `PYTHONPATH` 指向本文件夹的 `packages/`。
- **依赖装得很慢 / 失败**
  换网络重试；或者找发送方要带 `wheels/` 的离线包（脚本会自动走离线安装）。
- **3D 里全是纯色方块**
  贴图资源没拉到（网络或镜像问题）。换个网络重来一次；或让发送方把 `.cache/mcassets` 一起打包。
- **端口被占**
  不用管，会自动换（8617→8636）。想固定就双击不了、用命令行：`启动工作台.bat --port 8700`。
- **想换成别的版本 / 重来一遍**
  删掉文件夹里的 `.venv` 再双击脚本即可（会重新建）。

---

## 给 AI 用这个文件夹

把**这个文件夹**设成你 AI 工具的项目目录。里面有：

- `skills/` —— 10 个 skill（Agent Skills 标准：`SKILL.md` + frontmatter），
  AI 的入口是 `skills/structworkshop-overview/SKILL.md`（仓库有什么、命令地图、端到端工作流）。
  若你的工具没有自动加载它们，让它按自己的规矩注册到项目 skill 目录
  （pi → `.pi/skills/`，Claude Code → `.claude/skills/`）。
- `README.md` —— 人看的总览；`AGENT.md` —— 架构与硬约束（给 AI 读的详细版）。

## 目录里都有什么

```
packages/         引擎：9 个 Python 包 + 工作台前端
skills/           10 个 skill（AI 的方法索引与命令说明）
tests/ tools/     冒烟测试与自检小工具
packs/            投影素材包（资产包；本地内容，可选）
compositions/     一类建筑的做法（生成器 + 提示词；可选）
builds/ kb/       作品归档 / 规范语料（可选）
启动工作台.bat     Windows 启动器（首次运行会自动安装）
启动工作台.sh macOS / Linux 启动器
```

> 想自己重新生成一份这样的干净发行包：在仓库里跑 `python tools/make_release.py --zip`
> （默认排除 `.git/ .github/ .cache/ .venv/ __pycache__/` 等，并会扫一遍绝对路径残留）。
