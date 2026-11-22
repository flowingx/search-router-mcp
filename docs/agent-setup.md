# Search Router MCP 配置与验收提示词

把下面这段发给待配置的 Agent。准备好平台账户后，在本机填写密钥。

---

请帮我在本机配置并验收 Search Router MCP。仓库是 https://github.com/flowingx/search-router-mcp ，教程是 https://flowingx.github.io/search-router-mcp/ 。

1. 克隆仓库或复用本机已有的检出。确认 Python 3.12+ 可用。只有启用知乎时才需要 `uvx`。运行 `python verify.py` 验证 MCP 协议与工具发现。
2. 如果本地密钥文件不存在，用 `search-keys.env.example` 创建 `search-keys.env.local`，通知我在本机编辑器填写需要的服务。也可以沿用已有环境变量。请勿让我在聊天里发送密钥，也不要读取或显示密钥值、把含密钥的文件提交到 Git，或把密钥写进客户端配置。
3. 用 `python verify.py --print-config` 生成 Codex 的 TOML 配置块。只合并 `mcp_servers.search_router` 这一项，保留已有的其他配置和内置搜索设置。其他客户端按其文档注册同一 Python 命令和绝对脚本路径。告诉我是否需要重启客户端或新开会话。
4. 测试脚本通过后，使用当前客户端加载的 `search_router` MCP 工具验收。先调用 `search_status`，按启用的平台各生成一道不同且可公开搜索的问题。Tavily 可测官方资料，Keenable 可测近期论文，知乎可测中文社区经验，Serper 可测通用网页或文档。调用 `web_search` 时为每道题显式指定对应平台，每家先查一次，`limit=1`。如果想验收自动路由，再生成一个带明确主题的问题，把 `provider` 设为 `auto`，检查实际平台与尝试记录。真实搜索可能消耗额度。
5. 对每家报告查询、返回的平台、状态和一个来源链接。缺少密钥记为未配置，余额不足、密钥无效、限流和网络问题按各自状态报出。Keenable 第一次搜索前额度未知属于正常情况。只有当前客户端的 MCP 工具实际返回搜索结果，才能把该平台标为 MCP 验收通过。如果工具未进入当前会话，说明还需重启或重新加载，并明确区分脚本测试和客户端验收。

---
