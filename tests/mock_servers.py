# -*- coding: utf-8 -*-
"""测试用的本地模拟门户。"""

from __future__ import annotations

import json
import threading
from functools import partial
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from campusnet.adapters.srun import build_auth_payload


class BaseHandler(BaseHTTPRequestHandler):
    state = None

    def log_message(self, fmt, *args):
        pass

    def send_body(self, body: str, ctype="text/html; charset=utf-8", status=200, headers=None):
        raw = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(raw)))
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(raw)

    def send_jsonp(self, obj):
        self.send_body(
            "cb(" + json.dumps(obj, ensure_ascii=False) + ");",
            "application/javascript; charset=utf-8",
        )

    def send_json(self, obj):
        self.send_body(
            json.dumps(obj, ensure_ascii=False),
            "application/json; charset=utf-8",
        )

    def read_form(self):
        length = int(self.headers.get("Content-Length", "0") or 0)
        raw = self.rfile.read(length).decode("utf-8", "replace") if length else ""
        return {key: values[0] for key, values in parse_qs(raw).items()}


class SrunState:
    def __init__(self):
        self.username = "test-user"
        self.password = "test-pass"
        self.online = False
        self.login_attempts = 0
        self.token = "0123456789abcdef" * 4


class SrunHandler(BaseHandler):
    def __init__(self, *args, state: SrunState, **kwargs):
        self.state = state
        super().__init__(*args, **kwargs)

    def do_GET(self):
        parsed = urlparse(self.path)
        query = {key: values[0] for key, values in parse_qs(parsed.query).items()}
        if parsed.path == "/srun_portal_pc":
            html = """<!DOCTYPE html><html><head><title>Mock Campus</title></head>
<body>srun portal
<script>
var CONFIG = {
  page: 'account',
  acid: "1",
  ip: "127.0.0.1",
  lang: "zh-CN",
  portal: {"AuthIP":"","AuthIP6":"","ServiceIP":""},
  notice: "one"
};
</script>
</body></html>"""
            self.send_body(html)
            return
        if parsed.path == "/cgi-bin/rad_user_info":
            if self.state.online:
                self.send_jsonp(
                    {
                        "error": "ok",
                        "error_msg": "",
                        "res": "ok",
                        "ecode": 0,
                        "user_name": self.state.username,
                        "online_ip": "127.0.0.1",
                        "products_name": "学生",
                        "billing_name": "测试",
                        "all_bytes": 123456789,
                        "online_device_total": "1",
                    }
                )
            else:
                self.send_jsonp(
                    {
                        "error": "not_online_error",
                        "error_msg": "you are not online",
                        "res": "not_online_error",
                        "ecode": "E2901",
                    }
                )
            return
        if parsed.path == "/cgi-bin/get_challenge":
            if query.get("username") != self.state.username:
                self.send_jsonp({"error": "user_not_found", "error_msg": "user not found"})
                return
            self.send_jsonp(
                {
                    "challenge": self.state.token,
                    "client_ip": query.get("ip", "127.0.0.1"),
                    "online_ip": query.get("ip", "127.0.0.1"),
                    "error": "ok",
                    "ecode": 0,
                    "expire": "60",
                }
            )
            return
        if parsed.path == "/cgi-bin/srun_portal":
            self.state.login_attempts += 1
            ip = query.get("ip", "127.0.0.1")
            ac_id = query.get("ac_id", "1")
            expected = build_auth_payload(
                self.state.username, self.state.password, ip, ac_id, self.state.token
            )
            actual = (query.get("password"), query.get("info"), query.get("chksum"))
            if actual == ("{MD5}" + expected[0], expected[1], expected[2]):
                self.state.online = True
                self.send_jsonp(
                    {
                        "error": "ok",
                        "suc_msg": "login_ok",
                        "res": "ok",
                        "ecode": 0,
                        "user_name": self.state.username,
                    }
                )
            else:
                self.send_jsonp(
                    {
                        "error": "password_error",
                        "error_msg": "密码错误",
                        "ecode": "E2553",
                        "res": "password_error",
                    }
                )
            return
        self.send_body("not found", "text/plain", 404)


class RuijieState:
    def __init__(self):
        self.username = "test-user"
        self.password = "test-pass"
        self.online = False
        self.login_attempts = 0
        self.encryption_required = False
        self.last_form = {}
        self.query_string = "userip=10.0.0.5&wlanacname=mock&nasip=10.0.0.1"


