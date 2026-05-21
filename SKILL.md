---
name: wechat-export
description: 导出微信群聊/私聊记录到 Obsidian，含图片解密和排版。当用户要求导出微信聊天记录、归档微信群聊、或提到"微信导出"时触发。
arguments: [chat_name]
argument-hint: [群聊或联系人名称]
allowed-tools: Bash Read Write Edit Glob Grep
---

# wechat-export

将微信聊天记录导出为格式化的 Obsidian markdown，包含图片解密。

## 前置条件

- `wechat-cli` 已安装（`npm i -g @canghe_ai/wechat-cli`）
- 密钥已提取（`~/.wechat-cli/all_keys.json`）
- 终端有 Full Disk Access
- `pycryptodome` 已安装（`pip3 install pycryptodome`）
- `sqlcipher` 已安装（`brew install sqlcipher`）
- `ffmpeg` 已安装（转换 wxgf 格式图片）

## 执行步骤

收到参数 `$chat_name` 后，按以下流程执行：

### 第 1 步：导出消息

```bash
# 计算 3 个月前的日期作为默认起始时间
START_DATE=$(python3 -c "from datetime import datetime,timedelta; print((datetime.now()-timedelta(days=90)).strftime('%Y-%m-%d'))")
wechat-cli export "$chat_name" --format markdown --start-time "$START_DATE" --limit 10000 --output /tmp/wechat_export_raw.md
```

如果用户指定了时间范围，替换 `--start-time` 和添加 `--end-time`。
如果用户说"全部"或"所有"，用 `--start-time "2020-01-01"` 覆盖全量。
如果用户没指定时间，默认最近 3 个月。

### 第 2 步：获取群聊 username

```bash
wechat-cli history "$chat_name" --limit 1
```

从 JSON 输出中取 `username` 字段（如 `123456789@chatroom`）。

### 第 3 步：图片处理

调用图片处理脚本：

```bash
python3 ~/.claude/skills/wechat-export/export_images.py \
  --input /tmp/wechat_export_raw.md \
  --username "<上一步取到的 username>" \
  --output "<obsidian 输出路径>"
```

脚本会：
1. 解密 message_0.db（或 message_1.db），查 `Msg_{md5(username)}` 表
2. 从 `packed_info_data` 提取文件 MD5
3. 在 `attach/{md5(username)}/` 下找 .dat 文件
4. 用 AES-128-ECB + XOR 解密图片（密钥自动从 kvcomm 推导）
5. wxgf 格式自动转 jpg（ffmpeg）

### 第 4 步：格式化 markdown

调用格式化脚本：

```bash
python3 ~/.claude/skills/wechat-export/format_chat.py \
  --input "<上一步输出的 markdown>" \
  --output "<最终输出路径>"
```

格式化规则：
- 按日期分节（`## 2026-04-04 周六`）
- 消息格式：`**发送者** 时间` + 正文
- 图片独占一行（Obsidian 才能渲染）
- 引用回复：`> _被引用者: 原文_`
- 清理：拍了拍、红包、XML 残片、语音/视频提取时长

### 第 5 步：输出

最终文件放到 Obsidian 目录：
`~/Library/Mobile Documents/com~apple~CloudDocs/Obsidian/<vault>/微信记录/{chat_name}.md`
图片放到同目录下的 `img/` 子目录。

## 技术参考

### 图片映射链（重要，不要用 hardlink.db 的 _rowid_）

```
message_0.db → Msg_{md5(username)} 表 → local_type=3 的行
  → packed_info_data (protobuf blob) 扫描 32 字节 hex → 文件 MD5
  → attach/{md5(username)}/{YYYY-MM}/Img/{md5}[_h|_t].dat
  → V2 解密 (AES-128-ECB + XOR)
```

### 数据库解密

message_*.db 使用 SQLCipher 4 自定义参数，**不能**用 sqlcipher CLI 直接打开。
必须用 Python 逐页解密（AES-256-CBC, HMAC-SHA512, reserve=80, page_size=4096）。
密钥在 `~/.wechat-cli/all_keys.json`。

hardlink.db **可以**用 sqlcipher CLI 打开（PRAGMA cipher_page_size=4096, kdf_iter=256000）。

### 图片密钥推导

```
kvcomm 缓存: ~/Library/.../app_data/net/kvcomm/key_<uin>_*.statistic
aes_key = MD5(str(uin) + wxid)[:16]
xor_key = uin & 0xFF
```

`export_images.py` 中的 `find_image_keys()` 自动推导，无需手动配置。

### 微信数据路径

```
~/Library/Containers/com.tencent.xinWeChat/Data/Documents/
  xwechat_files/<your_wxid>/
    db_storage/message/message_0.db   # 消息数据库（加密）
    msg/attach/{hash}/{YYYY-MM}/Img/  # 图片 .dat 文件
```

## 注意事项

- 图片解密密钥与数据库密钥不同，图片密钥从 kvcomm 推导
- wxgf 是微信 HEVC 容器格式，跳过头部找 NAL start code `\x00\x00\x00\x01` 后用 ffmpeg 转 jpg
- message_resource.db HMAC 验证可能失败，直接用 message_0.db 的 packed_info_data 字段
- 多个 wxid 目录时，取 `~/.wechat-cli/config.json` 中 `db_dir` 对应的那个
