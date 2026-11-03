#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把「语音整理/」里人工整理的语音转文字，回填进会被导出脚本覆盖的聊天记录文件。

（wechat-export 的收尾步骤。wechat_export.py 导出后若输出目录下有「语音整理/」会自动调用；
也可单独跑。原 ~/scripts/wechat-voice-backfill.py 已并入此处。）

背景：每日/月度聊天记录由 wechat-export 从微信数据库重新导出，语音只会是
`[语音]` 或 `[语音 50s]` 占位。人工整理的转文字存在「语音整理/」目录里，
那里没有任何脚本会写。本脚本把权威内容注入可再生的导出产物。

幂等：已回填过的段落（含「·微信转文字」标记）会跳过，可重复运行。
匹配：按「发送人 + HH:MM」定位占位；同一分钟多条语音按出现先后依次消费。

用法：
    python3 voice_backfill.py <群目录> [<群目录> ...]

群目录需符合 wechat-export 结构：
    <群目录>/语音整理/*.md      权威源
    <群目录>/每日/YYYY-MM-DD.md 回填目标
    <群目录>/YYYY-MM.md         回填目标
    <群目录>/<任意名>.md        也会回填（wechat_export.py 一键导出的成品）
"""
import io
import os
import re
import sys

MARK = "·微信转文字"

# 「语音整理」页里的一段：## 语音 N：标题 / **语音信息**：09:38:49 · 50.52 秒 / 正文
# 切段用任意二级标题，否则最后一条语音会把文末的「核心观点」等小节一并吸进正文
RE_SECTION = re.compile(r"^##\s+", re.M)
RE_VOICE_HEAD = re.compile(r"^##\s+语音\s*\d+[：:]")
RE_VOICE_INFO = re.compile(r"^\*\*语音信息\*\*[：:]\s*(\d{2}):(\d{2}):\d{2}\s*·\s*([\d.]+)\s*秒")
RE_SPEAKER = re.compile(r"^speaker:\s*(.+?)\s*$", re.M)
RE_DATE = re.compile(r"^date:\s*(\d{4}-\d{2}-\d{2})\s*$", re.M)

# 目标文件里的占位：**老薛** `09:38`  换行  [语音] 或 [语音 50s]
RE_MSG_HEAD = re.compile(r"^\*\*(.+?)\*\*\s+`(\d{2}):(\d{2})`")
RE_PLACEHOLDER = re.compile(r"^\[语音(?:\s+(\d+)s)?\]$")


def parse_voice_page(path):
    """解析一个语音整理页 → (date, [ {speaker, hhmm, secs, text}, ... ])"""
    text = io.open(path, encoding="utf-8").read()

    m_date = RE_DATE.search(text)
    m_speaker = RE_SPEAKER.search(text)
    if not m_date or not m_speaker:
        return None, []
    date, speaker = m_date.group(1), m_speaker.group(1)

    entries = []
    # 按所有二级标题切段，再只取「## 语音 N：」开头的段
    starts = [m.start() for m in RE_SECTION.finditer(text)]
    for i, s in enumerate(starts):
        end = starts[i + 1] if i + 1 < len(starts) else len(text)
        block = text[s:end]
        if not RE_VOICE_HEAD.match(block):
            continue

        m_info = None
        for line in block.split("\n"):
            m_info = RE_VOICE_INFO.match(line.strip())
            if m_info:
                break
        if not m_info:
            continue
        hhmm = "%s:%s" % (m_info.group(1), m_info.group(2))
        # 截断而非四舍五入，与 wechat-export 生成的 `[语音 50s]` 保持一致
        # （微信界面显示的是四舍五入值，两者会差 1 秒）
        secs = int(float(m_info.group(3)))

        # 正文：跳过标题行、**语音信息**、**文字来源** 等元信息行
        body = []
        for line in block.split("\n"):
            st = line.strip()
            if not st:
                continue
            if st.startswith("##") or st.startswith("**语音信息**") \
               or st.startswith("**文字来源**") or st.startswith("**高置信度对应文字**"):
                continue
            body.append(st)
        if not body:
            continue
        entries.append({
            "speaker": speaker,
            "hhmm": hhmm,
            "secs": secs,
            "text": "".join(body),   # 多段合成一段：聊天记录里空行代表新消息
        })
    return date, entries


def backfill(target, entries):
    """把 entries 回填进 target 文件，返回 (替换数, 跳过数, 未匹配列表)"""
    if not os.path.isfile(target):
        return 0, 0, []

    lines = io.open(target, encoding="utf-8").read().split("\n")

    # 每个 (speaker, hhmm) 一个待消费队列，按整理页顺序
    queues = {}
    for e in entries:
        queues.setdefault((e["speaker"], e["hhmm"]), []).append(e)

    replaced = skipped = 0
    for i, line in enumerate(lines):
        m_head = RE_MSG_HEAD.match(line)
        if not m_head or i + 1 >= len(lines):
            continue
        key = (m_head.group(1), "%s:%s" % (m_head.group(2), m_head.group(3)))
        nxt = lines[i + 1].strip()

        if MARK in nxt:            # 已回填过
            if queues.get(key):
                queues[key].pop(0)
                skipped += 1
            continue

        m_ph = RE_PLACEHOLDER.match(nxt)
        if not m_ph or not queues.get(key):
            continue

        e = queues[key].pop(0)
        secs = m_ph.group(1) or str(e["secs"])
        lines[i + 1] = "[语音 %ss%s]\n%s" % (secs, MARK, e["text"])
        replaced += 1

    leftover = [(k[0], k[1]) for k, v in queues.items() if v]

    if replaced:
        io.open(target, "w", encoding="utf-8").write("\n".join(lines))
    return replaced, skipped, leftover


def process_group(group_dir):
    voice_dir = os.path.join(group_dir, "语音整理")
    if not os.path.isdir(voice_dir):
        print("  跳过（无 语音整理/ 目录）")
        return 0

    total = 0
    for name in sorted(os.listdir(voice_dir)):
        if not name.endswith(".md") or name == "README.md":
            continue
        date, entries = parse_voice_page(os.path.join(voice_dir, name))
        if not entries:
            print("  %s：未解析出语音段，跳过" % name)
            continue

        targets = [
            os.path.join(group_dir, "每日", "%s.md" % date),
            os.path.join(group_dir, "%s.md" % date[:7]),
        ]
        # 一键导出的成品是 <群目录>/<聊天名>.md，也要回填（按日期段匹配，不会误伤别的日期）
        for n in sorted(os.listdir(group_dir)):
            fp = os.path.join(group_dir, n)
            if n.endswith(".md") and os.path.isfile(fp) and fp not in targets and n != "README.md":
                targets.append(fp)
        for t in targets:
            rep, skip, leftover = backfill(t, entries)
            total += rep
            rel = os.path.relpath(t, group_dir)
            if rep:
                print("  ✅ %s：回填 %d 条" % (rel, rep))
            elif skip:
                print("  ○  %s：%d 条已是最新，无需回填" % (rel, skip))
            elif os.path.isfile(t):
                print("  ⚠️  %s：未找到可回填的占位" % rel)
            if leftover:
                print("     未匹配上的语音：%s" % ", ".join("%s %s" % x for x in leftover))
    return total


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return 1
    grand = 0
    for group_dir in sys.argv[1:]:
        print("【%s】" % os.path.basename(group_dir.rstrip("/")))
        if not os.path.isdir(group_dir):
            print("  目录不存在，跳过")
            continue
        grand += process_group(group_dir)
    print("合计回填 %d 条" % grand)
    return 0


if __name__ == "__main__":
    sys.exit(main())
