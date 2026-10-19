# -*- coding: utf-8 -*-
"""轻量 HTML 表单解析，供通用表单和锐捷 ePortal 适配器使用。"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import Optional

_SKIP_INPUT_TYPES = {
    "submit",
    "button",
    "reset",
    "file",
    "image",
    "checkbox",
    "radio",
}
_CAPTCHA_TOKENS = (
    "captcha",
    "validcode",
    "verifycode",
    "vcode",
    "checkcode",
    "验证码",
)
_OTP_TOKENS = ("otp", "smscode", "sms_code", "dynamiccode", "动态码", "短信验证")
_USERNAME_STRONG = (
    "username",
    "user_name",
    "userid",
    "user_id",
    "account",
    "accountname",
    "loginname",
    "login_name",
    "uname",
    "studentid",
    "student_id",
    "stu_id",
    "stuno",
    "学号",
    "账号",
    "用户名",
    "手机号",
    "mobile",
    "phone",
    "email",
)
_USERNAME_WEAK = ("user", "login", "name", "id", "account")
_USERNAME_EXCLUDE = (
    "search",
    "keyword",
    "captcha",
    "vcode",
    "code",
    "otp",
    "sms",
    "token",
    "password",
    "pwd",
    "submit",
)


@dataclass
class FormField:
    name: str
    value: str = ""
    type: str = "text"
    field_id: str = ""
    placeholder: str = ""
    autocomplete: str = ""
    checked: bool = False
    disabled: bool = False
    required: bool = False
    options: list[dict] = field(default_factory=list)  # [{value,label,selected}]

    @property
    def identity(self) -> str:
        return " ".join(
            part.lower()
            for part in (
                self.name,
                self.field_id,
                self.placeholder,
                self.autocomplete,
            )
            if part
        )


@dataclass
class HtmlForm:
    action: str = ""
    method: str = "GET"
    form_id: str = ""
    name: str = ""
    fields: list[FormField] = field(default_factory=list)

    def find(self, name: str) -> Optional[FormField]:
        low = name.lower()
        for item in self.fields:
            if item.name.lower() == low:
                return item
        return None

    @property
    def password_fields(self) -> list[FormField]:
        return [f for f in self.fields if f.type == "password" and not f.disabled]

    @property
    def text_fields(self) -> list[FormField]:
        return [
            f
            for f in self.fields
            if f.type in ("text", "email", "tel", "number", "search", "url")
            and not f.disabled
        ]

    def visible_fields(self) -> list[FormField]:
        return [
            f
            for f in self.fields
            if f.type not in ("hidden", "password") and not f.disabled
        ]


@dataclass
class FormSelection:
    form: HtmlForm
    username_field: FormField
    password_field: FormField
    captcha_fields: list[FormField] = field(default_factory=list)
    otp_fields: list[FormField] = field(default_factory=list)
    score: float = 0.0
    notes: list[str] = field(default_factory=list)


class _FormParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.forms: list[HtmlForm] = []
        self.current: Optional[HtmlForm] = None
        self.select: Optional[FormField] = None
        self.option: Optional[dict] = None
        self.textarea: Optional[FormField] = None

    def handle_starttag(self, tag, attrs_list):
        attrs = {k.lower(): (v or "") for k, v in attrs_list}
        tag = tag.lower()

        if tag == "form":
            if self.current:
                self.forms.append(self.current)
            self.current = HtmlForm(
                action=attrs.get("action", ""),
                method=(attrs.get("method") or "get").upper(),
                form_id=attrs.get("id", ""),
                name=attrs.get("name", ""),
            )
            return

        if self.current is None:
            return

        if tag == "input":
            ftype = (attrs.get("type") or "text").lower()
            name = attrs.get("name", "")
            if not name:
                return
            if ftype in ("checkbox", "radio") and "checked" not in attrs:
                return
            self.current.fields.append(
                FormField(
                    name=name,
                    value=attrs.get("value", ""),
                    type=ftype,
                    field_id=attrs.get("id", ""),
                    placeholder=attrs.get("placeholder", ""),
                    autocomplete=attrs.get("autocomplete", ""),
                    checked="checked" in attrs,
                    disabled="disabled" in attrs,
                    required="required" in attrs,
                )
            )
            return

        if tag == "select":
            name = attrs.get("name", "")
            if name:
                self.select = FormField(
                    name=name,
                    type="select",
                    field_id=attrs.get("id", ""),
                    disabled="disabled" in attrs,
                    required="required" in attrs,
                )
            return

        if tag == "option" and self.select is not None:
            self.option = {
                "value": attrs.get("value", ""),
                "label": "",
                "selected": "selected" in attrs,
            }
            return

        if tag == "textarea":
            name = attrs.get("name", "")
            if name:
                self.textarea = FormField(
                    name=name,
                    type="textarea",
                    field_id=attrs.get("id", ""),
                    disabled="disabled" in attrs,
                    required="required" in attrs,
                )

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag == "form" and self.current is not None:
            self.forms.append(self.current)
            self.current = None
            return
        if tag == "select" and self.select is not None:
            if self.current is not None and self.select.name:
                options = self.select.options
                chosen = next((o for o in options if o.get("selected")), None)
                if chosen is None:
                    chosen = next(
                        (o for o in options if (o.get("value") or o.get("label"))),
                        None,
                    )
                if chosen:
                    self.select.value = chosen.get("value") or chosen.get("label", "")
                else:
                    self.select.value = ""
                self.current.fields.append(self.select)
            self.select = None
            self.option = None
            return
        if tag == "textarea" and self.textarea is not None:
            if self.current is not None and self.textarea.name:
                self.current.fields.append(self.textarea)
            self.textarea = None

    def handle_data(self, data):
        if self.option is not None:
            self.option["label"] += data
        if self.textarea is not None:
            self.textarea.value += data

    def close(self):
        super().close()
        if self.current is not None:
            self.forms.append(self.current)
            self.current = None


def parse_forms(html: str) -> list[HtmlForm]:
    parser = _FormParser()
    try:
        parser.feed(html or "")
        parser.close()
    except Exception:
        pass
    return parser.forms


def _username_score(field: FormField) -> int:
    identity = field.identity
    if any(token in identity for token in _USERNAME_EXCLUDE):
        return -10
    if field.autocomplete.lower() in ("username", "email"):
        return 30
    if any(token in identity for token in _USERNAME_STRONG):
        return 20
    if any(token in identity for token in _USERNAME_WEAK):
        return 8
    if field.type in ("email", "tel"):
        return 6
    if field.required:
        return 3
    return 1


def _is_captcha(field: FormField) -> bool:
    identity = field.identity
    return any(token in identity for token in _CAPTCHA_TOKENS)


def _is_otp(field: FormField) -> bool:
    identity = field.identity
    return any(token in identity for token in _OTP_TOKENS)


def select_login_form(
    forms: list[HtmlForm],
    username_field_override: str = "",
    password_field_override: str = "",
) -> Optional[FormSelection]:
    best: Optional[FormSelection] = None

    for form in forms:
        passwords = form.password_fields
        if not passwords:
            continue
        password_field = None
        if password_field_override:
            password_field = form.find(password_field_override)
        if password_field is None:
            password_field = passwords[0]

        texts = form.text_fields
        username_field = None
        if username_field_override:
            username_field = form.find(username_field_override)
        if username_field is None and texts:
            username_field = max(texts, key=_username_score)
            if _username_score(username_field) < 0:
                username_field = None
        if username_field is None and texts:
            username_field = texts[0]
        if username_field is None:
            continue

        captcha_fields = [f for f in form.visible_fields() if _is_captcha(f)]
        otp_fields = [f for f in form.visible_fields() if _is_otp(f)]
        visible = form.visible_fields()

        score = 0.5
        if _username_score(username_field) >= 20:
            score += 0.2
        elif _username_score(username_field) > 0:
            score += 0.05
        if any(
            token in password_field.identity
            for token in ("password", "passwd", "pwd")
        ):
            score += 0.1
        if form.action:
            score += 0.03
        if captcha_fields:
            score -= 0.45
        if otp_fields:
            score -= 0.25
        if len(visible) > 8:
            score -= 0.15

        selection = FormSelection(
            form=form,
            username_field=username_field,
            password_field=password_field,
            captcha_fields=captcha_fields,
            otp_fields=otp_fields,
            score=round(score, 2),
        )
        if best is None or selection.score > best.score:
            best = selection

    return best


def collect_hidden_fields(form: HtmlForm) -> dict[str, str]:
    data: dict[str, str] = {}
    for item in form.fields:
        if item.disabled or item.type in ("submit", "button", "reset", "file", "image"):
            continue
        if item.type in ("checkbox", "radio") and not item.checked:
            continue
        data[item.name] = item.value
    return data


def detect_password_encoding(html: str) -> tuple[str, list[str]]:
    """返回 (plain|md5|sha1|base64|unsupported|unknown, 证据)。"""
    low = (html or "").lower()
    notes: list[str] = []
    if not low:
        return "unknown", notes

    windows = []
    for match in re.finditer(r"(password|passwd|pwd)", low):
        start = max(0, match.start() - 260)
        end = min(len(low), match.start() + 360)
        windows.append(low[start:end])
    for match in re.finditer(r"(?:md5|sha1|base64|btoa)\s*\(", low):
        start = max(0, match.start() - 120)
        end = min(len(low), match.start() + 240)
        windows.append(low[start:end])

    if not windows:
        return "unknown", notes
    text = "\n".join(windows)
    unsupported_markers = (
        "jsencrypt",
        "publickey",
        "public_key",
        "rsa",
        "aes",
        "strenc",
        "sm2",
        "sm4",
        "encryptpwd",
        "encryptpassword",
        "encrypt(",
    )
    for marker in unsupported_markers:
        if marker in text:
            notes.append(f"检测到前端加密标记：{marker}")
            return "unsupported", notes

    if "hex_md5" in text or re.search(r"\bmd5\s*\(", text):
        notes.append("检测到 MD5")
        return "md5", notes
    if re.search(r"\bsha1\s*\(", text):
        notes.append("检测到 SHA1")
        return "sha1", notes
    if "btoa(" in text or "base64" in text:
        notes.append("检测到 Base64")
        return "base64", notes
    return "plain", notes


def detect_captcha_image(html: str) -> bool:
    low = (html or "").lower()
    return bool(
        re.search(
            r"<img[^>]+src=[\"'][^\"']*(captcha|verify|validcode|vcode)[^\"']*[\"']",
            low,
        )
    )


def detect_sso(html: str) -> bool:
    low = (html or "").lower()
    return any(
        token in low
        for token in (
            "oauth",
            "cas/login",
            "saml",
            "统一身份认证",
            "企业微信登录",
            "扫码登录",
        )
    )
