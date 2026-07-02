# 微信聊天导出工具（wechat-export）

把 Mac 微信的群聊 / 私聊记录导出成排版好的 markdown 文件，聊天里的图片会自动还原。
全程只读你电脑上的微信本地数据，微信不用挂在线，也不上传任何数据到网上。

> 只支持 **macOS + Mac 微信 4.0 及以上**。Windows / 手机微信用不了。

---

## 一、这是什么

- 输入：一个群聊或联系人的名字
- 输出：一个 `.md` 文件（按天分段、显示发送人和时间、图片内嵌），可以直接丢进 Obsidian，也可以当普通文档看

---

## 二、安装（一次性，约 10 分钟）

### 1. 放好 skill 文件夹

把整个 `wechat-export` 文件夹放到下面任一位置：

- 用 **Claude Code**：`~/.claude/skills/wechat-export`
- 用 **Codex**：`~/.codex/skills/wechat-export`

（下文把这个路径记作 `<skill>`。）

### 2. 装几个基础工具

需要这几样，缺哪个装哪个：

- **Node.js** —— 去 https://nodejs.org 下载安装（用来跑 wechat-cli）
- **Homebrew** —— 去 https://brew.sh 按提示装（用来装 ffmpeg，只有导图片才需要）
- **Python 3** —— macOS 一般自带，命令行输 `python3 --version` 有版本号就行

### 3. 给终端「完全磁盘访问权限」

这是最容易漏的一步。微信数据受系统保护，必须授权：

1. 打开 **系统设置 → 隐私与安全性 → 完全磁盘访问权限**
2. 找到你平时用的终端（Terminal 或 iTerm 或 VS Code），打开它的开关
3. **完全退出该终端再重新打开**（很重要，不重开权限不生效）

### 4. 登录一次 Mac 微信

打开电脑版微信登录一次，让它把聊天数据同步到本地。之后导出时微信可以关掉。

### 5. 一键体检 + 自动补齐

打开终端，运行：

```bash
python3 <skill>/scripts/doctor.py --fix
```

它会自动帮你装好 wechat-cli、Python 加密库、ffmpeg，并提取密钥。
凡是它自己搞不定的（比如权限、登录微信），会打印出**具体怎么做**。
看到「环境就绪」就说明配好了。

---

## 三、使用

配好后，导出一个聊天只要一条命令：

```bash
python3 <skill>/scripts/wechat_export.py "群聊或联系人名字"
```

几个例子：

```bash
# 最近 3 个月（默认）
python3 <skill>/scripts/wechat_export.py "老同学群"

# 指定时间段
python3 <skill>/scripts/wechat_export.py "老同学群" --since 2026-01-01 --until 2026-03-31

# 全部历史
python3 <skill>/scripts/wechat_export.py "老同学群" --all

# 指定导出到哪
python3 <skill>/scripts/wechat_export.py "老同学群" --out ~/Desktop/我的导出
```

> 名字要和微信里显示的**完全一样**。记不清就先运行 `wechat-cli sessions --limit 30` 看最近会话列表。

导出的文件默认放在：
1. 你 `--out` 指定的目录，否则
2. 检测到的 Obsidian 仓库下的「微信记录」文件夹，否则
3. 桌面的「微信导出」文件夹

---

## 四、如果你用 Claude Code / Codex

不用记命令。直接对助手说：

> 帮我导出微信「老同学群」最近三个月的记录

助手会自己跑体检和导出。第一次它可能会提示你去做权限授权、登录微信这类只能人工完成的步骤。

---

## 五、出问题了看这里

| 现象 | 怎么办 |
| --- | --- |
| 提示读不到微信目录 / Permission | 完全磁盘访问权限没配好，重看第二步第 3 点，授权后**重开终端** |
| 找不到账号 | 电脑版微信登录一次 |
| 提示密钥不存在 | 跑 `python3 <skill>/scripts/doctor.py --fix` |
| 图片显示不出来 | 图片文件在成品旁边的 `img/` 文件夹里，用 Obsidian 打开能正常显示 |
| 某类图片（wxgf）缺失 | 装一下 ffmpeg：`brew install ffmpeg` |
| 找不到某个群 | 名字对不上，用 `wechat-cli sessions` 查准确名字 |

---

## 六、隐私说明

- 所有处理都在你自己电脑本地完成，不联网、不上传。
- 密钥保存在你本机 `~/.wechat-cli/`，导出的聊天记录只写到你指定的文件夹。
- 分享导出文件前请自行注意里面的隐私内容。
