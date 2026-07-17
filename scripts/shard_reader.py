#!/usr/bin/env python3
"""微信消息多分片读取器 —— wechat-cli export 的替代。

背景：微信 4.0 会把消息轮换存进 message_0.db / message_1.db / message_2.db … 多个
加密分片；wechat-cli 0.2.4 只读 message_0.db，读不到新分片里的近期消息。本模块用纯
Python（复用 export_images.py 的 SQLCipher 解密 + ctypes 绑定 libzstd）读全部分片，
合并后输出与 wechat-cli export 完全一致的 markdown，供 export_images.py / format_chat.py 消费。

只读微信数据库；解密临时库权限 0600、用完即删；绝不改动微信本体。
"""
import os, sys, json, re, sqlite3, hashlib, tempfile, argparse, ctypes, ctypes.util
from datetime import datetime
import xml.etree.ElementTree as ET

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)
import export_images as EI  # 复用 decrypt_database（SQLCipher 解密，含 HMAC 校验）


# ---------- zstd（ctypes 绑定系统 libzstd，无需 pip 依赖） ----------
def _load_zstd():
    cands = ["/opt/homebrew/lib/libzstd.dylib", "/opt/homebrew/lib/libzstd.1.dylib",
             "/usr/local/lib/libzstd.dylib", "/usr/lib/libzstd.dylib",
             ctypes.util.find_library("zstd"), "libzstd.dylib"]
    for c in cands:
        if not c:
            continue
        try:
            lib = ctypes.CDLL(c)
            lib.ZSTD_getFrameContentSize.restype = ctypes.c_ulonglong
            lib.ZSTD_getFrameContentSize.argtypes = [ctypes.c_void_p, ctypes.c_size_t]
            lib.ZSTD_decompress.restype = ctypes.c_size_t
            lib.ZSTD_decompress.argtypes = [ctypes.c_void_p, ctypes.c_size_t, ctypes.c_void_p, ctypes.c_size_t]
            lib.ZSTD_isError.restype = ctypes.c_uint
            lib.ZSTD_isError.argtypes = [ctypes.c_size_t]
            return lib
        except OSError:
            continue
    return None

_ZSTD = _load_zstd()
_ZSTD_MAGIC = b"\x28\xb5\x2f\xfd"


def zstd_decompress(blob):
    """解开 zstd blob；非 zstd 原样返回；失败返回 None。"""
    if not blob:
        return blob
    if blob[:4] != _ZSTD_MAGIC:
        return blob
    if _ZSTD is None:
        return None
    n = _ZSTD.ZSTD_getFrameContentSize(blob, len(blob))
    if n in (0xFFFFFFFFFFFFFFFF, 0xFFFFFFFFFFFFFFFE) or n <= 0 or n > (1 << 30):
        n = max(len(blob) * 25, 1 << 16)
    out = ctypes.create_string_buffer(int(n))
    r = _ZSTD.ZSTD_decompress(out, int(n), blob, len(blob))
    if _ZSTD.ZSTD_isError(r):
        return None
    return out.raw[:r]


# ---------- 配置 / 密钥 ----------
def load_env():
    cfg = json.load(open(os.path.expanduser("~/.wechat-cli/config.json")))
    db_dir = cfg["db_dir"]
    allk = json.load(open(os.path.expanduser("~/.wechat-cli/all_keys.json")))
    keys = {}

    def walk(o, p=""):
        if isinstance(o, dict):
            for k, v in o.items():
                if k == "enc_key" and isinstance(v, str):
                    keys[p.strip("/")] = v
                else:
                    walk(v, p + "/" + k)
    walk(allk)
    # 本人 wxid：从 db_dir 路径里的 wxid_xxx_yyyy 提取
    m = re.search(r"(wxid_[0-9a-zA-Z]+)_[0-9a-f]+", db_dir)
    my_wxid = m.group(1) if m else None
    return db_dir, keys, my_wxid


