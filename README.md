# Search Router MCP

一个本地 MCP，将 Tavily、Keenable、知乎和 Serper 合并成一个搜索工具。Agent 根据查询意图选择主题，路由器结合额度选平台，并记录余额不足、密钥错误、限流、网络错误和回退原因。

**[配置网站](https://flowingx.github.io/search-router-mcp/) · [Agent 配置与验收提示词](docs/agent-setup.md)**

## 配置

需要 [Python 3.12+](https://www.python.org/downloads/)。知乎额外依赖 [uvx](https://docs.astral.sh/uv/getting-started/installation/)。

```sh
git clone https://github.com/flowingx/search-router-mcp.git
cd search-router-mcp
```

Windows PowerShell 使用 `Copy-Item search-keys.env.example search-keys.env.local`，macOS/Linux 使用 `cp search-keys.env.example search-keys.env.local`。已有本地密钥文件时直接编辑它。每行在等号后填写一个值，留空的服务可通过同名环境变量配置，也可跳过。

| 平台 | 环境变量 | 密钥入口 |
|---|---|---|
| Tavily | `TAVILY_API_KEY` | [Tavily 控制台](https://app.tavily.com/) |
| Keenable | `KEENABLE_API_KEY` | [Keenable 控制台](https://app.keenable.ai/) |
| 知乎 | `ZHIHU_ACCESS_SECRET` | [知乎数据开放平台](https://developer.zhihu.com/)的 Access Secret |
| Serper | `SERPER_API_KEY` | [Serper 控制台](https://serper.dev/) |

密钥文件已被 Git 忽略。配置页面无需输入密钥。

```sh
python verify.py
python verify.py --print-config
```

在 macOS/Linux 上，如果系统的命令是 `python3`，把示例里的 `python` 改成 `python3`。将输出的 `[mcp_servers.search_router]` 配置块合并到 Codex 的 `~/.codex/config.toml`，重启 Codex 或新开会话以加载工具。其他 MCP 客户端可用 stdio 注册同一启动命令。具体客户端格式见 [Codex MCP 文档](https://developers.openai.com/codex/mcp)。

## 测试

```sh
python verify.py --status
python verify.py --check auto "替换成你的测试问题"
```

`verify.py` 默认检查 MCP 握手和工具发现。`--status` 读取可查询的额度，`--check PROVIDER QUERY` 发起一次真实搜索，可以重复传入。`PROVIDER` 可使用 `auto`、`tavily`、`keenable`、`zhihu`、`serper`。脚本测试通过后，把 [验收提示词](docs/agent-setup.md) 发给你的 Agent，让它自行生成测试问题并直接调用已注册的 MCP 工具。

## 路由与额度

| 主题参数 | 优先平台 |
|---|---|
| `china` 国内话题与社区讨论 | 知乎 |
| `research` 论文与科研 | Keenable |
| `official` 政府与官方资料 | Tavily |
| `general` 通用搜索 | Serper |

`web_search` 的 `provider=auto` 会按主题、额度和失败状态从多家平台中选择。指定平台时只调用该平台，适合测试。返回结果包含实际平台、额度状态、搜索结果和 `attempts`。`search_status` 单独返回平台状态，余额为 `null` 表示未知。Tavily、知乎和 Serper 可主动查询额度。Keenable 余额来自最近一次成功搜索，初次调用或缓存过期后会显示未知。真实搜索可能消耗相应平台额度，知乎保留其客户端的免费额度保护。

常见状态包括 `missing_key`（缺少密钥）、`invalid_key`（密钥无效）、`access_denied`（无访问权限）、`insufficient_quota`（可用额度耗尽）、`rate_limited`（请求限流）、`network_error`（连接失败）和 `local_state_unwritable`（知乎本地保护目录无法写入）。
