"""
微信聊天图片导出：从 markdown 中的 local_id 定位并解密图片。

映射链：
  message_0.db(local_id → packed_info_data → MD5) → attach/.dat → 解密

用法：
  python3 export_images.py \
    --input /tmp/wechat_export_raw.md \
    --username "123456789@chatroom" \
    --output ~/path/to/output.md
"""
import argparse
import glob
import hashlib
import json
import os
import re
import sqlite3
import struct
import subprocess
import sys

import hmac as hmac_mod
from Crypto.Cipher import AES
from Crypto.Util import Padding

# === 常量 ===
PAGE_SZ = 4096
KEY_SZ = 32
SALT_SZ = 16
IV_SZ = 16
HMAC_SZ = 64
RESERVE_SZ = 80
SQLITE_HDR = b"SQLite format 3\x00"
V2_MAGIC_FULL = b"\x07\x08V2\x08\x07"
V1_MAGIC_FULL = b"\x07\x08V1\x08\x07"
V2_MAGIC_4B = b"\x07\x08\x56\x32"

SKILL_DIR = os.path.dirname(os.path.abspath(__file__))


# ========== 数据库解密 ==========

def derive_mac_key(enc_key, salt):
    mac_salt = bytes(b ^ 0x3A for b in salt)
    return hashlib.pbkdf2_hmac("sha512", enc_key, mac_salt, 2, dklen=KEY_SZ)


def decrypt_page(enc_key, page_data, pgno):
    iv = page_data[PAGE_SZ - RESERVE_SZ: PAGE_SZ - RESERVE_SZ + IV_SZ]
    if pgno == 1:
        encrypted = page_data[SALT_SZ: PAGE_SZ - RESERVE_SZ]
        cipher = AES.new(enc_key, AES.MODE_CBC, iv)
        decrypted = cipher.decrypt(encrypted)
        return bytes(bytearray(SQLITE_HDR + decrypted + b"\x00" * RESERVE_SZ))
    else:
        encrypted = page_data[: PAGE_SZ - RESERVE_SZ]
        cipher = AES.new(enc_key, AES.MODE_CBC, iv)
        return cipher.decrypt(encrypted) + b"\x00" * RESERVE_SZ


def decrypt_database(db_path, out_path, enc_key_hex):
    enc_key = bytes.fromhex(enc_key_hex)
    with open(db_path, "rb") as f:
        page1 = f.read(PAGE_SZ)

    salt = page1[:SALT_SZ]
    mac_key = derive_mac_key(enc_key, salt)
    p1_hmac_data = page1[SALT_SZ: PAGE_SZ - RESERVE_SZ + IV_SZ]
    p1_stored_hmac = page1[PAGE_SZ - HMAC_SZ: PAGE_SZ]
    hm = hmac_mod.new(mac_key, p1_hmac_data, hashlib.sha512)
    hm.update(struct.pack("<I", 1))
    if hm.digest() != p1_stored_hmac:
        return False

    file_size = os.path.getsize(db_path)
    total_pages = file_size // PAGE_SZ

    with open(db_path, "rb") as fin, open(out_path, "wb") as fout:
        for pgno in range(1, total_pages + 1):
            page = fin.read(PAGE_SZ)
            if len(page) < PAGE_SZ:
                if page:
                    page += b"\x00" * (PAGE_SZ - len(page))
                else:
                    break
            fout.write(decrypt_page(enc_key, page, pgno))
    return True


# ========== 图片密钥推导 ==========

