# wechat-export

Claude Code skill：导出 Mac 微信聊天记录到 Obsidian，含图片解密和排版。

## 功能

- 通过 [wechat-cli](https://www.npmjs.com/package/@canghe_ai/wechat-cli) 导出聊天记录
- 自动解密 Mac 微信 4.0+ 的加密图片（V2 格式：AES-128-ECB + XOR）
- 密钥自动从 kvcomm 缓存推导，无需手动配置
- wxgf（微信 HEVC 容器）自动转 jpg
- 输出 Obsidian 友好的 markdown：按日期分节、图片嵌入、引用格式化、噪音清理

## 前置条件

```bash
# wechat-cli
npm i -g @canghe_ai/wechat-cli
wechat-cli init  # 提取数据库密钥

# Python 依赖
pip3 install pycryptodome

# 系统工具
brew install sqlcipher ffmpeg
```

- macOS 终端需要 **Full Disk Access**（系统设置 → 隐私与安全 → 完全磁盘访问权限）
- 微信聊天记录需要已同步到 Mac（手机端迁移或 Mac 端登录后自动同步）

## 安装

```bash
# 克隆到 Claude Code skills 目录
git clone https://github.com/duetzk/wechat-export.git ~/.claude/skills/wechat-export
```

## 使用

在 Claude Code 中：

```
/wechat-export 远明AI课堂
```

或者直接说"导出微信群聊 XXX 到 Obsidian"，skill 会自动触发。

### 手动使用脚本

```bash
# 1. 导出消息
wechat-cli export "群聊名" --format markdown --start-time "2026-01-01" --limit 10000 --output /tmp/raw.md

# 2. 获取 chatroom username
wechat-cli history "群聊名" --limit 1
# 从输出中取 username 字段

# 3. 图片解密 + 嵌入
python3 export_images.py \
  --input /tmp/raw.md \
  --username "123456789@chatroom" \
  --output ~/obsidian/微信记录/群聊名.md

# 4. 格式化
python3 format_chat.py \
  --input ~/obsidian/微信记录/群聊名.md \
  --output ~/obsidian/微信记录/群聊名.md
```

## 技术细节

### 图片映射链

```
message_0.db → Msg_{md5(username)} 表 → local_type=3
  → packed_info_data (protobuf) → 扫描 32 字节 hex → 文件 MD5
  → attach/{md5(username)}/{YYYY-MM}/Img/{md5}[_h|_t].dat
  → V2 解密 (AES-128-ECB + XOR)
```

### 数据库解密

Mac 微信 4.0+ 的 message_*.db 使用 SQLCipher 4 自定义参数（AES-256-CBC, HMAC-SHA512, reserve=80），不能用 sqlcipher CLI 直接打开，需逐页解密。

### 图片密钥

从 `~/Library/.../app_data/net/kvcomm/key_<uin>_*.statistic` 文件名提取 uin：

```
aes_key = MD5(str(uin) + wxid)[:16]
xor_key = uin & 0xFF
```

### V2 图片格式

```
[6B 签名: 07 08 V2 08 07] [4B aes_size LE] [4B xor_size LE] [1B padding]
[AES-ECB 加密段] [明文段] [XOR 加密段]
```

### wxgf 格式

微信 HEVC 容器，跳过头部找 NAL start code `\x00\x00\x00\x01` 后用 ffmpeg 转 jpg。

## 文件说明

| 文件 | 说明 |
|------|------|
| `SKILL.md` | Claude Code skill 定义 |
| `export_images.py` | 图片解密主脚本：DB 解密 → MD5 映射 → .dat 解密 → markdown 嵌入 |
| `format_chat.py` | markdown 格式化：日期分节、清理噪音、Obsidian 排版 |

## 致谢

- [wechat-cli](https://www.npmjs.com/package/@canghe_ai/wechat-cli) - 微信消息导出
- [ylytdeng/wechat-decrypt](https://github.com/ylytdeng/wechat-decrypt) - 图片密钥推导算法参考

## License

MIT
