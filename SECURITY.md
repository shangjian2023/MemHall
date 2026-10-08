# 安全策略

## 支持范围

最新 dev 分支与最近一个 tag 版本。

## 上报渠道

安全问题请勿开公开 issue——发邮件至维护者（仓库 Owner 页可见联系方式），
或用 GitHub 私有安全通告（Security → Report a vulnerability）。

## 威胁面速览（评测工具特有）

- **凭据**：网关真上游 key 只应存在于网关进程环境（GATEWAY_UPSTREAM_KEY），
  任何把真实 key 写进代码/日志/命令行的行为都算漏洞
- **SSH 车道**：VM 密码走环境变量（VM_PASS），TOFU 钉扎在 ~/.memhall/known_hosts
- **入站鉴权**：网关绑 0.0.0.0 时 LAN 内任意主机可访问，凭据不合法一律 401
  （memhall-* 派生 dummy 或 GATEWAY_INBOUND_TOKEN）
- **不可见题池**：heldout 题目文本不得出现在公开产物中（分享前
  `scripts/redact_heldout.py`）
