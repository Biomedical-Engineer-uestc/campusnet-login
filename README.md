# CampusNet Login / 校园网自动登录

一个可扩展的校园网账号密码自动登录工具。支持深澜 Srun、锐捷 ePortal 和常见网页登录表单。
首次运行会自动探测登录门户（也可以手动输入网址），你只需要在本机输入自己的账号和密码。

> English: CampusNet Login is an extensible auto-login tool for Chinese campus networks
> (Srun, Ruijie ePortal, generic web forms). It auto-detects the portal on first run,
> stores the password in the OS credential store, and never sends credentials to
> unconfirmed hosts. See [docs/PORTALS.md](docs/PORTALS.md).

**密码不需要提供给我或任何第三方。** 它只保存在你的电脑上，使用系统安全存储
（Windows DPAPI / macOS Keychain / Linux Secret Service），并且只发送给你确认过的
学校登录页。

> Srun 门户已在真实校园网环境实测。不同学校需要看门户协议；支持范围和边界见
> [docs/PORTALS.md](docs/PORTALS.md)。

## 特性

- 自动识别常见校园网登录门户，首次运行有交互向导；
- 深澜 Srun / 锐捷 ePortal / 通用 HTML 表单三套适配器；
- 密码用 Windows DPAPI 或系统钥匙串加密保存，不写明文到日志；
- 一键联网、掉线自动重连、Windows 登录时自启；
- 密码错误只尝试一次，不自动重试，避免账号被锁；
- 检测到验证码、短信/OTP、SSO、前端 RSA/AES 加密时停止，绝不盲试；
- 通用表单默认拒绝跨域提交密码；
- `--detect` / `--diagnose` 可输出脱敏门户信息，方便提交适配 issue；
- 测试全部使用本地模拟门户，不需要真实账号。

## 支持的校园网类型

| 类型 | 状态 |
| --- | --- |
| 深澜 Srun（账号密码） | ✅ 已支持，真实校园网实测 |
| 锐捷 ePortal（明文密码流程） | ✅ 已实现常见流程（欢迎实测反馈） |
| 通用 HTML 账号/密码表单 | ⚠️ 尽力而为（Dr.COM/H3C/WebAuth 等可能适用） |
| 验证码 / 短信 OTP / SSO / 扫码 | ❌ 需要浏览器手动登录 |
| 前端 RSA/AES 加密密码 | ❌ 检测到会停止，不发送密码 |
| 802.1X / PPPoE | ❌ 不是网页认证 |

详细说明见 [docs/PORTALS.md](docs/PORTALS.md)。

## 适用场景

适合：

- 学校使用网页账号/密码认证，希望免去每次打开浏览器登录；
- 需要一键联网、掉线自动重连或开机自启；
- 想先自动探测登录门户，再在本机安全保存密码；
- Windows 10/11 用户，或能在 Linux/macOS 上运行 Python 的用户。

不适合：

- 需要图片/短信验证码、OTP、SSO、扫码登录的门户；
- 密码经过 RSA/AES 等前端加密、当前适配器未覆盖的门户；
- 802.1X / WPA-Enterprise / PPPoE 等非网页认证方式；
- 纯 JavaScript 渲染、没有普通 HTML 表单的登录页。

## 快速开始

依赖：Python 3.9+、`requests`。Linux/macOS 建议安装 `keyring`（本仓库的
`requirements.txt` 已按平台处理）。

```bash
python -m pip install -r requirements.txt
```

### Windows 便携版

1. 双击 `一键联网.bat`。
2. 首次运行进入设置向导：
   - 自动探测登录页；如果探测不到，会请你粘贴未认证时浏览器打开的登录页地址；
   - 显示最终 URL 和识别到的协议，**确认这是你们学校的页面**；
   - 输入校园网账号和密码（密码输入不回显）；
   - 自动尝试登录并验证。
3. 以后双击 `一键联网.bat` 即可；已经在线时会直接提示。

想开机自动联网：

```text
双击 安装开机自启.bat
```

它会先完成密码设置，然后在当前用户的启动文件夹创建 `CampusNet.lnk`，并立刻启动后台守护。
卸载：

```text
双击 卸载开机自启.bat
```

### 命令行

```bat
python campus_net.py                 :: 连接一次（首次会启动向导）
python campus_net.py --setup         :: 重新设置门户/账号/密码
python campus_net.py --status        :: 查看在线状态
python campus_net.py --watch         :: 掉线自动重连
python campus_net.py --detect        :: 只探测门户，不发送账号密码
python campus_net.py --diagnose      :: 输出脱敏诊断信息（适合发 issue）
python campus_net.py --forget        :: 删除本机保存的密码
python campus_net.py --stop          :: 停止后台守护
```

也可以安装成命令：

```bash
python -m pip install .
campusnet --setup
campusnet
```

Linux/macOS 的核心功能可用；Windows 的 `安装开机自启.bat`/VBS 是 Windows 专用。
在 Linux/macOS 上可用 systemd timer 或 cron 定时执行 `campusnet`。

## 首次设置时注意

