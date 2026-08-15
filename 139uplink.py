#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""139uplink —— 本地图片 → 中国移动云盘(139) 上传 → 本地直链 → 回填 md

复用同目录 yidong.py（casgen 的 Yun139：鉴权 / mcloud-sign / 上传 / 文件夹解析创建），
纯 Python3 标准库，无需 pip 安装任何东西。

输出本地直链（经 139img.py 代理实时取图，永久有效、无分享数量限制、Obsidian 可直接显示）：
    ![photo.png](http://127.0.0.1:8740/img/<file_id>)

用法示例：
    python 139uplink.py photo.png                          # 上传到默认文件夹
    python 139uplink.py photo.png --folder /知识库/2026    # 指定文件夹（不存在会自动创建）
    python 139uplink.py photo.png --token "Basic xxxx"     # 临时用命令行 token
    python 139uplink.py photo.png --no-link                # 只上传，不输出链接（调试用）

注意：
    * token（Authorization）等同登录态，泄露即账号失窃，勿提交到 git。
    * 直链依赖 139img.py 代理在运行（插件会自动拉起，或 python 139img.py）。
"""

import argparse
import hashlib
import json
import mimetypes
import os
import sys
import time
import urllib.error
import urllib.request

# 139 是国内直连服务。用户本机可能开着 Clash/V2Ray 等本地代理（如 127.0.0.1:7897），
# urllib 默认会读系统代理把所有请求绕道代理 → 139 请求慢 10s+。
# 这里统一装一个"无代理"的默认 opener，让本工具所有请求（含 casgen 的 yidong）直连。
urllib.request.install_opener(
    urllib.request.build_opener(urllib.request.ProxyHandler({}))
)

# yidong.py 位置：优先同目录（自包含项目），回退到上级目录（casgen 场景）
TOOL_DIR = os.path.dirname(os.path.abspath(__file__))
if os.path.isfile(os.path.join(TOOL_DIR, "yidong.py")):
    if TOOL_DIR not in sys.path:
        sys.path.insert(0, TOOL_DIR)
else:
    CASGEN_DIR = os.path.dirname(TOOL_DIR)
    if CASGEN_DIR not in sys.path:
        sys.path.insert(0, CASGEN_DIR)

from yidong import Yun139, TokenExpired  # noqa: E402


# ============================ 配置 ============================
DEFAULT_CONFIG = os.path.join(TOOL_DIR, "config.json")


def load_config(path=DEFAULT_CONFIG):
    if os.path.isfile(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def save_config(cfg, path=DEFAULT_CONFIG):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


# ============================ 图片类型 ============================
def guess_content_type(name):
    """根据扩展名猜 Content-Type，取不到就退回 octet-stream。"""
    t, _ = mimetypes.guess_type(name)
    if t:
        return t
    ext = os.path.splitext(name)[1].lower()
    return {
        ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
        ".gif": "image/gif", ".webp": "image/webp", ".bmp": "image/bmp",
        ".svg": "image/svg+xml", ".avif": "image/avif",
    }.get(ext, "application/octet-stream")


# ============================ 带进度的上传 ============================
class _ProgressBody:
    """把 bytes 包装成 file-like，让 http.client 分块读并触发进度回调。

    说明：urllib 对带 .read() 的 body 会用固定 8KB 块循环发送，
    我们借此拿到"已发送字节数"做进度上报。
    """

    def __init__(self, data, total, cb, step=262144):
        self.data = data
        self.off = 0
        self.total = total
        self.cb = cb
        self.next_report = min(step, total)
        self.step = step

    def __len__(self):
        return self.total

    def read(self, n=-1):
        if n < 0 or n > self.total - self.off:
            n = self.total - self.off
        chunk = self.data[self.off:self.off + n]
        self.off += len(chunk)
        if self.cb and self.off >= self.next_report:
            self.cb(self.off, self.total)
            self.next_report += self.step
        return chunk


def _progress_line(done, total):
    pct = int(done * 100 / total) if total else 100
    sys.stderr.write(f"\r  上传中 {done // 1024}/{total // 1024} KB ({pct}%)")
    sys.stderr.flush()


def upload_file_progress(client, name, content, parent, content_type="application/octet-stream"):
    """等价 casgen 的 upload_file，但 PUT 阶段上报进度并打印各步骤耗时。

    复用 client.personal_post（含 mcloud-sign 签名与鉴权），body 结构与 casgen 完全一致。
    """
    if isinstance(content, str):
        content = content.encode("utf-8")
    size = len(content)
    content_sha256 = hashlib.sha256(content).hexdigest()
    body = {
        "contentHash": content_sha256,
        "contentHashAlgorithm": "SHA256",
        "contentType": content_type,
        "parallelUpload": False,
        "partInfos": [{"partNumber": 1, "partSize": size}],
        "size": size,
        "parentFileId": parent if parent not in ("root", "/", "") else "/",
        "name": name,
        "type": "file",
    }

    t0 = time.time()
    resp = client.personal_post("/file/create", body)
    t1 = time.time()
    d = resp.get("data") if isinstance(resp, dict) else None
    if not isinstance(d, dict):
        raise RuntimeError(f"file/create 响应异常: {json.dumps(resp, ensure_ascii=False)[:500]}")
    file_id = d.get("fileId")
    upload_id = d.get("uploadId")
    if (d.get("exist") or d.get("rapidUpload")) and file_id:
        sys.stderr.write(f"\r  已存在（秒传）\n")
        return {"_file_id": file_id, "_exist": True}

    upload_url = None
    for p in (d.get("partInfos") or []):
        upload_url = p.get("uploadUrl") or p.get("uploadurl")
        if upload_url:
            break
    if not upload_url:
        raise RuntimeError(f"file/create 未返回上传地址: {json.dumps(d, ensure_ascii=False)[:500]}")

    _progress_line(0, size)
    req = urllib.request.Request(upload_url, data=_ProgressBody(content, size, _progress_line), headers={
        "Content-Type": content_type,
        "Content-Length": str(size),
        "Origin": "https://yun.139.com",
        "Referer": "https://yun.139.com/",
    }, method="PUT")
    try:
        with urllib.request.urlopen(req, timeout=300) as r:
            r.read()
    except Exception as e:
        sys.stderr.write("\n")
        raise RuntimeError(f"上传传输失败: {e}")
    sys.stderr.write("\n")
    t2 = time.time()

    if file_id and upload_id:
        try:
            client.personal_post("/file/complete", {
                "contentHash": content_sha256,
                "contentHashAlgorithm": "SHA256",
                "fileId": file_id,
                "uploadId": upload_id,
            })
        except Exception as e:
            raise RuntimeError(f"file/complete 报错: {e}")
    t3 = time.time()
    sys.stderr.write(
        f"  耗时: create {t1 - t0:.1f}s / 传输 {t2 - t1:.1f}s / complete {t3 - t2:.1f}s\n")
    return {"_file_id": file_id, "_upload_id": upload_id}


# ============================ 主流程 ============================
def _warn_if_server_down(port, log):
    """检查 139img.py 代理是否在运行，没起就提示。"""
    try:
        import socket
        s = socket.create_connection(("127.0.0.1", port), timeout=0.5)
        s.close()
    except Exception:
        log(f"⚠️ 本地代理(127.0.0.1:{port})未运行！链接暂时无法显示。"
            f"启动方法：python {os.path.join(TOOL_DIR, '139img.py')}")


def upload_and_link(image_path, folder, token, no_link, save, link_only=False):
    """上传图片并输出本地直链。link_only=True 时只往 stdout 打一行 md 链接（插件用）。"""
    cfg = load_config()

    def log(msg=""):
        (sys.stderr if link_only else sys.stdout).write(msg + "\n")

    token = token or cfg.get("token") or os.environ.get("OBS139_TOKEN", "").strip()
    if not token:
        sys.exit("缺少登录态：请用 --token 传入 Authorization，或在 config.json 里填 token。")

    folder = folder or cfg.get("default_folder") or "知识库图片"
    name = os.path.basename(image_path)
    content_type = guess_content_type(name)

    log(f"[1/3] 初始化 139 客户端 ...")
    client = Yun139(token)

    log(f"[2/3] 解析目标文件夹: {folder or '(根目录)'}")
    parent = client.resolve_folder(folder)   # 不存在会自动创建

    with open(image_path, "rb") as f:
        content = f.read()
    size = len(content)
    log(f"[3/3] 上传 {name} ({size} bytes, {content_type}) → {folder or '根目录'}")

    result = upload_file_progress(client, name, content, parent, content_type)
    file_id = result.get("_file_id")
    if not file_id:
        log("上传接口返回异常（无 fileId）：")
        log(json.dumps(result, ensure_ascii=False, indent=2)[:2000])
        sys.exit(1)
    if result.get("_exist"):
        log(f"   已存在（秒传跳过）：file_id={file_id}")
    else:
        log(f"   上传完成：file_id={file_id}")

    # 保存 token 与文件夹到配置，方便下次直接复用
    if save:
        cfg["token"] = token
        cfg["default_folder"] = folder
        save_config(cfg)
        log(f"   已保存 token 与默认文件夹到 {DEFAULT_CONFIG}")

    if no_link:
        log(f"\nfile_id: {file_id}\n（--no-link，未输出链接）")
        return

    # 本地直链：经 139img.py 代理实时取图，永久有效、无分享数量限制、可内嵌渲染
    port = int(os.environ.get("OBS139_PORT", cfg.get("port", 8740)))
    md = f"![{name}](http://127.0.0.1:{port}/img/{file_id})"
    _warn_if_server_down(port, log)
    if link_only:
        print(md)
        return

    print("\n========== 复制下面的链接到 md（本地直链，Obsidian 可直接显示） ==========")
    print(md)
    print("==========================================")
    print("（直链经本机 139img.py 代理取图，需保持该服务运行）")


def check_token(token):
    """轻量校验 token 是否有效（只列根目录，不传文件），并提示剩余有效期。"""
    import datetime
    if not token:
        sys.exit("缺少 token：请在 config.json 填 token，或用 --token 传入。")
    exp = None
    try:
        # 139 token 形如 pc:手机号:xxx|1|RCS|<毫秒时间戳>|签名
        raw = __import__("base64").b64decode(token + "==="[:(-len(token)) % 4]).decode("utf-8", "replace")
        for part in raw.split("|"):
            if part.isdigit() and len(part) == 13:
                exp = datetime.datetime.fromtimestamp(int(part) / 1000)
                break
    except Exception:
        pass
    print("正在校验 token ...")
    try:
        client = Yun139(token)
        client.list_dir("root")   # 轻量探测，失败会抛 TokenExpired
        if exp:
            days = (exp.date() - datetime.date.today()).days
            print(f"✅ token 有效，有效期至 {exp.strftime('%Y-%m-%d %H:%M:%S')}（剩 {days} 天）")
        else:
            print("✅ token 有效")
    except TokenExpired as e:
        print(f"❌ token 已失效：{e}")
        print("   刷新方法：浏览器登录 yun.139.com → F12 → Network → 任选请求 →")
        print("   复制请求头 Authorization 的值 → 更新 config.json 的 token。")
        sys.exit(1)
    except Exception as e:
        # 路由策略层也会返回鉴权失败码（05050006 / 04000005 / 暂无权限），同样按失效提示
        if any(k in str(e) for k in ("05050006", "04000005", "暂无权限")):
            print(f"❌ token 已失效（{str(e)[:80]}...）")
            print("   刷新方法：浏览器登录 yun.139.com → F12 → Network → 任选请求 →")
            print("   复制请求头 Authorization 的值 → 更新 config.json 的 token。")
            sys.exit(1)
        print(f"⚠️ 无法确认（网络/其他错误）：{e}")
        sys.exit(1)


def main():
    ap = argparse.ArgumentParser(description="上传本地图片到 139 云盘并生成本地直链")
    ap.add_argument("image", nargs="?", help="本地图片路径（--check 时不需要）")
    ap.add_argument("--folder", help="上传到的云盘文件夹（支持 /a/b 多级，不存在自动创建）")
    ap.add_argument("--token", help="Authorization 头（可带可不带 Basic 前缀），优先级最高")
    ap.add_argument("--no-link", action="store_true", help="只上传，不输出链接（调试用）")
    ap.add_argument("--save", action="store_true", help="把 token 和默认文件夹写入 config.json")
    ap.add_argument("--link-only", action="store_true",
                    help="只输出一行 md 链接到 stdout（供 Obsidian 插件等程序化调用）")
    ap.add_argument("--check", action="store_true", help="只校验 token 有效性，不上传")
    args = ap.parse_args()

    cfg = load_config()
    token = args.token or cfg.get("token") or os.environ.get("OBS139_TOKEN", "").strip()

    if args.check:
        check_token(token)
        return

    if not args.image:
        ap.error("缺少图片路径参数（或使用 --check）")
    if not os.path.isfile(args.image):
        sys.exit(f"文件不存在: {args.image}")

    try:
        upload_and_link(args.image, args.folder, token, args.no_link, args.save,
                        link_only=args.link_only)
    except TokenExpired as e:
        sys.exit(f"登录态已失效，请重新获取 Authorization：{e}\n"
                 f"（浏览器登录 yun.139.com → F12 → Network → 复制 Authorization 头 → 更新 config.json 的 token）")
    except Exception as e:
        sys.exit(f"出错：{e}")


if __name__ == "__main__":
    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except Exception:
            pass
    main()
