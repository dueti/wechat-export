"""
一条命令跑完微信导出全程：导出 → 图片解密 → 格式化。

用法：
  python3 wechat_export.py "群聊名"
  python3 wechat_export.py "群聊名" --since 2026-01-01 --until 2026-03-31
  python3 wechat_export.py "群聊名" --all                 # 全量
  python3 wechat_export.py "群聊名" --out ~/Desktop/导出   # 指定输出目录

输出目录优先级：
  1. --out 指定的目录
  2. 环境变量 WECHAT_EXPORT_DIR
  3. 检测到的 Obsidian vault 下的「微信记录/」（若存在）
  4. 兜底：~/Desktop/微信导出/
"""
import argparse
import glob
import os
import re
import subprocess
import sys
from datetime import datetime, timedelta

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))


def die(msg):
    print(f"错误：{msg}", file=sys.stderr)
    sys.exit(1)


def safe_name(name):
    return re.sub(r'[/\\:*?"<>|]', "_", name).strip()


def detect_output_dir():
    env = os.environ.get("WECHAT_EXPORT_DIR")
    if env:
        return os.path.expanduser(env)
    # 探测 Obsidian vault（iCloud 常见路径）
    vault_glob = os.path.expanduser(
        "~/Library/Mobile Documents/com~apple~CloudDocs/Obsidian/*/"
    )
    vaults = sorted(glob.glob(vault_glob))
    if vaults:
        return os.path.join(vaults[0], "微信记录")
    return os.path.expanduser("~/Desktop/微信导出")


def get_username(chat_name):
    """用 history --limit 1 拿聊天的 username（xxx@chatroom 或 wxid_xxx）。"""
    r = subprocess.run(
        ["wechat-cli", "history", chat_name, "--limit", "1"],
        capture_output=True, text=True,
    )
    if r.returncode != 0:
        die(f'查不到聊天「{chat_name}」：{(r.stderr or r.stdout).strip()[:200]}')
    import json
    try:
        data = json.loads(r.stdout)
    except json.JSONDecodeError:
        die("wechat-cli history 输出不是 JSON，版本可能不兼容")
    # 兼容多种结构：list 或 {messages:[...]} 或 {username:...}
    if isinstance(data, dict):
        if data.get("username"):
            return data["username"]
        msgs = data.get("messages") or data.get("data") or []
    else:
        msgs = data
    if msgs and isinstance(msgs, list):
        first = msgs[0]
        if isinstance(first, dict):
            u = first.get("username") or first.get("talker") or first.get("chat_username")
            if u:
                return u
    die(f'拿不到「{chat_name}」的 username，请确认名称与微信里完全一致')


def main():
    ap = argparse.ArgumentParser(description="微信聊天一键导出到 markdown（含图片）")
    ap.add_argument("chat", help="群聊或联系人名称（与微信里显示的一致）")
    ap.add_argument("--since", help="起始日期 YYYY-MM-DD（默认最近 90 天）")
    ap.add_argument("--until", help="结束日期 YYYY-MM-DD")
    ap.add_argument("--all", action="store_true", help="导出全部历史")
    ap.add_argument("--out", help="输出目录（覆盖自动探测）")
    ap.add_argument("--limit", default="20000", help="最大条数（默认 20000）")
    args = ap.parse_args()

    if not subprocess.run(["which", "wechat-cli"], capture_output=True).returncode == 0:
        die("wechat-cli 未安装，先跑 python3 doctor.py --fix")

    # 起始时间
    if args.all:
        since = "2015-01-01"
    elif args.since:
        since = args.since
    else:
        since = (datetime.now() - timedelta(days=90)).strftime("%Y-%m-%d")

    out_dir = os.path.expanduser(args.out) if args.out else detect_output_dir()
    os.makedirs(out_dir, exist_ok=True)
    name = safe_name(args.chat)

    raw = os.path.join(out_dir, f".{name}.raw.md")       # wechat-cli 原始输出
    withimg = os.path.join(out_dir, f".{name}.img.md")   # 图片替换后
    final = os.path.join(out_dir, f"{name}.md")          # 最终成品

    # 1) 导出原始 markdown
    print(f"[1/4] 导出「{args.chat}」（{since} 起）...")
    cmd = ["wechat-cli", "export", args.chat, "--format", "markdown",
           "--start-time", since, "--limit", args.limit, "--output", raw]
    if args.until:
        cmd += ["--end-time", args.until]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0 or not os.path.exists(raw):
        die(f"导出失败：{(r.stderr or r.stdout).strip()[:300]}")

    # 2) 拿 username
    print("[2/4] 定位聊天 username ...")
    username = get_username(args.chat)
    print(f"      username = {username}")

    # 3) 图片解密
    print("[3/4] 解密图片 ...")
    r = subprocess.run(
        [sys.executable, os.path.join(SCRIPT_DIR, "export_images.py"),
         "--input", raw, "--username", username, "--output", withimg],
        text=True,
    )
    if r.returncode != 0 or not os.path.exists(withimg):
        die("图片处理失败（若只想要文字，可忽略图片重跑）")

    # 4) 格式化
    print("[4/4] 格式化 ...")
    r = subprocess.run(
        [sys.executable, os.path.join(SCRIPT_DIR, "format_chat.py"),
         "--input", withimg, "--output", final, "--title", args.chat],
        text=True,
    )
    if r.returncode != 0 or not os.path.exists(final):
        die("格式化失败")

    # 清理中间文件
    for tmp in (raw, withimg):
        try:
            os.remove(tmp)
        except OSError:
            pass

    print(f"\n完成 → {final}")
    print(f"图片目录 → {os.path.join(out_dir, 'img')}")


if __name__ == "__main__":
    main()