def to_epoch(v):
    if v is None:
        return None
    v = int(v)
    if v > 10_000_000_000:
        v //= 1000
    return v


# ---------- 联系人显示名解析 ----------
def build_name_map(db_dir, keys):
    """username → 显示名（remark 优先，其次 nick_name）。"""
    rel = "contact/contact.db"
    if rel not in keys:
        return {}
    src = os.path.join(db_dir, rel)
    if not os.path.exists(src):
        return {}
    fd, tmp = tempfile.mkstemp(prefix="wx_contact_", suffix=".db")
    os.close(fd)
    os.chmod(tmp, 0o600)
    names = {}
    try:
        if not EI.decrypt_database(src, tmp, keys[rel]):
            return {}
        os.chmod(tmp, 0o600)
        con = sqlite3.connect(tmp)
        con.text_factory = lambda b: b.decode("utf-8", "replace")
        try:
            for uname, remark, nick in con.execute(
                    "SELECT username, remark, nick_name FROM contact"):
                disp = (remark or "").strip() or (nick or "").strip()
                if uname and disp:
                    names[uname] = disp
        except sqlite3.Error:
            pass
        con.close()
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass
    return names


# ---------- 群内成员昵称（chatroom_member / 群消息内 XML 兜底） ----------
def build_room_names(db_dir, keys, room_username):
    """群成员 wxid → 群昵称。用 contact.db 的 chatroom_member（若结构支持）。"""
    # 结构在不同版本差异较大，失败则返回空，交由 contact 名 / wxid 兜底。
    return {}


# ---------- 消息内容渲染 ----------
def strip_prefix(text, sender_username):
    """群消息 content 形如 'wxid_xxx:\n正文'，剥掉发送者前缀。"""
    if not text:
        return text
    pre = f"{sender_username}:\n"
    if sender_username and text.startswith(pre):
        return text[len(pre):]
    # 兜底：首行是 'xxx:' 且第二行起为正文
    m = re.match(r"^[^\n:：]{1,64}:\n", text)
    if m and sender_username and text.startswith(sender_username + ":"):
        return text[m.end():]
    return text


def _xml_root(s):
    try:
        # content 可能前面还有 wxid 前缀，截取第一个 '<'
        i = s.find("<")
        return ET.fromstring(s[i:]) if i >= 0 else None
    except Exception:
        return None


def render_content(base, sub, raw_text, local_id, sender_username):
    """返回 (正文, 引用行 或 None)。"""
    body = strip_prefix(raw_text or "", sender_username)
    if base == 1:  # 文本
        return body.strip(), None
    if base == 3:  # 图片
        return f"[图片] (local_id={local_id})", None
    if base == 34:
        return "[语音]", None
    if base == 43:
        return "[视频]", None
    if base == 47:
        return "[表情]", None
    if base == 42:
        return "[名片]", None
    if base == 66:  # 企业微信/openim 名片
        root = _xml_root(body)
        nick = ""
        if root is not None:
            nick = (root.get("nickname") or "").strip()
            if not nick:
                mm = root.find(".//msg")
                if mm is not None:
                    nick = (mm.get("nickname") or "").strip()
        return (f"[名片] {nick}".strip() if nick else "[名片]"), None
    if base == 48:
        return "[位置]", None
    if base == 10000:  # 系统消息
        root = _xml_root(body)
        if root is not None:
            c = root.find(".//content")
            if c is not None and c.text:
                return c.text.strip(), None
        # 纯文本系统提示
        return (body.strip() or "[系统消息]"), None
    if base == 49:  # 应用消息
        root = _xml_root(body)
        title = ""
        apptype = sub
        if root is not None:
            app = root.find(".//appmsg")
            if app is not None:
                t = app.findtext("title")
                title = (t or "").strip()
                ty = app.findtext("type")
                if ty and ty.isdigit():
                    apptype = int(ty)
                if apptype == 57:  # 引用回复
                    refer = app.find(".//refermsg")
                    quote = None
                    if refer is not None:
                        rname = (refer.findtext("displayname") or "").strip()
                        rcontent = (refer.findtext("content") or "").strip()
                        # 群引用的 content 可能也带 'wxid:\n' 前缀
                        rcontent = re.sub(r"^[^\n:：]{1,64}:\n", "", rcontent)
                        rcontent = rcontent.replace("\n", " ")[:80]
                        if rname or rcontent:
                            quote = f"回复 {rname or '某人'}: {rcontent}"
                    return (title or "[引用]"), quote
        if apptype == 6:
            return f"[文件] {title}".strip(), None
        if apptype == 5:
            return f"[链接] {title}".strip(), None
        if apptype == 19:
            return f"[聊天记录] {title}".strip(), None
        if apptype == 33 or apptype == 36:
            return f"[小程序] {title}".strip(), None
        if apptype == 51:
            return "[视频号]", None
        return (f"[应用消息] {title}".strip() if title else "[应用消息]"), None
    return "[未知消息]", None