def find_image_keys(wxid_dir):
    """从 kvcomm 缓存推导图片加解密密钥，返回 (aes_key_str, xor_key_int) 或 None"""
    # 找 kvcomm 目录
    docs_dir = os.path.dirname(os.path.dirname(wxid_dir))  # xwechat_files 的父目录
    kvcomm_candidates = [
        os.path.join(docs_dir, "app_data", "net", "kvcomm"),
        os.path.join(os.path.dirname(docs_dir), "app_data", "net", "kvcomm"),
    ]
    kvcomm_dir = None
    for c in kvcomm_candidates:
        if os.path.isdir(c):
            kvcomm_dir = c
            break
    if not kvcomm_dir:
        return None

    # 提取 uin
    kvcomm_re = re.compile(r"^key_(\d+)_.+\.statistic$", re.IGNORECASE)
    codes = set()
    for name in os.listdir(kvcomm_dir):
        m = kvcomm_re.match(name)
        if m:
            try:
                code = int(m.group(1))
                if 0 < code <= 0xFFFFFFFF:
                    codes.add(code)
            except ValueError:
                pass

    if not codes:
        return None

    # 提取 wxid
    wxid_basename = os.path.basename(wxid_dir)
    wxid_candidates = [wxid_basename]
    if wxid_basename.lower().startswith("wxid_"):
        m = re.match(r"^(wxid_[^_]+)", wxid_basename, re.IGNORECASE)
        if m and m.group(1) != wxid_basename:
            wxid_candidates.append(m.group(1))
    else:
        m = re.match(r"^(.+)_([a-zA-Z0-9]{4})$", wxid_basename)
        if m:
            wxid_candidates.append(m.group(1))

    # 找 V2 模板验证
    attach_dir = os.path.join(wxid_dir, "msg", "attach")
    templates = _find_v2_templates(attach_dir)
    if not templates:
        return None

    # 枚举验证
    for wxid in wxid_candidates:
        for code in codes:
            xor_key = code & 0xFF
            aes_key = hashlib.md5(f"{code}{wxid}".encode()).hexdigest()[:16]
            if _verify_aes_key(aes_key, templates):
                return aes_key, xor_key

    return None


def _find_v2_templates(attach_dir, max_templates=3):
    if not os.path.isdir(attach_dir):
        return []
    templates = []
    seen = set()
    for root, _, files in os.walk(attach_dir):
        for f in files:
            if not f.endswith("_t.dat"):
                continue
            try:
                with open(os.path.join(root, f), "rb") as fp:
                    data = fp.read(0x20)
                if len(data) >= 0x1F and data[:4] == V2_MAGIC_4B:
                    ct = data[0xF:0x1F]
                    if ct not in seen:
                        seen.add(ct)
                        templates.append(ct)
                        if len(templates) >= max_templates:
                            return templates
            except OSError:
                pass
    return templates


def _verify_aes_key(aes_key_str, templates):
    IMAGE_MAGICS = (b"\xff\xd8\xff", b"\x89PNG", b"GIF", b"RIFF", b"wxgf")
    key_bytes = aes_key_str.encode("ascii")[:16]
    for ct in templates:
        try:
            decrypted = AES.new(key_bytes, AES.MODE_ECB).decrypt(ct)
        except (ValueError, KeyError):
            return False
        if not any(decrypted.startswith(m) for m in IMAGE_MAGICS):
            return False
    return True


# ========== 图片解密 ==========