- **如果你现在已经能上网**，校园网关不会把你重定向到认证页，自动探测可能只能找到
  已保存的地址或常见网关。此时可以直接粘贴浏览器里未认证时看到的登录页 URL。
- 如果你刚连上校园 Wi-Fi 且尚未认证，让向导自动探测通常最准。
- 使用 `--setup` 时，如果当前已经在线，密码无法真正验证，工具会保存配置并明确提示；
  下次离线时会自动使用。
- 只有在向导里确认过的 URL 才会收到账号密码。
- 自动登录不是“绕过认证”，它只是替你填写学校登录页。

## 密码和隐私

- 默认 `password` 字段留空；首次运行时在本地输入，输入不回显。
- Windows 用 DPAPI 加密保存到 `%LOCALAPPDATA%\CampusNet\credential.dat`，
  只有当前 Windows 账户在这台电脑上能解密。
- Linux/macOS 用 `keyring` 写入系统钥匙串；没有可用后端时不会退化为明文，
  而是每次运行提示输入。
- 日志、诊断输出、测试数据中都不会出现密码。
- 不发送遥测；网络请求只发往学校门户、你的本地网关和公开的联网检测地址
  （Google/Microsoft/Apple/小米的 204 检测端点），不会把账号密码发给这些检测端点。
- 如果完全不想落盘：在 `config.ini` 设置 `remember_password = false`，每次手动输入。
- 如果自己把密码填进 `config.ini` 的 `password`，它会明文保存；不推荐。
- `config.ini` 包含个人账号，已被 `.gitignore` 忽略；**不要提交到公开仓库**。
  仓库里只保留没有个人信息的 `config.example.ini`。

### 自动探测的安全边界

自动探测阶段只发送普通 GET 请求，不发送认证信息。向导会显示最终 URL 并要求确认；
之后的登录只使用已确认并保存的地址。通用表单默认拒绝提交到不同域名。

## 配置

第一次运行会自动生成 `config.ini`。完整模板见 [config.example.ini](config.example.ini)。

最小配置：

```ini
[account]
username = 你的学号
password =
remember_password = true

[portal]
protocol = auto
url =
```

首次向导会把 `protocol` 和 `url` 填好。可选高级项：

```ini
[portal]
# Srun 备用地址
hosts = https://aaa.example.edu.cn, http://10.0.0.2
ac_id = 1
theme = pro

# 锐捷 ePortal
service = 学生服务

# 通用表单
username_field = username
password_field = password
login_url = /do_login
extra_fields = domain=student; service=internet
# auto/plain/md5/sha1/base64
password_encoding = auto
# 默认 false，确认安全后才允许跨域提交
allow_cross_origin = false

# 联网检测地址
connectivity_urls = http://connectivitycheck.gstatic.com/generate_204, http://www.msftconnecttest.com/connecttest.txt

[watch]
interval = 30
log = true
```

通用表单的字段名可以在 `--diagnose` 输出里看到。

## 工作原理

- **Srun**：标准 `get_challenge` + `srun_portal`，加密算法与门户 `Portal.js` 一致
  （MD5 + 自定义 base64 + TEA 变体 xencode + sha1 校验串）。
- **锐捷 ePortal**：`InterFace.do?method=login`，自动读取页面里的 `queryString`、
  `service` 和 `passwordEncrypt`。
- **通用表单**：用 HTML 解析器找账号/密码字段，保留隐藏字段并提交；通过联网检测确认
  是否真正登录成功。
- 每次登录最多尝试一种密码编码/一次密码提交。密码错误、验证码、前端加密等情况
  会立即停止，绝不重试密码。

## 常见问题

| 现象 | 处理 |
| --- | --- |
| 自动探测找不到门户 | 先手动打开未认证页面，复制地址栏 URL，用 `--setup` 粘贴。 |
| 提示前端加密/验证码 | 当前版本不支持这种门户，请用浏览器手动登录，或提交适配 issue。 |
| 密码错误后停止 | 防止账号被锁。运行 `--setup` 确认密码。 |
| 提示在线设备超限 | 先在其它设备退出校园网，再重试。 |
| 开机没有自动联网 | 确认运行过 `安装开机自启.bat`；查看 `%LOCALAPPDATA%\CampusNet\netkeeper.log`。 |
| 想反馈适配问题 | 运行 `--diagnose`，附上门户 URL 和输出；**不要附密码**。 |
| 开了全局 TUN/代理后登录失败 | 暂时关闭全局 TUN/代理再试。 |

## 开发与测试

```bash
python -m unittest discover -s tests -v
```

21 个测试覆盖：Srun 协议向量和请求、锐捷登录/加密拒绝、通用表单明文/MD5/验证码/跨域、
配置迁移、DPAPI、CLI 连接和首次向导。测试全部使用本地模拟门户。

贡献新协议请见 [CONTRIBUTING.md](CONTRIBUTING.md)，支持边界见
[docs/PORTALS.md](docs/PORTALS.md)。

## 安全

见 [SECURITY.md](SECURITY.md)。请不要在公开 issue 中粘贴密码、验证码、Cookie
或完整认证页面。

## License

MIT，见 [LICENSE](LICENSE)。你可以自由修改、分发，但请保留版权声明。
