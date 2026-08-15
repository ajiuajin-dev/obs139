#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""obs139 本地图片代理服务器 —— 把 139 云盘图片转成本机可直链的稳定 URL。

原理：持有用户的 token，按需调 139 的 getDownloadUrl 取最新下载直链并把图片字节
流回给本机浏览器 / Obsidian。URL 基于 file_id，永久有效，且不受分享数量限制。

用法：
    python 139img.py             # 启动，监听 127.0.0.1:8740
    浏览器/Obsidian 里用：       http://127.0.0.1:8740/img/<file_id>
    健康检查：                   http://127.0.0.1:8740/health

安全：只绑定 127.0.0.1，token 不出本机。
"""

import json
import os
import sys
import threading
import urllib.request
import urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# 139 是国内直连服务，绕开系统代理（同 139uplink.py）
urllib.request.install_opener(urllib.request.build_opener(urllib.request.ProxyHandler({})))

TOOL_DIR = os.path.dirname(os.path.abspath(__file__))
# yidong.py 优先用同目录的（自包含项目），否则回退上级目录（casgen 场景）
if os.path.isfile(os.path.join(TOOL_DIR, "yidong.py")):
    CASGEN_DIR = TOOL_DIR
else:
    CASGEN_DIR = os.path.dirname(TOOL_DIR)
if CASGEN_DIR not in sys.path:
    sys.path.insert(0, CASGEN_DIR)
from yidong import Yun139, TokenExpired  # noqa: E402

CONFIG = os.path.join(TOOL_DIR, "config.json")
PORT = int(os.environ.get("OBS139_PORT", "8740"))

_client = None
_client_lock = threading.Lock()


def load_token():
    cfg = json.load(open(CONFIG, encoding="utf-8"))
    tok = (cfg.get("token") or "").strip()
    if not tok:
        raise RuntimeError("config.json 里没有 token，请先配置（python 139uplink.py --check 会提示）")
    return tok


def get_client():
    """持有一个持久化的 Yun139 客户端（复用路由策略，避免每次请求都重新取 host）。"""
    global _client
    with _client_lock:
        if _client is None:
            _client = Yun139(load_token())
            _client._ensure_host()  # 预热：取一次真实 host
        return _client


def reset_client():
    global _client
    with _client_lock:
        _client = None


def guess_image_type(data):
    """用文件头魔法字节猜图片类型，兜底用 octet-stream。"""
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if data[:2] == b"BM":
        return "image/bmp"
    if data[:4] == b"\x00\x00\x00\x1cftyp" or b"ftypavif" in data[:16]:
        return "image/avif"
    s = data[:200].lstrip()
    if s[:5].lower() in (b"<?xml", b"<svg", b"<html", b"<img"):
        return "image/svg+xml"
    return "application/octet-stream"


def fetch_image(file_id):
    """按 file_id 取图，返回 (content_type, bytes)。"""
    c = get_client()
    url = c.get_download_link(file_id)
    req = urllib.request.Request(url, headers={
        "Referer": "https://yun.139.com/",
        "User-Agent": "Mozilla/5.0",
    })
    with urllib.request.urlopen(req, timeout=60) as r:
        data = r.read()
        ctype = r.headers.get("Content-Type", "").split(";")[0].strip().lower()
    if not ctype or ctype in ("application/octet-stream", "application/oct-stream"):
        ctype = guess_image_type(data)
    return ctype, data


class Handler(BaseHTTPRequestHandler):
    server_version = "obs139-img-proxy"

    def do_GET(self):
        try:
            path = self.path.split("?", 1)[0]
            if path in ("/", "/health"):
                self._json(200, {"ok": True, "service": "obs139-img-proxy", "port": PORT})
                return
            if path.startswith("/img/"):
                fid = path[len("/img/"):].strip()
                if not fid or "/" in fid or "\\" in fid:
                    self._json(400, {"error": "非法的 file_id"})
                    return
                ctype, data = fetch_image(fid)
                self.send_response(200)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "public, max-age=86400")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(data)
                return
            self._json(404, {"error": "not found"})
        except TokenExpired as e:
            self._json(401, {
                "error": f"token 已失效: {e}",
                "hint": "运行 python D:/obs139/139uplink.py --check 查看刷新方法",
            })
        except urllib.error.HTTPError as e:
            self._json(502, {"error": f"拉取图片失败 HTTP {e.code}"})
        except Exception as e:
            self._json(500, {"error": str(e)[:300]})

    def _json(self, code, obj):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass  # 静默，避免刷屏


def main():
    try:
        load_token()
    except Exception as e:
        print(f"启动失败: {e}")
        return 1
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    print(f"✅ obs139 图片代理已启动: http://127.0.0.1:{PORT}/img/<file_id>")
    print("   按 Ctrl+C 停止")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
