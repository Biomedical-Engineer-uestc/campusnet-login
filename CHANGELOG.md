# Changelog

## 2.1.1 - 2026-10-19

- 修复：已经在线时运行一键联网会因 `_log_status` 未定义而抛出 `NameError`。
- 回归测试覆盖“已在线时再次调用 connect_once”。
- 重新构建 Windows 免 Python 独立版。

## 2.1.0 - 2026-10-19

- 后台守护在其它 Wi-Fi 有互联网时先做联网检测，不再反复探测学校门户。
- 网络连续失败时增加 30s → 60s → … → 600s 退避，恢复在线后自动复位。
- 登录页被重定向到未确认域名时停止发送认证信息，防止学校密码发给其它 Wi-Fi 门户。
- 新增 Windows 免 Python 独立版打包（PyInstaller）和 GitHub Release。
- 新增边界测试：其它 Wi-Fi 快速状态、跨域重定向拒绝。

## 2.0.0 - 2026-10-19

- 重构为 `campusnet` 包，保留 `campus_net.py` 兼容入口。
- 新增协议适配框架：
  - 深澜 Srun；
  - 锐捷 ePortal（常见明文密码流程）；
  - 通用 HTML 账号/密码表单（Dr.COM/H3C/WebAuth 等尽力而为）。
- 新增首次运行向导：自动探测门户，或手动输入 URL；确认后输入账号密码。
- 新增自动探测：校园网关重定向、已保存地址、默认网关/常见网关，探测阶段不发送密码。
- 新增通用表单安全边界：验证码/OTP/前端加密/跨域提交默认拒绝。
- 新增 `--detect`、`--diagnose`、`--stop`。
- 密码改为 Windows DPAPI / 系统钥匙串保存；通用平台支持 keyring。
- 新增本地模拟门户测试（Srun/锐捷/通用表单/CLI/向导/DPAPI）。
- 新增 MIT License、贡献指南、安全策略、协议支持说明和 GitHub Actions。

## 1.0.0

- 深澜 Srun 自动登录、DPAPI 密码保存、后台守护和 Windows 自启。