class RuijieHandler(BaseHandler):
    def __init__(self, *args, state: RuijieState, **kwargs):
        self.state = state
        super().__init__(*args, **kwargs)

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path in ("/", "/eportal/index.jsp"):
            html = f"""<!DOCTYPE html><html><head><title>ePortal</title></head>
<body>
<div>eportal 锐捷登录</div>
<script>
var queryString = "{self.state.query_string}";
var service = "默认服务";
var passwordEncrypt = {"true" if self.state.encryption_required else "false"};
</script>
<form method="post" action="/ePortal/InterFace.do?method=login">
  <input type="text" name="userId">
  <input type="password" name="password">
  <select name="service"><option value="默认服务" selected>默认服务</option></select>
</form>
</body></html>"""
            self.send_body(html)
            return
        if parsed.path == "/generate_204":
            if self.state.online:
                self.send_body("", "text/plain", 204)
            else:
                self.send_body(
                    "",
                    "text/plain",
                    302,
                    {"Location": "/eportal/index.jsp"},
                )
            return
        self.send_body("not found", "text/plain", 404)

    def do_POST(self):
        parsed = urlparse(self.path)
        if parsed.path == "/ePortal/InterFace.do" and "login" in parsed.query:
            form = self.read_form()
            self.state.last_form = form
            self.state.login_attempts += 1
            if (
                form.get("userId") == self.state.username
                and form.get("password") == self.state.password
                and form.get("service") == "默认服务"
                and form.get("queryString") == self.state.query_string
                and form.get("passwordEncrypt") == "false"
            ):
                self.state.online = True
                self.send_json({"result": "success", "message": "登录成功"})
            else:
                self.send_json({"result": "fail", "message": "用户名或密码错误"})
            return
        self.send_body("not found", "text/plain", 404)


class GenericState:
    def __init__(self):
        self.username = "test-user"
        self.password = "test-pass"
        self.mode = "plain"  # plain / md5 / captcha / cross
        self.online = False
        self.login_attempts = 0
        self.last_form = {}


class GenericHandler(BaseHandler):
    def __init__(self, *args, state: GenericState, **kwargs):
        self.state = state
        super().__init__(*args, **kwargs)

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/login":
            action = (
                "http://example.invalid/do_login"
                if self.state.mode == "cross"
                else "/do_login"
            )
            extra = ""
            script = ""
            if self.state.mode == "captcha":
                extra = (
                    '<input type="text" name="validcode" placeholder="验证码">'
                    '<img src="/captcha.png" alt="验证码">'
                )
            if self.state.mode == "md5":
                script = (
                    "<script>var p=document.getElementById('pwd');"
                    "var h=md5(p.value);</script>"
                )
            html = f"""<!DOCTYPE html><html><head><title>校园网登录</title></head>
<body>
<form method="post" action="{action}">
  <input type="hidden" name="csrf" value="token-123">
  <input type="text" name="username" placeholder="学号">
  <input type="password" name="password" id="pwd">
  {extra}
  {script}
  <input type="submit" value="登录">
</form>
</body></html>"""
            self.send_body(html)
            return
        if parsed.path == "/generate_204":
            if self.state.online:
                self.send_body("", "text/plain", 204)
            else:
                self.send_body(
                    "", "text/plain", 302, {"Location": "/login"}
                )
            return
        self.send_body("not found", "text/plain", 404)

    def do_POST(self):
        parsed = urlparse(self.path)
        if parsed.path == "/do_login":
            form = self.read_form()
            self.state.last_form = form
            self.state.login_attempts += 1
            expected_password = self.state.password
            if self.state.mode == "md5":
                import hashlib

                expected_password = hashlib.md5(
                    self.state.password.encode("utf-8")
                ).hexdigest()
            if (
                form.get("csrf") == "token-123"
                and form.get("username") == self.state.username
                and form.get("password") == expected_password
            ):
                self.state.online = True
                self.send_body("登录成功", "text/plain; charset=utf-8")
            else:
                self.send_body("用户名或密码错误", "text/plain; charset=utf-8")
            return
        self.send_body("not found", "text/plain", 404)


def start_server(handler_cls, state):
    server = ThreadingHTTPServer(
        ("127.0.0.1", 0), partial(handler_cls, state=state)
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, server.server_address[1]
