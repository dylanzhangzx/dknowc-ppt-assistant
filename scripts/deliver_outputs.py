#!/usr/bin/env python3
"""把本次任务产出物复制到用户可见的工作目录（WorkBuddy / 豆包等宿主环境）。

产出物默认生成在 skill 安装目录内（提纲/成稿核验报告于 official-docs/output/，
三件套于 projects/<项目>/exports/），宿主只向用户展示其工作区目录中的文件，
导致用户看不到交付物。本脚本自动识别宿主、探测其工作区，把刚生成的文件复制
过去并输出用户可见路径。

探测优先级（宿主工作区，宿主身份先经 detect_host 确认）：
  1. --dest 显式指定（不得指向 skill 目录自身）
  2. 宿主注入的工作区环境变量（仅明确命名的变量，不认 WORKSPACE/PROJECT_DIR 等通用变量）
  3. WorkBuddy 宿主：CODEBUDDY_PROJECT_DIR / CLAUDE_PROJECT_DIR（本次任务工作区，最精确）
     → 当前目录所在的时间戳工作区（进程在哪个工作区运行就交付到哪，多会话并发时最可靠）
     → 否则 ~/WorkBuddy/ 下最新的时间戳工作区，产物复制到其 outputs/ 子目录；
       最新两个工作区时间戳间隔小于 10 分钟（并发会话）时视为歧义，不自动复制，要求 --dest 指定
  4. 豆包宿主：~/DoubaoWork/chats/<日期>/<会话> 中当前目录所在（或最近活跃的）会话目录
  5. 宿主未知：当前目录（仅当当前目录不在 skill 目录树内）
  6. 都探测不到（宿主未知且 cwd 在 skill 树内）：不复制，交付物保留在 skill 输出目录
     （仅 WorkBuddy/豆包有明确工作区才复制，其余宿主不做猜测）

注意一：宿主 agent 执行 skill 脚本时当前目录常在 skill 安装目录内，因此不能用
"当前目录是否等于 skill 目录"判断宿主环境。

注意二：**宿主身份未确认时不得写入 ~/WorkBuddy**。原逻辑把 "~/WorkBuddy 下最新工作区"
作为兜底，导致其他宿主任务的产物被复制进用户当天在 WorkBuddy 的工作区并覆盖同名
交付物。宿主未知时宁可不复制。

任何情况下都不会把文件复制到 skill 安装目录自身；也不会覆盖宿主目录中的同名
文件——内容不同时自动改名为"名字_v2.扩展名"（依次 _v3、_v4…），内容相同则跳过。

用法：
  python3 scripts/deliver_outputs.py <文件1> [文件2 ...] [--dest <目录>]
  python3 scripts/deliver_outputs.py --probe    # 只输出宿主识别证据，不复制文件
  文件路径为空时，自动复制 official-docs/output/ 与 projects/*/exports/
  下最近 10 分钟内新建的文件（提纲版报告在确认门前即可交付展示；
  交付三件套时建议显式传入 .pptx 与两份核验报告路径）。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parent.parent
OUTPUT_DIR = SKILL_ROOT / "official-docs" / "output"
PROJECTS_DIR = SKILL_ROOT / "projects"
RECENT_WINDOW_SECONDS = 10 * 60

# 常见宿主工作区环境变量，按顺序探测
WORKSPACE_ENV_VARS = (
    "WORKBUDDY_WORKSPACE",
    "WORKBUDDY_WORKDIR",
    "WORKBUDDY_PROJECT_DIR",
    "AGENT_WORKSPACE",
    "AGENT_WORKDIR",
    "AGENT_PROJECT_DIR",
    "WORKSPACE_DIR",
    "WORKSPACE",
    "PROJECT_DIR",
    "WORKING_DIR",
)

# WorkBuddy 任务工作区目录名：YYYY-MM-DD-HH-MM-SS
WORKBUDDY_DIR_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}-\d{2}-\d{2}-\d{2}$")

# 并发歧义判定阈值：最新两个工作区时间戳间隔小于该值视为多会话并发
AMBIGUOUS_WINDOW_SECONDS = 10 * 60

# 豆包宿主工作区：~/DoubaoWork/chats/<日期>/<会话>/
DOUBAO_CHATS_DIR = Path.home() / "DoubaoWork" / "chats"

# 豆包会话目录的活跃窗口：mtime 早于该窗口视为不是本次任务的会话
DOUBAO_ACTIVE_WINDOW_SECONDS = 6 * 60 * 60

# 宿主身份关键词（小写匹配，用于祖先进程可执行路径）
HOST_PROCESS_MARKERS = {
    "doubao": ("doubaowork", "doubao"),
    "workbuddy": ("workbuddy",),
}

# 宿主注入的工作区环境变量：只认这些明确命名的变量，不扫描整个环境——
# PWD/OLDPWD 等含路径的变量会因上一次 cd 的位置造成误判。
HOST_ENV_VARS = (
    "WORKBUDDY_WORKSPACE",
    "WORKBUDDY_WORKDIR",
    "WORKBUDDY_PROJECT_DIR",
    "WORKBUDDY_HOME",
    # WorkBuddy 实测补充：宿主实际注入的权威标识是 CODEBUDDY_HOST
    # （值如 workbuddy-desktop）与 WORKBUDDY_PRODUCT_NAME（值 WorkBuddy）——
    # 此前只查 WORKBUDDY_* 前缀，在 WorkBuddy 下必然识别失败、每次任务都要 --dest。
    "CODEBUDDY_HOST",
    "WORKBUDDY_PRODUCT_NAME",
    "DOUBAO_WORKSPACE",
    "DOUBAO_WORKDIR",
    "DOUBAO_CHAT_DIR",
    "DOUBAO_SESSION_DIR",
    "DOUBAO_HOME",
)


def ancestor_executable_paths(max_depth: int = 10) -> list[str]:
    """沿父进程链向上收集各进程的可执行文件路径，用于识别宿主 App。

    只取可执行路径、不取命令行参数：agent 执行的命令文本里可能出现宿主名
    （例如调试命令里带 ~/WorkBuddy 路径），用整条命令行做关键词匹配会被污染。
    实测中，本脚本曾因探测代码自身含 "workbuddy" 字面量，把其他环境误判为 WorkBuddy。

    豆包桌面版的进程路径含 DoubaoWork.app，WorkBuddy 含 WorkBuddy.app；
    宿主沙箱可能截断进程树，取到多少算多少。
    """
    paths: list[str] = []
    pid = os.getppid()
    for _ in range(max_depth):
        if pid <= 1:
            break
        try:
            proc = subprocess.run(
                ["ps", "-o", "ppid=,comm=", "-p", str(pid)],
                capture_output=True, text=True, timeout=3,
            )
        except (OSError, subprocess.SubprocessError):
            break
        line = proc.stdout.strip()
        if not line:
            break
        parts = line.split(None, 1)
        if len(parts) < 2:
            break
        ppid_text, executable = parts
        paths.append(executable)
        try:
            pid = int(ppid_text)
        except ValueError:
            break
    return paths


def detect_host() -> tuple[str, str, list[str]]:
    """识别当前宿主环境及判定依据。

    返回（宿主标识 workbuddy/doubao/unknown, 依据, 祖先进程可执行路径链）。
    依据优先级：可执行路径链 → 宿主环境变量 → 当前目录。识别不出时返回 unknown，
    此时不猜测宿主、不使用任何宿主工作区兜底。
    """
    executables = ancestor_executable_paths()
    executable_blob = " ".join(executables).lower()
    for host, markers in HOST_PROCESS_MARKERS.items():
        if any(marker in executable_blob for marker in markers):
            return host, "ancestor-executable", executables

    for key in HOST_ENV_VARS:
        value = os.environ.get(key, "").strip()
        if value:
            if key == "CODEBUDDY_HOST":
                # CODEBUDDY_HOST 的值是权威宿主标识（如 workbuddy-desktop）；值不含
                # workbuddy 时不据此判定，继续看其他变量
                if "workbuddy" in value.lower():
                    return "workbuddy", f"env:{key}={value}", executables
                continue
            host = "doubao" if "doubao" in key.lower() else "workbuddy"
            return host, f"env:{key}", executables

    cwd = Path.cwd().resolve()
    doubao_root = DOUBAO_CHATS_DIR.parent
    if cwd == doubao_root or doubao_root in cwd.parents:
        return "doubao", "cwd", executables
    if cwd_workbuddy_workspace() is not None:
        return "workbuddy", "cwd", executables

    return "unknown", "none", executables


def cwd_doubao_chat() -> Path | None:
    """当前目录所在的豆包会话目录；不在会话目录内返回 None。

    路径形如 ~/DoubaoWork/chats/<日期>/<会话>（会话目录下可能还有子目录）。
    """
    root = DOUBAO_CHATS_DIR.resolve()
    if not root.is_dir():
        return None
    cwd = Path.cwd().resolve()
    for candidate in (cwd, *cwd.parents):
        try:
            parts = candidate.relative_to(root).parts
        except ValueError:
            continue
        # 相对 chats 的前两段即 <日期>/<会话>
        if len(parts) >= 2:
            chat = root / parts[0] / parts[1]
            return chat if chat.is_dir() else None
    return None


def doubao_chat_dir() -> tuple[Path | None, list[str]]:
    """豆包宿主当前会话目录。

    优先用当前目录所在的会话（进程在哪运行就交付到哪，与 WorkBuddy 分支同理、
    并发最可靠）；定位不到时退化为"最近活跃的会话目录"，其 mtime 早于活跃窗口
    视为无法确定。
    """
    cwd_chat = cwd_doubao_chat()
    if cwd_chat is not None:
        return cwd_chat, []

    if not DOUBAO_CHATS_DIR.is_dir():
        return None, []
    chats = [
        chat
        for day in DOUBAO_CHATS_DIR.iterdir()
        if day.is_dir()
        for chat in day.iterdir()
        if chat.is_dir() and not chat.name.startswith(".")
    ]
    if not chats:
        return None, []
    chats.sort(key=lambda d: d.stat().st_mtime, reverse=True)
    names = [f"{chat.parent.name}/{chat.name}" for chat in chats[:5]]
    if (time.time() - chats[0].stat().st_mtime) > DOUBAO_ACTIVE_WINDOW_SECONDS:
        return None, names
    return chats[0], names


def workbuddy_workspaces() -> list[Path]:
    """返回 ~/WorkBuddy/ 下全部时间戳工作区，按目录名从新到旧排序。

    目录名形如 2026-08-29-16-28-53，字典序即时间序（新工作区刚创建、
    尚无文件写入时 mtime 不可靠，按目录名排序最可靠）。
    """
    root = Path.home() / "WorkBuddy"
    if not root.is_dir():
        return []
    candidates = [
        d for d in root.iterdir()
        if d.is_dir() and WORKBUDDY_DIR_PATTERN.match(d.name)
    ]
    return sorted(candidates, key=lambda d: d.name, reverse=True)


def cwd_workbuddy_workspace() -> Path | None:
    """当前目录所在的 WorkBuddy 时间戳工作区；不在任何工作区内返回 None。

    多会话并发时"最新时间戳工作区"可能是别的会话，而进程当前目录是
    宿主为本任务设定的工作位置，优先以其为准。
    """
    root = (Path.home() / "WorkBuddy").resolve()
    cwd = Path.cwd().resolve()
    for parent in (cwd, *cwd.parents):
        if parent.parent == root and WORKBUDDY_DIR_PATTERN.match(parent.name):
            return parent
    return None


def workspace_timestamp(name: str) -> float:
    """把工作区目录名解析为时间戳，失败返回 0。"""
    from datetime import datetime
    try:
        return datetime.strptime(name, "%Y-%m-%d-%H-%M-%S").timestamp()
    except ValueError:
        return 0.0


def in_skill_tree(path: Path) -> bool:
    """路径是否等于 skill 目录或位于其内部。"""
    return path == SKILL_ROOT or SKILL_ROOT in path.parents


def normalize_host_dest(dest: Path) -> Path:
    """宿主工作区目录归一：目标是 WorkBuddy 时间戳工作区根目录时改用其 outputs/。

    宿主只展示工作区 outputs/ 下的文件。实测模型按规则传 `--dest <工作区目录>` 后
    产物落在工作区根目录、用户看不到，模型只好手工 cp 到 outputs/——而规则明令
    禁止手工复制。此处直接归一，消除这个死循环。
    """
    if dest.name and WORKBUDDY_DIR_PATTERN.match(dest.name):
        return dest / "outputs"
    return dest


def detect_dest(explicit_dest: str | None, host: str) -> tuple[Path | None, bool, str, bool, list[str]]:
    """探测交付目标目录。

    返回（目标目录或 None, 是否宿主环境, 探测来源说明, 是否需要用户补 --dest, 候选工作区名单）。
    host 来自 detect_host()：宿主未知时只认当前目录，不使用任何宿主工作区兜底。
    """
    if explicit_dest:
        dest = normalize_host_dest(Path(explicit_dest).expanduser().resolve())
        # 显式 --dest 也不得指向 skill 安装目录自身（会复制到包内造成自污染，
        # 与"任何情况下不复制到 skill 目录"承诺一致）
        if in_skill_tree(dest):
            raise SystemExit(f"错误：--dest 不能指向 skill 目录自身: {dest}")
        return dest, True, "explicit", False, []

    for name in WORKSPACE_ENV_VARS:
        value = os.environ.get(name, "").strip()
        if value:
            dest = Path(value).expanduser().resolve()
            if dest.is_dir() and (host != "unknown" or name in HOST_ENV_VARS):
                # 宿主未识别时只接受明确命名的宿主变量，不接受 WORKSPACE/PROJECT_DIR
                # 这类通用变量——它们常被 shell 或其它工具占用，会绕过"宿主未识别不复制"
                return normalize_host_dest(dest), True, f"env:{name}", False, []

    if host == "workbuddy":
        # 环境变量里宿主注入的本次任务工作区最精确（CODEBUDDY_PROJECT_DIR / CLAUDE_PROJECT_DIR
        # 直接就是当前任务工作区），优先于"cwd 所在工作区"与"最新时间戳"启发式。注意：
        # 这两个变量只在 host 已确认 workbuddy 后用于定位工作区，不作为宿主判定源
        # （CLAUDE_PROJECT_DIR 在本机其他环境也可能存在，避免误判宿主）。
        for proj_name in ("CODEBUDDY_PROJECT_DIR", "CLAUDE_PROJECT_DIR"):
            proj_value = os.environ.get(proj_name, "").strip()
            if not proj_value:
                continue
            proj = Path(proj_value).expanduser().resolve()
            if proj.is_dir() and proj.parent.name == "WorkBuddy":
                return proj / "outputs", True, f"workbuddy-env-proj:{proj.name}", False, []

        # 当前目录就在某个 WorkBuddy 工作区内：进程在哪运行就交付到哪，并发最可靠
        cwd_ws = cwd_workbuddy_workspace()
        if cwd_ws is not None:
            return cwd_ws / "outputs", True, f"workbuddy-cwd:{cwd_ws.name}", False, []

        workspaces = workbuddy_workspaces()
        if workspaces:
            newest = workspaces[0]
            # 歧义保护：最新两个工作区时间戳间隔很近说明有并发会话，"最新"未必是
            # 本任务的——宁可不复制也不串会话，列出候选要求 --dest 指定
            if len(workspaces) > 1:
                gap = workspace_timestamp(newest.name) - workspace_timestamp(workspaces[1].name)
                if 0 < gap < AMBIGUOUS_WINDOW_SECONDS:
                    return None, False, "ambiguous", True, [d.name for d in workspaces[:5]]
            return newest / "outputs", True, f"workbuddy:{newest.name}", False, []

    doubao_candidates: list[str] = []
    if host == "doubao":
        chat, doubao_candidates = doubao_chat_dir()
        if chat is not None:
            dest = chat / "outputs" if (chat / "outputs").is_dir() else chat
            return dest, True, f"doubao-chat:{chat.parent.name}/{chat.name}", False, []

    cwd = Path.cwd().resolve()
    if not in_skill_tree(cwd):
        # 当前目录在 skill 目录树之外，视为宿主工作区；已有 outputs/ 子目录时对齐
        dest = cwd / "outputs" if (cwd / "outputs").is_dir() else cwd
        return dest, True, "cwd", False, []

    # 宿主身份未知：不复制、不要求 --dest，交付物保留在 skill 输出目录
    # （仅 WorkBuddy/豆包有明确工作区才复制，其余宿主留在 skill output）
    if host == "unknown":
        return None, False, "unknown", False, doubao_candidates

    # 已识别宿主但工作区定位失败（如 WorkBuddy 并发歧义）：宁可不复制，要求 --dest
    return None, False, f"{host}-unresolved", True, doubao_candidates


def file_digest(path: Path) -> str:
    """文件内容摘要，用于判断同名文件是否为同一份。"""
    digest = hashlib.md5()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def copy_without_overwrite(src: Path, dest: Path) -> dict:
    """复制文件到目标目录，绝不覆盖已有同名文件。

    内容相同 → 跳过（skipped=identical）；内容不同 → 依次尝试"名字_v2.扩展名"
    （_v3、_v4…）另存（renamed=true）。新文件直接复制。命名与 render_trace_html 的
    版本化输出规则一致（替代原"名字 (2)"）。
    """
    target = dest / src.name
    # is_symlink 单独判定：断链软链接的 exists() 为假，copy2 会顺着链接写到目标之外；
    # 同名目标是目录时也要走改名分支，否则 copy2 会写进 "目录/文件名" 并覆盖其中
    # 同名文件，绕过"绝不覆盖"的承诺
    occupied = target.is_symlink() or target.exists()
    if occupied:
        if target.is_file() and not target.is_symlink():
            try:
                identical = (
                    target.stat().st_size == src.stat().st_size
                    and file_digest(target) == file_digest(src)
                )
            except OSError:
                identical = False
            if identical:
                return {
                    "copied": True, "source": str(src), "delivered": str(target),
                    "delivered_display": str(target), "skipped": "identical",
                }
        for index in range(2, 100):
            candidate = dest / f"{src.stem}_v{index}{src.suffix}"
            if not candidate.is_symlink() and not candidate.exists():
                target = candidate
                break
        else:
            return {"copied": False, "source": str(src),
                    "error": "同名文件过多，无法生成不冲突的文件名"}
    try:
        shutil.copy2(src, target)
    except OSError as exc:
        # 磁盘满等情况下 copy2 会留下截断文件：清理掉，避免半成品被当成交付物
        try:
            if target.exists() and target.is_file() and not target.is_symlink():
                target.unlink()
        except OSError:
            pass
        return {"copied": False, "source": str(src), "error": str(exc)}
    return {
        "copied": True, "source": str(src), "delivered": str(target),
        "delivered_display": str(target), "renamed": target.name != src.name,
    }


def collect_files(args_files: list[str]) -> list[Path]:
    if args_files:
        files = []
        for name in args_files:
            p = Path(name).expanduser()
            if not p.is_absolute():
                p = (SKILL_ROOT / p).resolve() if (SKILL_ROOT / p).is_file() else p.resolve()
            files.append(p)
        return files
    # 未指定文件：取报告初始输出目录与各项目 exports/ 最近窗口内新建的文件
    now = time.time()
    files: list[Path] = []
    scan_dirs = [OUTPUT_DIR]
    if PROJECTS_DIR.is_dir():
        scan_dirs.extend(d / "exports" for d in PROJECTS_DIR.iterdir() if d.is_dir())
    for scan_dir in scan_dirs:
        if not scan_dir.is_dir():
            continue
        for p in sorted(scan_dir.iterdir()):
            if p.is_file() and (now - p.stat().st_mtime) < RECENT_WINDOW_SECONDS:
                files.append(p)
    return files


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="复制产出物到宿主工作区（WorkBuddy / 豆包）")
    parser.add_argument("files", nargs="*",
                        help="要复制的产出物路径（建议始终显式传入）；为空时仅在近期文件唯一时自动选取，多个候选会拒绝执行以防串任务")
    parser.add_argument("--dest", help="宿主工作区目录（探测失败或需要明确指定时使用）")
    parser.add_argument("--probe", action="store_true", help="只输出宿主识别证据，不复制文件")
    args = parser.parse_args()

    host, host_evidence, ancestor_executables = detect_host()

    if args.probe:
        chat, chat_candidates = doubao_chat_dir()
        cwd = Path.cwd().resolve()
        cwd_workspace = cwd_workbuddy_workspace()
        print(json.dumps({
            "probe": True,
            "host": host,
            "host_evidence": host_evidence,
            "ancestor_executables": ancestor_executables,
            "cwd": str(cwd),
            "cwd_in_skill_tree": in_skill_tree(cwd),
            "cwd_workbuddy_workspace": cwd_workspace.name if cwd_workspace else None,
            "workbuddy_workspaces": [d.name for d in workbuddy_workspaces()[:5]],
            "doubao_chats_dir": str(DOUBAO_CHATS_DIR),
            "doubao_chat_active": str(chat) if chat else None,
            "doubao_chat_candidates": chat_candidates,
        }, ensure_ascii=False, indent=2))
        return 0

    dest, host_env, dest_source, need_dest, candidates = detect_dest(args.dest, host)
    files = collect_files(args.files)

    results = []
    if need_dest and dest_source == "ambiguous":
        cand_list = "、".join(candidates)
        note = (f"检测到多个时间戳相近的 WorkBuddy 工作区（{cand_list}），无法确定哪个是当前任务的，"
                "已停止自动复制以防产物串到其他会话。请用 --dest <当前任务的工作区目录> 重新运行本脚本。")
    elif need_dest:
        hint = f"（候选：{'、'.join(candidates)}）" if candidates else ""
        note = (f"已识别宿主为 {host}，但未能定位其工作区{hint}。已停止自动复制——"
                "不写入其他宿主的工作区，也不写进 skill 目录。"
                "请用 --dest <本次任务的工作区目录> 重新运行本脚本。")
    elif dest_source == "unknown":
        # 宿主未知（非 WorkBuddy、非豆包）：交付物保留在 skill 输出目录，不复制
        # （不做宿主猜测，用户可自行从 skill output 取文件）
        note = (f"未识别宿主环境（非 WorkBuddy / 豆包），交付物保留在 skill 输出目录 {OUTPUT_DIR}，"
                "不复制到其他位置。")
    elif not files:
        note = "没有找到可交付的产出物"
        results.append({"copied": False, "error": "没有找到可交付的产出物"})
    elif not args.files and len(files) > 1:
        # 未显式指定文件、且输出目录里同时有多个近期产物：不猜，要求显式指定
        # （此前会把上一任务的产物一起交付，造成串任务）
        note = (f"未指定产出物、且输出目录中有 {len(files)} 个近期文件（{'、'.join(p.name for p in files[:5])}），"
                "无法确定哪些是本次任务的，已停止复制。请显式传入本次产出物路径后重跑本脚本。")
        results.append({"copied": False, "error": "未指定产出物且有多个近期候选"})
    else:
        try:
            dest.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            # 目标路径被同名文件占用、权限不足等：给出结构化错误，不抛 traceback
            print(json.dumps({
                "host_env": False, "host": host, "host_evidence": host_evidence,
                "need_dest": True, "dest_source": "dest-unusable",
                "dest": str(dest), "skill_output_dir": str(OUTPUT_DIR),
                "note": (f"无法使用交付目录 {dest}（{exc}）。请确认 --dest 指向一个可用目录后重跑；"
                         "在复制成功前不要向用户交付 skill 内部路径。"),
                "files": [{"copied": False, "error": str(exc)}],
            }, ensure_ascii=False, indent=2))
            return 1
        for src in files:
            if not src.is_file():
                results.append({"copied": False, "source": str(src), "error": "文件不存在"})
                continue
            results.append(copy_without_overwrite(src, dest))
        # note 按实际结果生成：交付失败时不得再写"已复制到宿主工作区"
        # （模型最可能转述的就是这句自然语言）
        failed = [r for r in results if not r.get("copied")]
        if host_env and not failed:
            note = (f"宿主环境（{host} / {dest_source}）：产出物已复制到宿主工作区，向用户展示 delivered 路径。"
                    "同名文件不会被覆盖：内容相同自动跳过（skipped=identical），内容不同自动改名（renamed=true）。"
                    "如该目录不是当前任务工作区，请用 --dest 指定正确目录重跑。")
        elif host_env:
            note = (f"交付未完成：{len(failed)}/{len(results)} 个文件复制失败。"
                    f"失败原因见 files[].error。**不得向用户声称已交付**；"
                    "请按原因处理后重跑（目录不可写、磁盘空间等）。")
        else:
            note = ""

    payload = {
        "host_env": host_env,
        "host": host,
        "host_evidence": host_evidence,
        "need_dest": need_dest,
        "dest_source": dest_source,
        "dest": str(dest) if dest is not None else None,
        "skill_output_dir": str(OUTPUT_DIR),
        "skill_exports_dirs": [str(d / "exports") for d in PROJECTS_DIR.glob("*") if d.is_dir()] if PROJECTS_DIR.is_dir() else [],
        "note": note,
        "files": results,
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    ok = bool(results) and all(r.get("copied") for r in results)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
