---
name: wechat-export
description: 导出 Mac 微信群聊/私聊记录为格式化 markdown（含图片解密），可放入 Obsidian 或任意文件夹。当用户要求导出微信聊天记录、归档微信群聊、或提到"微信导出""wechat export"时触发。仅支持 macOS + Mac 微信 4.0+。
arguments: [chat_name]
argument-hint: [群聊或联系人名称]
allowed-tools: Bash Read Write Edit Glob Grep
metadata:
  short-description: 导出微信聊天到 markdown
---

# wechat-export

把 Mac 微信的群聊/私聊记录导出成排版好的 markdown，图片自动解密还原。全程读本地数据库，微信不用在线。

> `<skill_dir>` = 本 skill 所在目录。Claude Code 下通常 `~/.claude/skills/wechat-export`，Codex 下 `~/.codex/skills/wechat-export`。下面命令里把它替换成实际路径（或先 `cd` 进去）。

## 第 0 步：环境体检（首次使用必做）

先跑体检，它会逐项检查并告诉你缺什么、怎么补：

```bash
python3 <skill_dir>/scripts/doctor.py          # 只检查
python3 <skill_dir>/scripts/doctor.py --fix    # 检查 + 自动装能自动装的（pip/npm/密钥）
```

体检通过再往下走。它把「能自动装的」和「只能你手动做的」分开了：

- **能自动装**（加 `--fix`）：pycryptodome、wechat-cli（需已装 Node）、ffmpeg（需已装 Homebrew）、`wechat-cli init` 提取密钥
- **只能手动做**：
  1. 给终端授权 **完全磁盘访问权限**：系统设置 → 隐私与安全性 → 完全磁盘访问权限 → 打开你用的终端（Terminal / iTerm / VS Code），然后**完全退出终端再重开**
  2. 装并**登录一次 Mac 微信**（4.0+）
  3. 没装 Node 的先去 https://nodejs.org 装；没装 Homebrew 且要导图片的去 https://brew.sh 装

## 第 1 步：一键导出（推荐）

体检通过后，一条命令跑完全程（导出 → 图片解密 → 格式化）：

```bash
python3 <skill_dir>/scripts/wechat_export.py "群聊或联系人名称"
```

常用参数：

```bash
# 指定时间范围
python3 <skill_dir>/scripts/wechat_export.py "AI交流群" --since 2026-01-01 --until 2026-03-31
# 全部历史
python3 <skill_dir>/scripts/wechat_export.py "AI交流群" --all
# 指定输出目录
python3 <skill_dir>/scripts/wechat_export.py "AI交流群" --out ~/Desktop/导出
```

不加 `--since` 默认最近 90 天。**聊天名称必须和微信里显示的完全一致**，不确定就先 `wechat-cli sessions --limit 30` 或 `wechat-cli contacts --query "关键词"` 查。

### 输出去哪

编排脚本自动决定输出目录，优先级：

1. `--out` 指定的
2. 环境变量 `WECHAT_EXPORT_DIR`
3. 检测到的 Obsidian vault 下的 `微信记录/`（iCloud 路径，若存在）
4. 兜底 `~/Desktop/微信导出/`

成品是 `<输出目录>/{聊天名}.md`，图片在同级 `img/`。

## 第 2 步（可选）：分步执行

一键脚本内部就是下面三步，需要单独调试某一步时用：

```bash
# a. 导出原始 markdown
wechat-cli export "群名" --format markdown --start-time 2026-01-01 --limit 20000 --output /tmp/raw.md
# b. 拿 username（取 JSON 里的 username 字段，如 123@chatroom）
wechat-cli history "群名" --limit 1
# c. 图片解密
python3 <skill_dir>/scripts/export_images.py --input /tmp/raw.md --username "123@chatroom" --output /tmp/withimg.md
# d. 格式化
python3 <skill_dir>/scripts/format_chat.py --input /tmp/withimg.md --output ~/out/群名.md --title "群名"
```

格式化规则：按日期分节、`**发送者** 时间`、图片独占一行、引用回复用 `> _被引用者: 原文_`、清理拍了拍/红包/XML 残片、语音视频保留时长。

## 常见报错对照

| 现象 | 原因 | 处理 |
| --- | --- | --- |
| 读不到微信目录 / PermissionError | 终端没有完全磁盘访问权限 | 授权后**完全退出终端重开** |
| 找不到 wxid 账号 | 微信没登录过 | 打开并登录一次 Mac 微信 |
| 密钥文件不存在 | 没跑 init | `wechat-cli init` 或 `doctor.py --fix` |
| 图片全部「无文件」 | username 传错 | 用 `history --limit 1` 重新确认 |
| wxgf 图片解密失败 | 缺 ffmpeg | `brew install ffmpeg`（只影响这类图） |
| 查不到聊天 | 名称对不上 | `wechat-cli sessions` / `contacts` 查准确名字 |

## 技术参考（排障用）

图片映射链（不要用 hardlink.db 的 _rowid_）：

```
message_0.db → Msg_{md5(username)} 表 → local_type=3 的行
  → packed_info_data (protobuf blob) 扫 32 字节 hex → 文件 MD5
  → attach/{md5(username)}/{YYYY-MM}/Img/{md5}[_h|_t].dat
  → V2 解密 (AES-128-ECB + XOR)
```

- **数据库解密**：message_*.db 是 SQLCipher 4 自定义参数，不能用 sqlcipher CLI 直接开，脚本用 Python 逐页解密（AES-256-CBC / HMAC-SHA512 / reserve=80 / page=4096）。密钥在 `~/.wechat-cli/all_keys.json`。
- **图片密钥**（与库密钥不同）：从 `app_data/net/kvcomm/key_<uin>_*.statistic` 推导，`aes_key = MD5(str(uin)+wxid)[:16]`，`xor_key = uin & 0xFF`。`export_images.py` 的 `find_image_keys()` 自动推导，无需手配。
- **wxgf**：微信 HEVC 容器，跳到 NAL start code `\x00\x00\x00\x01` 后用 ffmpeg 转 jpg。
- 多 wxid 目录时取 `~/.wechat-cli/config.json` 的 `db_dir` 对应那个。
- sqlcipher **不是**必需依赖（脚本纯 Python 解密），无需安装。
