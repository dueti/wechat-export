"""
微信聊天记录 markdown 格式化。

将 wechat-cli 导出的原始 markdown 转换为 Obsidian 友好的格式：
- 按日期分节（## YYYY-MM-DD 周X）
- 消息：**发送者** `时间` + 正文
- 图片独占一行
- 引用回复：> _被引用者: 原文_
- 清理：拍了拍、红包、XML 残片、语音/视频提取时长

用法：
  python3 format_chat.py --input raw.md --output formatted.md [--title "群聊名"]
"""
import argparse
import os
import re
import sys
from datetime import datetime


def parse_messages(body):
    """解析消息体，返回结构化消息列表"""
    lines = body.split("\n")
    messages = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if not line.strip():
            i += 1
            continue

        m = re.match(r"^- \[(\d{4}-\d{2}-\d{2}) (\d{2}:\d{2})\] (.+)$", line)
        if not m:
            i += 1
            continue

        date, time, rest = m.group(1), m.group(2), m.group(3)

        # 收集续行
        content_lines = [rest]
        while i + 1 < len(lines):
            next_line = lines[i + 1]
            if next_line.startswith("- [") or next_line.strip().startswith("↳"):
                break
            if not next_line.strip():
                break
            content_lines.append(next_line)
            i += 1

        # 收集回复行
        reply_to = None
        if i + 1 < len(lines) and lines[i + 1].strip().startswith("↳"):
            reply_line = lines[i + 1].strip()
            rm = re.match(r"↳ 回复 (.+?):\s*(.+)", reply_line)
            if rm:
                reply_to = rm.group(1)
                reply_content = rm.group(2)
                # 清理 XML
                if reply_content.startswith("<msg>") or reply_content.startswith("<?xml"):
                    tm = re.search(r"<title>(.*?)</title>", reply_content)
                    reply_content = tm.group(1) if tm else ""
                if reply_content:
                    truncated = reply_content[:80]
                    if len(reply_content) > 80:
                        truncated += "..."
                    reply_to = f"{reply_to}: {truncated}"
            i += 1

        full_content = "\n".join(content_lines)

        # 分离 sender 和 content
        sm = re.match(r"^(.+?):\s+(.+)$", full_content, re.DOTALL)
        if sm and not full_content.startswith("["):
            sender = sm.group(1)
            content = sm.group(2)
        else:
            sender = None
            content = full_content

        messages.append({
            "date": date,
            "time": time,
            "sender": sender,
            "content": content,
            "reply_to": reply_to,
        })
        i += 1

    return messages


def filter_messages(messages):
    """过滤噪音消息，清理 XML 残片"""
    filtered = []
    for msg in messages:
        c = msg["content"]
        # 跳过拍了拍
        if "[链接/文件]" in c and "拍了拍" in c:
            continue
        # 跳过系统 XML
        if c.startswith("[系统] <?xml"):
            continue
        # 跳过红包
        if "[链接/文件] 微信红包" in c:
            continue

        # 清理回复中的 XML
        if msg["reply_to"]:
            rt = msg["reply_to"]
            if "<msg>" in rt or "<?xml" in rt:
                tm = re.search(r"<title>(.*?)</title>", rt)
                sender_part = rt.split(":")[0]
                msg["reply_to"] = f"{sender_part}: {tm.group(1)[:80]}" if tm else sender_part

        # 清理语音消息 XML
        vm = re.search(r"\[语音\]\s*<msg>.*?voicelength=\"(\d+)\"", c, re.DOTALL)
        if vm:
            secs = int(vm.group(1)) // 1000
            msg["content"] = f"[语音 {secs}s]"

        # 清理视频消息 XML
        vm = re.search(r"\[视频\]\s*<\?xml.*?playlength=\"(\d+)\"", c, re.DOTALL)
        if vm:
            msg["content"] = f"[视频 {int(vm.group(1))}s]"

        # 清理正文中 XML 引用残片
        if "wxid_" in c and "<?xml" in c:
            tm = re.search(r"<title>(.*?)</title>", c)
            if tm:
                msg["content"] = tm.group(1)

        filtered.append(msg)
    return filtered


def render_markdown(messages, title=None):
    """渲染为 Obsidian 友好的 markdown"""
    out = []

    # Header
    if title:
        out.append(f"# {title}\n")
    else:
        out.append("# 聊天记录\n")

    if messages:
        first_date = messages[0]["date"]
        last_date = messages[-1]["date"]
        out.append(f"> {first_date} ~ {last_date} | {len(messages)} 条消息\n")

    out.append("---\n")

    weekdays = ["一", "二", "三", "四", "五", "六", "日"]
    current_date = None

    for msg in messages:
        # 日期分隔
        if msg["date"] != current_date:
            current_date = msg["date"]
            dt = datetime.strptime(current_date, "%Y-%m-%d")
            wd = weekdays[dt.weekday()]
            out.append(f"\n## {current_date} 周{wd}\n")

        sender = msg["sender"] or ""
        content = msg["content"]
        time = msg["time"]
        is_image = "![[img/" in content

        if is_image and content.strip().startswith("![[img/"):
            # 图片独占一行
            if msg["reply_to"]:
                out.append(f"**{sender}** `{time}` > _{msg['reply_to']}_\n")
            else:
                out.append(f"**{sender}** `{time}`\n")
            out.append(f"{content.strip()}\n")
        elif sender:
            reply_prefix = f" > _{msg['reply_to']}_" if msg["reply_to"] else ""
            content_flat = content.replace("\n", " ") if "\n" in content else content
            out.append(f"**{sender}** `{time}`{reply_prefix}  \n{content_flat}\n")
        else:
            out.append(f"_{content}_ `{time}`\n")

    return "\n".join(out)


def main():
    parser = argparse.ArgumentParser(description="微信聊天 markdown 格式化")
    parser.add_argument("--input", required=True, help="输入 markdown 文件")
    parser.add_argument("--output", required=True, help="输出 markdown 文件")
    parser.add_argument("--title", default=None, help="聊天标题（默认从文件提取）")
    args = parser.parse_args()

    with open(args.input) as f:
        raw = f.read()

    # 分离 header 和 body
    header_end = raw.find("---\n")
    if header_end >= 0:
        body = raw[header_end + 4:]
        # 从 header 提取标题
        if not args.title:
            tm = re.search(r"^# .+[:：]\s*(.+)$", raw[:header_end], re.MULTILINE)
            if tm:
                args.title = tm.group(1).strip() + " 群聊记录"
    else:
        body = raw

    messages = parse_messages(body)
    messages = filter_messages(messages)
    result = render_markdown(messages, title=args.title)

    os.makedirs(os.path.dirname(args.output) or ".", exist_ok=True)
    with open(args.output, "w") as f:
        f.write(result)

    print(f"格式化完成: {len(messages)} 条消息 → {args.output}")


if __name__ == "__main__":
    main()
