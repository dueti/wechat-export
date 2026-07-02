"""
wechat-export 环境体检 + 自动修复。

首次在别人电脑上跑这个脚本，它会逐项检查依赖并告诉你缺什么、怎么补。
能自动装的（pip / npm 包、wechat-cli init）加 --fix 就自动装；
只能人工做的（授权 Full Disk Access、登录微信、brew 装 ffmpeg）会打印明确指引。

用法：
  python3 doctor.py            # 只检查，报告哪里没配好
  python3 doctor.py --fix      # 检查 + 自动安装能自动装的部分
"""
import argparse
import json
import os
import shutil
import subprocess
import sys

WECHAT_BASE = os.path.expanduser(
    "~/Library/Containers/com.tencent.xinWeChat/Data/Documents/xwechat_files"
)
KEYS_FILE = os.path.expanduser("~/.wechat-cli/all_keys.json")
CONFIG_FILE = os.path.expanduser("~/.wechat-cli/config.json")

OK = "\033[32m✓\033[0m"
BAD = "\033[31m✗\033[0m"
WARN = "\033[33m!\033[0m"


def run(cmd):
    return subprocess.run(cmd, capture_output=True, text=True)


def has(bin_name):
    return shutil.which(bin_name) is not None


def check_platform():
    if sys.platform != "darwin":
        return False, "仅支持 macOS", "这个 skill 依赖 Mac 微信的本地数据库，Windows/Linux 用不了"
    return True, "macOS", None


def check_python_crypto(fix):
    try:
        import Crypto  # noqa: F401
        return True, f"pycryptodome {Crypto.__version__}", None
    except ImportError:
        if fix:
            print("  安装 pycryptodome ...")
            r = run([sys.executable, "-m", "pip", "install", "--user", "pycryptodome"])
            if r.returncode == 0:
                return True, "pycryptodome 已安装", None
            return False, "pycryptodome 安装失败", r.stderr.strip()[:200]
        return False, "pycryptodome 未安装", "pip3 install pycryptodome（或加 --fix 自动装）"


def check_ffmpeg(fix):
    if has("ffmpeg"):
        return True, "ffmpeg 已安装", None
    if fix and has("brew"):
        print("  brew install ffmpeg ...（可能较慢）")
        r = run(["brew", "install", "ffmpeg"])
        if r.returncode == 0 and has("ffmpeg"):
            return True, "ffmpeg 已安装", None
    return False, "ffmpeg 未安装", "brew install ffmpeg（wxgf 格式图片需要，缺了只影响这类图）"


def check_node_cli(fix):
    if has("wechat-cli"):
        r = run(["wechat-cli", "--version"])
        return True, r.stdout.strip() or "wechat-cli 已安装", None
    if not has("npm"):
        return False, "wechat-cli 未安装", "先装 Node.js（https://nodejs.org），再 npm i -g @canghe_ai/wechat-cli"
    if fix:
        print("  npm i -g @canghe_ai/wechat-cli ...")
        r = run(["npm", "i", "-g", "@canghe_ai/wechat-cli"])
        if r.returncode == 0 and has("wechat-cli"):
            return True, "wechat-cli 已安装", None
        return False, "wechat-cli 安装失败", r.stderr.strip()[:200]
    return False, "wechat-cli 未安装", "npm i -g @canghe_ai/wechat-cli（或加 --fix 自动装）"


def check_full_disk_access():
    """尝试读取微信容器目录，读不到通常是没给终端 Full Disk Access。"""
    parent = os.path.dirname(WECHAT_BASE)
    if not os.path.exists(parent):
        return None, "微信数据目录不存在", (
            "要么没装 Mac 微信，要么微信从没登录过。"
            "请安装并登录一次 Mac 微信（需 4.0+ 版本）"
        )
    try:
        os.listdir(parent)
    except PermissionError:
        return False, "读不到微信目录（Full Disk Access 未授权）", (
            "打开 系统设置 → 隐私与安全性 → 完全磁盘访问权限，"
            "把你运行命令的终端（Terminal / iTerm / VS Code）打开开关，然后完全退出终端重开"
        )
    return True, "可读取微信目录", None


def check_wechat_logged_in():
    if not os.path.isdir(WECHAT_BASE):
        return False, "找不到已登录的微信账号", "打开 Mac 微信并登录一次"
    wxids = [d for d in os.listdir(WECHAT_BASE) if d.startswith("wxid_")]
    if not wxids:
        return False, "没有找到 wxid 账号目录", "打开 Mac 微信并登录一次"
    return True, f"检测到 {len(wxids)} 个账号目录", None


def check_keys(fix):
    if os.path.exists(KEYS_FILE) and os.path.exists(CONFIG_FILE):
        try:
            with open(KEYS_FILE) as f:
                keys = json.load(f)
            if keys:
                return True, f"密钥已提取（{len(keys)} 个库）", None
        except (json.JSONDecodeError, OSError):
            pass
    if fix and has("wechat-cli"):
        print("  wechat-cli init（提取密钥）...")
        r = run(["wechat-cli", "init", "--force"])
        if r.returncode == 0 and os.path.exists(KEYS_FILE):
            return True, "密钥已提取", None
        return False, "wechat-cli init 失败", (r.stderr or r.stdout).strip()[:200]
    return False, "密钥未提取", "运行 wechat-cli init（或加 --fix 自动跑）"


def main():
    ap = argparse.ArgumentParser(description="wechat-export 环境体检")
    ap.add_argument("--fix", action="store_true", help="自动安装能自动装的依赖")
    args = ap.parse_args()

    print("\n=== wechat-export 环境体检 ===\n")

    # 有先后依赖：平台 → 依赖 → 权限 → 微信登录 → 密钥
    steps = [
        ("运行平台", lambda: check_platform()),
        ("Python 加密库", lambda: check_python_crypto(args.fix)),
        ("ffmpeg", lambda: check_ffmpeg(args.fix)),
        ("wechat-cli", lambda: check_node_cli(args.fix)),
        ("磁盘访问权限", lambda: check_full_disk_access()),
        ("微信已登录", lambda: check_wechat_logged_in()),
        ("密钥提取", lambda: check_keys(args.fix)),
    ]

    blocking = []
    warnings = []
    for name, fn in steps:
        ok, msg, hint = fn()
        if ok is True:
            print(f"  {OK} {name}: {msg}")
        elif ok is None:
            print(f"  {WARN} {name}: {msg}")
            if hint:
                print(f"      → {hint}")
            warnings.append(name)
        else:
            # ffmpeg 只影响 wxgf 图片，算警告不算阻塞
            mark = WARN if name == "ffmpeg" else BAD
            print(f"  {mark} {name}: {msg}")
            if hint:
                print(f"      → {hint}")
            (warnings if name == "ffmpeg" else blocking).append(name)

    print()
    if not blocking:
        print(f"{OK} 环境就绪，可以导出了。")
        if warnings:
            print(f"{WARN} 次要提醒：{', '.join(warnings)}（不影响文字导出）")
        print('\n下一步：python3 wechat_export.py "群聊或联系人名称"')
        sys.exit(0)
    else:
        print(f"{BAD} 还差：{', '.join(blocking)}")
        if not args.fix:
            print("  提示：pip/npm/密钥 这几项可以用 `python3 doctor.py --fix` 自动补。")
        print("  权限和登录微信只能你手动做，按上面 → 的指引操作。")
        sys.exit(1)


if __name__ == "__main__":
    main()
