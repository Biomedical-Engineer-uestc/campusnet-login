# Contributing

欢迎提交新的校园网协议适配、通用表单字段补充和测试。

## 开发环境

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
# Linux/macOS: source .venv/bin/activate
python -m pip install -r requirements.txt
python -m unittest discover -s tests -v
```

测试只使用本地模拟门户，不会连接真实校园网，也不需要真实密码。

## 新增一个协议适配器

1. 在 `campusnet/adapters/` 下新增 `your_protocol.py`，继承
   `campusnet.adapters.base.ProtocolAdapter`。
2. 实现：
   - `detect(page)`：根据 URL、HTML 特征、响应头给出置信度评分；
   - `context_from_page(cfg, page)`：只做页面解析，不发送认证信息；
   - `prepare(context)`：补齐 IP、ac_id、queryString 等参数；
   - `status(context)`：返回 `Status(online=True/False/None)`；
   - `login(context, username, password)`：只尝试一次密码；
   - `verify(context, username)`：登录后确认。
3. 在 `campusnet/adapters/__init__.py` 注册。
4. 在 `tests/mock_servers.py` 增加模拟门户，在 `tests/` 增加：
   - 正常登录；
   - 错误密码只请求一次；
   - 需要验证码/加密时必须在发送密码前拒绝；
   - 跨域提交必须拒绝。
5. 更新 `README.md` 和 `docs/PORTALS.md` 的支持矩阵。

## 适配新的通用网页表单

先让用户运行：

```bash
python campus_net.py --detect
python campus_net.py --diagnose
```

如果页面是简单的 HTML 账号/密码表单，通常无需写代码，直接运行 `--setup` 即可。
如果字段名特殊，可在 `config.ini` 配置：

```ini
[portal]
protocol = generic
username_field = ...
password_field = ...
login_url = ...
extra_fields = key=value; key2=value2
password_encoding = auto
```

如果页面使用验证码、OTP、SSO、RSA/AES 或纯 JavaScript 渲染，自动适配通常不可行；
请按“不支持”处理，不要用穷举密码或绕过验证码的方式实现。

## 提交 issue

请附上：

- 学校/门户类型（不需要学校账号）；
- `campusnet --diagnose` 的脱敏输出；
- 未认证时登录页的 URL；
- 现象描述。

**不要提交密码、验证码、Cookie、会话令牌或包含个人信息的完整页面。**
测试账号也请使用明显的假数据。

## 代码风格

- Python 3.9+，优先标准库，新增第三方依赖需要在 PR 中说明原因。
- 类型标注和 `from __future__ import annotations`。
- 不要在日志里打印密码、密码哈希、Cookie、完整认证请求参数。
- 任何自动化登录都不得自动重试密码错误。

提交即表示你同意以 MIT License 发布你的贡献。