def decrypt_dat(dat_path, out_path, aes_key, xor_key):
    """解密 .dat 文件，返回 (成功, 实际输出路径)"""
    with open(dat_path, "rb") as f:
        data = f.read()
    if len(data) < 15:
        return False, None

    sig = data[:6]

    if sig in (V2_MAGIC_FULL, V1_MAGIC_FULL):
        if sig == V1_MAGIC_FULL:
            aes_k = b"cfcd208495d565ef"
        else:
            aes_k = aes_key.encode("ascii")[:16] if isinstance(aes_key, str) else aes_key[:16]

        aes_size, xor_size = struct.unpack_from("<LL", data, 6)
        aligned = aes_size - ~(~aes_size % 16)
        offset = 15
        if offset + aligned > len(data):
            return False, None
        try:
            dec_aes = Padding.unpad(
                AES.new(aes_k, AES.MODE_ECB).decrypt(data[offset: offset + aligned]),
                AES.block_size,
            )
        except (ValueError, KeyError):
            return False, None
        offset += aligned
        raw_end = len(data) - xor_size
        raw_data = data[offset:raw_end] if offset < raw_end else b""
        dec_xor = bytes(b ^ xor_key for b in data[raw_end:])
        decrypted = dec_aes + raw_data + dec_xor
    else:
        # 旧 XOR
        header = data[:16]
        magics = {"jpg": [0xFF, 0xD8, 0xFF], "png": [0x89, 0x50, 0x4E, 0x47],
                  "gif": [0x47, 0x49, 0x46, 0x38], "webp": [0x52, 0x49, 0x46, 0x46]}
        xor_k = None
        for _, magic in magics.items():
            k = header[0] ^ magic[0]
            if all(i < len(header) and (header[i] ^ k) == magic[i] for i in range(len(magic))):
                xor_k = k
                break
        if xor_k is None:
            return False, None
        decrypted = bytes(b ^ xor_k for b in data)

    fmt = _detect_format(decrypted[:16])

    # wxgf → jpg
    if fmt == "hevc":
        nal_pos = decrypted.find(b"\x00\x00\x00\x01")
        if nal_pos >= 0:
            raw_h265 = out_path + ".h265"
            with open(raw_h265, "wb") as f:
                f.write(decrypted[nal_pos:])
            jpg_path = out_path + ".jpg"
            result = subprocess.run(
                ["ffmpeg", "-y", "-i", raw_h265, "-frames:v", "1", jpg_path],
                capture_output=True, timeout=15,
            )
            os.unlink(raw_h265)
            if result.returncode == 0 and os.path.exists(jpg_path):
                return True, jpg_path
        return False, None

    final_path = f"{out_path}.{fmt}"
    os.makedirs(os.path.dirname(final_path), exist_ok=True)
    with open(final_path, "wb") as f:
        f.write(decrypted)
    return True, final_path


def _detect_format(header):
    if header[:3] == b"\xff\xd8\xff":
        return "jpg"
    if header[:4] == b"\x89PNG":
        return "png"
    if header[:3] == b"GIF":
        return "gif"
    if header[:4] == b"RIFF":
        return "webp"
    if header[:4] == b"wxgf":
        return "hevc"
    return "bin"


# ========== MD5 提取 ==========

def extract_md5_from_packed_info(blob):
    if not blob:
        return None
    hex_chars = set(b"0123456789abcdef")
    i = 0
    while i <= len(blob) - 32:
        if blob[i] in hex_chars:
            candidate = blob[i: i + 32]
            if all(b in hex_chars for b in candidate):
                try:
                    return candidate.decode("ascii")
                except UnicodeDecodeError:
                    pass
            i += 32
        else:
            i += 1
    return None


# ========== 主流程 ==========

def resolve_wxid_dir():
    """从 wechat-cli config 读取 wxid 目录"""
    config_path = os.path.expanduser("~/.wechat-cli/config.json")
    if os.path.exists(config_path):
        with open(config_path) as f:
            cfg = json.load(f)
        db_dir = cfg.get("db_dir", "")
        if db_dir:
            return os.path.dirname(db_dir)  # 去掉 /db_storage

    # fallback：扫目录
    base = os.path.expanduser(
        "~/Library/Containers/com.tencent.xinWeChat/Data/Documents/xwechat_files"
    )
    for d in os.listdir(base):
        if d.startswith("wxid_"):
            return os.path.join(base, d)
    return None