# ---------- 单分片读取 ----------
def read_shard(db_path, enc_key, msg_table, since_ts, until_ts, my_wxid, name_map):
    """解密单个分片，返回该分片内 [since,until] 的消息列表 + 该分片最早消息时间。"""
    fd, tmp = tempfile.mkstemp(prefix="wx_msg_", suffix=".db")
    os.close(fd)
    os.chmod(tmp, 0o600)
    msgs = []
    shard_min = None
    try:
        if not EI.decrypt_database(db_path, tmp, enc_key):
            return msgs, shard_min, False
        os.chmod(tmp, 0o600)
        con = sqlite3.connect(tmp)
        con.text_factory = bytes
        cur = con.cursor()
        # 表存在？
        exists = cur.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (msg_table,)).fetchone()
        if not exists:
            con.close()
            return msgs, shard_min, False
        # 该分片整体最早时间（用于早停判断）
        row = cur.execute(f"SELECT MIN(sort_seq) FROM [{msg_table}]").fetchone()
        shard_min = to_epoch(row[0]) if row and row[0] is not None else None
        # sender_id → username
        id2name = {rid: u.decode("utf-8", "replace")
                   for rid, u in cur.execute("SELECT rowid, user_name FROM Name2Id")}
        q = (f"SELECT local_id, local_type, real_sender_id, sort_seq, create_time, "
             f"message_content, WCDB_CT_message_content FROM [{msg_table}] "
             f"WHERE sort_seq >= ? AND sort_seq <= ? ORDER BY sort_seq")
        lo = since_ts * 1000
        hi = until_ts * 1000 + 999
        # sort_seq 有时是秒有时是毫秒，做宽松边界
        for lid, ltype, sid, seq, ctime, content, ct in cur.execute(
                q, (min(since_ts, lo), max(until_ts + 1, hi))):
            ts = to_epoch(seq) or to_epoch(ctime)
            if ts is None or ts < since_ts or ts > until_ts:
                continue
            base = ltype & 0xFFFFFFFF
            sub = ltype >> 32
            if ct == 4:  # zstd 压缩
                content = zstd_decompress(content)
            if isinstance(content, bytes):
                content = content.decode("utf-8", "replace")
            sender_uname = id2name.get(sid, f"id{sid}")
            if sender_uname == my_wxid:
                sender_disp = "me"
            else:
                sender_disp = name_map.get(sender_uname) or sender_uname
            body, quote = render_content(base, sub, content, lid, sender_uname)
            if base == 10000:  # 系统消息统一发送者标签
                sender_disp = "系统"
            # 服务器消息 id 用于跨分片去重
            msgs.append({
                "ts": ts, "sender": sender_disp, "body": body, "quote": quote,
                "server_id": None, "seq": to_epoch(seq),
            })
        con.close()
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass
    return msgs, shard_min, True


