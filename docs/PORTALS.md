# 校园网协议支持说明

CampusNet Login 不是“所有校园网都能用”的万能工具。它按协议适配，不同学校的门户
差异很大。下面是当前的支持范围和边界。

## 支持矩阵

| 类型 | 状态 | 说明 |
| --- | --- | --- |
| 深澜 Srun，账号密码 | ✅ 支持 | 标准 `/srun_portal_pc` + `/cgi-bin/get_challenge` + `/cgi-bin/srun_portal`。已在真实校园网环境实测。 |
| 锐捷 ePortal，明文密码 | ✅ 常见流程 | 常见 `/eportal/index.jsp` + `InterFace.do?method=login` 流程，自动读取 `service`/`queryString`。已用模拟门户覆盖测试，欢迎各校实测反馈。 |
| 通用 HTML 表单 | ⚠️ 尽力而为 | 页面里有普通账号/密码 `<form>` 即可尝试。Dr.COM、H3C、WebAuth、校园自建门户可能落在这一类。 |
| Srun 变种/前端加密 | ❌ | 密码经过 RSA/AES/JSEncrypt 等前端加密，或需要额外签名。检测到会在发送密码前停止。 |
| 锐捷 `passwordEncrypt=true` | ❌ | 需要复现学校页面的 JS 加密函数。检测到会在发送密码前停止。 |
| 验证码 | ❌ | 图片/短信/邮箱验证码无法在无人值守时可靠处理。 |
| 短信/OTP/动态码 | ❌ | 需要人工输入。 |
| SSO / CAS / 统一身份认证 / 扫码 | ❌ | 登录流程不是单一账号密码表单。 |
| 802.1X / WPA-Enterprise | ❌ | 不是网页认证。 |
| PPPoE 拨号 | ❌ | 不是网页认证。 |
| 纯 JavaScript 渲染的登录页 | ❌ | 当前只用 HTML 解析和 HTTP 请求，不执行 JS。 |

## 自动探测做了什么

首次运行向导会：

1. 尝试已保存的门户地址；
2. 请求几个公开的联网检测地址，读取校园网网关的 302 跳转；
3. 从默认网关和少量常见网关地址探测；
4. 解析页面特征，判断是 Srun、锐捷还是通用表单；
5. 显示候选 URL 和判断依据，由用户确认。

**探测阶段只发送 GET 请求，不发送任何账号密码。**
确认之前不会向任何新地址提交认证信息。

## 通用表单适配器能做什么

- 自动找账号/密码字段（按 name/id/placeholder/autocomplete 和常见中英文关键词）；
- 保留隐藏字段、select 选项、textarea 和默认值；
- 自动识别页面里常见的 MD5 / SHA1 / Base64 密码处理；
- 支持在 `config.ini` 里覆盖字段名、登录 URL、额外字段和密码编码；
- 默认拒绝提交到不同域名（可显式设置 `allow_cross_origin = true`）。

它**不会**：

- 绕过验证码；
- 猜测/穷举多种密码编码（每次只尝试一种，避免账号锁定）；
- 执行页面 JavaScript；
- 在页面没有账号/密码表单时盲试接口。

## 遇到不支持的学校怎么办

先运行：

```bash
python campus_net.py --detect
python campus_net.py --diagnose
```

`--diagnose` 输出会脱敏：不包含密码、Cookie、会话令牌和完整页面内容。

提交 issue 时请附上：

- 学校 / 门户类型（不需要账号）；
- 未认证时浏览器打开的门户 URL；
- `--diagnose` 输出；
- 页面大致长什么样（截图请打码账号）。

不要提交密码、验证码、Cookie、完整 HTML 中的 token。

## 想自己适配

见 [CONTRIBUTING.md](../CONTRIBUTING.md)。新增协议需要：

- 实现 `ProtocolAdapter`；
- 用本地 mock 覆盖成功、密码错误、验证码/加密拒绝、跨域拒绝；
- 更新本文件的支持矩阵。