def main():
    parser = argparse.ArgumentParser(description="微信聊天图片导出")
    parser.add_argument("--input", required=True, help="wechat-cli 导出的 markdown 文件")
    parser.add_argument("--username", required=True, help="聊天 username (如 xxx@chatroom)")
    parser.add_argument("--output", required=True, help="输出 markdown 路径")
    args = parser.parse_args()

    wxid_dir = resolve_wxid_dir()
    if not wxid_dir:
        print("错误：找不到微信数据目录", file=sys.stderr)
        sys.exit(1)

    db_storage = os.path.join(wxid_dir, "db_storage")
    attach_dir = os.path.join(wxid_dir, "msg", "attach")
    chat_hash = hashlib.md5(args.username.encode()).hexdigest()
    chat_attach_dir = os.path.join(attach_dir, chat_hash)
    msg_table = f"Msg_{chat_hash}"

    keys_file = os.path.expanduser("~/.wechat-cli/all_keys.json")
    with open(keys_file) as f:
        keys = json.load(f)

    # 读取 markdown
    with open(args.input) as f:
        content = f.read()

    pattern = re.compile(r"\[图片\] \(local_id=(\d+)\)")
    all_ids = sorted(set(int(m.group(1)) for m in pattern.finditer(content)))
    print(f"找到 {len(all_ids)} 个图片占位符")

    if not all_ids:
        # 没有图片，直接复制
        out_dir = os.path.dirname(args.output)
        os.makedirs(out_dir, exist_ok=True)
        with open(args.output, "w") as f:
            f.write(content)
        print(f"无图片，已复制到 {args.output}")
        return

    # 解密 message DB
    decrypted_db = "/tmp/wechat_msg_decrypted.db"
    db_found = False
    for db_name in ["message_0.db", "message_1.db"]:
        key_path = f"message/{db_name}"
        if key_path not in keys:
            continue
        db_path = os.path.join(db_storage, "message", db_name)
        if not os.path.exists(db_path):
            continue
        print(f"解密 {db_name}...")
        if decrypt_database(db_path, decrypted_db, keys[key_path]["enc_key"]):
            # 检查表是否存在
            conn = sqlite3.connect(decrypted_db)
            try:
                conn.execute(f"SELECT count(*) FROM [{msg_table}]").fetchone()
                db_found = True
                conn.close()
                print(f"  {db_name} 包含 {msg_table}")
                break
            except Exception:
                conn.close()
                continue

    if not db_found:
        print("错误：无法在消息数据库中找到对应的聊天表", file=sys.stderr)
        sys.exit(1)

    # 构建 local_id → MD5 映射
    conn = sqlite3.connect(decrypted_db)
    rows = conn.execute(f"""
        SELECT local_id, packed_info_data
        FROM [{msg_table}]
        WHERE local_type = 3
    """).fetchall()
    conn.close()

    id_to_md5 = {}
    for local_id, blob in rows:
        md5 = extract_md5_from_packed_info(blob)
        if md5:
            id_to_md5[local_id] = md5
    print(f"数据库中 {len(id_to_md5)} 条图片记录，匹配 {sum(1 for i in all_ids if i in id_to_md5)}/{len(all_ids)}")

    # 推导图片密钥
    print("推导图片解密密钥...")
    key_result = find_image_keys(wxid_dir)
    if not key_result:
        print("错误：无法推导图片密钥", file=sys.stderr)
        sys.exit(1)
    aes_key, xor_key = key_result
    print(f"  aes_key={aes_key}, xor_key=0x{xor_key:02x}")

    # 解密图片
    out_dir = os.path.dirname(args.output)
    img_dir = os.path.join(out_dir, "img")
    os.makedirs(img_dir, exist_ok=True)

    success = failed = no_md5 = no_file = 0
    for local_id in all_ids:
        if local_id not in id_to_md5:
            no_md5 += 1
            continue

        file_md5 = id_to_md5[local_id]
        dat_files = glob.glob(os.path.join(chat_attach_dir, "*", "Img", f"{file_md5}*.dat"))
        if not dat_files:
            no_file += 1
            continue

        # 选最佳文件：无后缀 > _h > _t
        dat_path = dat_files[0]
        for f in dat_files:
            if os.path.basename(f) == f"{file_md5}.dat":
                dat_path = f
                break
        for f in dat_files:
            if f.endswith("_h.dat"):
                dat_path = f
                break

        out_base = os.path.join(img_dir, str(local_id))
        ok, final_path = decrypt_dat(dat_path, out_base, aes_key, xor_key)
        if ok:
            img_filename = os.path.basename(final_path)
            content = content.replace(
                f"[图片] (local_id={local_id})",
                f"![[img/{img_filename}]]",
            )
            success += 1
        else:
            failed += 1

    print(f"\n图片结果: {success} 成功, {failed} 解密失败, {no_md5} 无MD5, {no_file} 无文件")

    # 写入
    os.makedirs(os.path.dirname(args.output), exist_ok=True)
    with open(args.output, "w") as f:
        f.write(content)
    print(f"已写入: {args.output}")


if __name__ == "__main__":
    main()