# ---------- 主流程 ----------
def export(chat_name, username, since_ts, until_ts, limit, out_path):
    db_dir, keys, my_wxid = load_env()
    msg_table = "Msg_" + hashlib.md5(username.encode()).hexdigest()
    name_map = build_name_map(db_dir, keys)

    # 消息分片按文件 mtime 从新到旧排序，覆盖到 since 即早停
    msg_dir = os.path.join(db_dir, "message")
    shard_keys = []
    for rel in keys:
        m = re.fullmatch(r"message/message_(\d+)\.db", rel)
        if m:
            p = os.path.join(db_dir, rel)
            if os.path.exists(p):
                shard_keys.append((os.path.getmtime(p), int(m.group(1)), rel, p))
    shard_keys.sort(reverse=True)  # 新→旧

    all_msgs = []
    for mtime, idx, rel, path in shard_keys:
        msgs, shard_min, has_table = read_shard(
            path, keys[rel], msg_table, since_ts, until_ts, my_wxid, name_map)
        if has_table:
            all_msgs.extend(msgs)
            # 该分片最早消息已早于/等于 since，更旧的分片无需再解（大幅省时）
            if shard_min is not None and shard_min <= since_ts:
                break

    # 跨分片去重（同 seq+sender+body），按时间排序
    seen = set()
    uniq = []
    for m in sorted(all_msgs, key=lambda x: (x["ts"], x["seq"] or 0)):
        k = (m["seq"], m["sender"], m["body"])
        if k in seen:
            continue
        seen.add(k)
        uniq.append(m)
    if limit and len(uniq) > limit:
        uniq = uniq[-limit:]

    is_group = username.endswith("@chatroom")
    lines = []
    lines.append(f"# 聊天记录: {chat_name}\n")
    lines.append(f"**时间范围:** {datetime.fromtimestamp(since_ts).strftime('%Y-%m-%d')} ~ "
                 f"{datetime.fromtimestamp(until_ts).strftime('%Y-%m-%d')}\n")
    lines.append(f"**导出时间:** {datetime.now().strftime('%Y-%m-%d %H:%M')}\n")
    lines.append(f"**消息数量:** {len(uniq)}\n")
    lines.append(f"**类型:** {'群聊' if is_group else '私聊'}\n")
    lines.append("---")
    for m in uniq:
        t = datetime.fromtimestamp(m["ts"]).strftime("%Y-%m-%d %H:%M")
        body = (m["body"] or "").rstrip()
        lines.append(f"- [{t}] {m['sender']}: {body}")
        if m["quote"]:
            lines.append(f"  ↳ {m['quote']}")
    text = "\n".join(lines) + "\n"
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(text)
    return len(uniq)


def main():
    ap = argparse.ArgumentParser(description="微信多分片消息读取器（wechat-cli export 替代）")
    ap.add_argument("chat")
    ap.add_argument("--username", required=True, help="xxx@chatroom 或 wxid_xxx")
    ap.add_argument("--start-time", dest="since", required=True, help="YYYY-MM-DD")
    ap.add_argument("--end-time", dest="until", help="YYYY-MM-DD（默认今天）")
    ap.add_argument("--limit", type=int, default=20000)
    ap.add_argument("--output", required=True)
    a = ap.parse_args()

    def parse_day(s, end=False):
        d = datetime.strptime(s[:10], "%Y-%m-%d")
        if end:
            d = d.replace(hour=23, minute=59, second=59)
        return int(d.timestamp())

    since_ts = parse_day(a.since)
    until_ts = parse_day(a.until, end=True) if a.until else int(
        datetime.now().replace(hour=23, minute=59, second=59).timestamp())
    n = export(a.chat, a.username, since_ts, until_ts, a.limit, a.output)
    print(f"[shard_reader] 导出 {n} 条到 {a.output}")


if __name__ == "__main__":
    main()
