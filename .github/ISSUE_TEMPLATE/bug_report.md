---
name: Bug 报告
about: 评测工具/题库/适配器的缺陷报告
labels: bug
---

**环境**
- 部署形态：☐ openKylin 原生（deb） ☐ Windows 宿主 + 评测 VM ☐ 纯本机
- 版本：`memhall --version` 输出
- 判卷口径：scripted / llm（兼容历史 dual 名称，请附 manifest 实际值）

**复现步骤**

**预期 vs 实际**

**证据**
run 目录路径（注意：含 heldout 用例的 run 先跑 `scripts/redact_heldout.py` 再贴）、
报错栈 / UI 截图 / `~/.memhall/memhall.log` 相关段落。

请先移除真实凭据和个人信息；安全漏洞按照SECURITY.md私密报告。
