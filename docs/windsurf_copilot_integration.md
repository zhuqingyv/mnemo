# Windsurf 与 GitHub Copilot MCP 集成技术调研

> 调研日期：2026-05-11  
> 数据来源：官方文档、技术博客、实测配置

## 概述

用户希望补充 Windsurf 和 GitHub Copilot 对 mnemo 的 MCP 支持。本文档调研这两个平台的 MCP 配置方式、文件路径、格式差异，并提出在 mnemo 中实现支持的方案。

**核心发现：**
1. **Windsurf**：基于 VS Code 的 IDE，MCP 配置位于 `~/.codeium/windsurf/mcp_config.json`，支持 stdio 和 HTTP 两种传输模式
2. **GitHub Copilot CLI**：独立命令行工具，配置位于 `~/.copilot/mcp-config.json`，支持 stdio 和 HTTP 模式
3. **GitHub Copilot in VS Code**：使用 VS Code 的 MCP 配置机制（`.vscode/mcp.json` 或用户全局配置）

---

## 1. Windsurf MCP 集成

### 基本信息

| 项目 | 值 |
|------|---|
| 产品 | Windsurf IDE (基于 VS Code) |
| 官方文档 | [Cascade - MCP 协议](https://geekdaxue.co/read/Windsurf-doc/mcp) |
| 配置格式 | JSON |
| 配置路径 | `~/.codeium/windsurf/mcp_config.json` |
| MCP 字段 | `mcpServers` |
| 提示词支持 | 不支持（仅支持 Tools） |
| 支持传输模式 | stdio（目前仅支持 stdio，HTTP 支持待确认） |

### 配置结构

```json
{
  "mcpServers": {
    "server-name": {
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-example"],
      "env": {
        "API_KEY": "<YOUR_API_KEY>"
      }
    }
  }
}
```

### 重要限制

1. **仅支持 stdio 传输**：当前 Windsurf 仅支持 stdio 模式的 MCP 服务器
2. **仅支持 Tools**：不支持 Prompts 和 Resources
3. **工具输出限制**：不支持输出图片的工具
4. **计费考虑**：工具调用消耗 Flow Action credits，即使调用失败也会扣费

### 配置方式

1. **通过设置 UI**：Advanced Settings → Cascade → "Add Server"
2. **手动编辑文件**：直接编辑 `~/.codeium/windsurf/mcp_config.json`
3. **配置后需要刷新**：添加或修改后需点击刷新按钮使配置生效

### 与现有 mnemo 集成模式的差异

1. **文件路径**：不同于其他 agent 的 `~/.config/agent` 模式，使用 `~/.codeium/windsurf/` 目录
2. **字段名称**：与大多数 agent 一致，使用 `mcpServers` 字段
3. **传输模式**：目前仅支持 stdio，需要 mnemo 提供 stdio 模式配置

---

## 2. GitHub Copilot CLI MCP 集成

### 基本信息

| 项目 | 值 |
|------|---|
| 产品 | GitHub Copilot CLI |
| 官方文档 | [Adding MCP servers for GitHub Copilot CLI](https://docs.github.com/en/copilot/how-tos/copilot-cli/customize-copilot/add-mcp-servers) |
| 配置格式 | JSON |
| 配置路径 | `~/.copilot/mcp-config.json` |
| MCP 字段 | `mcpServers` |
| 支持传输模式 | stdio (`"local"`/`"stdio"`)、HTTP (`"http"`/`"sse"`) |

### 配置结构

```json
{
  "mcpServers": {
    "playwright": {
      "type": "local",
      "command": "npx",
      "args": ["@playwright/mcp@latest"],
      "env": {},
      "tools": ["*"]
    },
    "context7": {
      "type": "http",
      "url": "https://mcp.context7.com/mcp",
      "headers": {
        "CONTEXT7_API_KEY": "YOUR-API-KEY"
      },
      "tools": ["*"]
    }
  }
}
```

### 关键字段说明

1. **`type`**：服务器类型
   - `"local"` 或 `"stdio"`：本地命令模式
   - `"http"` 或 `"sse"`：远程 HTTP 模式
2. **`command` + `"args"`**：stdio 模式下的执行命令
3. **`url`**：HTTP 模式下的服务器 URL
4. **`headers`**：HTTP 模式下的认证头
5. **`tools`**：可选，指定启用哪些工具，`["*"]` 表示全部启用

### 配置方式

1. **通过 CLI 命令**：`copilot /mcp add`（交互式）
2. **手动编辑文件**：直接编辑 `~/.copilot/mcp-config.json`
3. **即时生效**：配置修改后立即生效，无需重启 CLI

### 内置服务器

GitHub Copilot CLI 内置了 GitHub MCP 服务器，无需额外配置。

### 与现有 mnemo 集成模式的差异

1. **`type` 字段**：需要显式指定服务器类型
2. **`tools` 字段**：支持工具选择，可选配置
3. **配置路径**：使用 `~/.copilot/` 目录

---

## 3. GitHub Copilot in VS Code MCP 集成

### 基本信息

| 项目 | 值 |
|------|---|
| 产品 | GitHub Copilot in Visual Studio Code |
| 官方文档 | [Add and manage MCP servers in VS Code](https://code.visualstudio.com/docs/copilot/customization/mcp-servers) |
| 配置格式 | JSON |
| 配置作用域 | 工作区 (`.vscode/mcp.json`) 或用户全局 |
| MCP 字段 | `servers` |
| 支持传输模式 | stdio、HTTP |

### 配置结构

**工作区配置** (`.vscode/mcp.json`):
```json
{
  "servers": {
    "github": {
      "type": "http",
      "url": "https://api.githubcopilot.com/mcp"
    },
    "playwright": {
      "command": "npx",
      "args": ["-y", "@microsoft/mcp-server-playwright"]
    }
  }
}
```

**用户全局配置**：
- 通过命令 `MCP: Open User Configuration` 访问
- 文件位置因平台而异

### 关键特性

1. **双作用域**：
   - 工作区配置：项目特定，可提交到版本控制
   - 用户全局配置：跨所有项目生效
2. **安全信任**：首次启动 MCP 服务器时需要用户确认信任
3. **沙箱支持**：macOS/Linux 可启用沙箱限制文件系统和网络访问
4. **同步支持**：可通过 Settings Sync 跨设备同步配置

### 配置方式

1. **扩展视图**：搜索 `@mcp` 安装服务器
2. **命令面板**：`MCP: Add Server`（引导式）
3. **命令行**：`code --add-mcp` 添加到用户配置
4. **手动编辑**：直接编辑 `mcp.json` 文件

### 与现有 mnemo 集成模式的差异

1. **字段名称**：使用 `servers` 而非 `mcpServers`
2. **作用域机制**：支持工作区和用户全局配置
3. **信任机制**：需要用户显式确认信任
4. **配置路径**：工作区配置在 `.vscode/` 目录下

---

## 4. 与现有 mnemo 支持的对比分析

### 配置路径对比

| Agent | 配置路径 | 格式 | MCP 字段 | 提示词路径 |
|-------|----------|------|----------|------------|
| Claude Code | `~/.claude.json` | JSON | `mcpServers` | `~/.claude/CLAUDE.md` |
| Cursor | `~/.cursor/mcp.json` | JSON | `mcpServers` | `.cursorrules` |
| Codex CLI | `~/.codex/config.toml` | TOML | `mcp_servers` | `AGENTS.md` |
| Qwen Code | `~/.qwen/settings.json` | JSON | `mcpServers` | `~/.qwen/QWEN.md` |
| Gemini CLI | `~/.gemini/settings.json` | JSON | `mcpServers` | `~/.gemini/GEMINI.md` |
| CodeBuddy | `~/.codebuddy/.mcp.json` | JSON | `mcpServers` | `~/.codebuddy/CODEBUDDY.md` |
| **Windsurf** | `~/.codeium/windsurf/mcp_config.json` | JSON | `mcpServers` | 不支持 |
| **GitHub Copilot CLI** | `~/.copilot/mcp-config.json` | JSON | `mcpServers` | 无独立提示词 |
| **GitHub Copilot in VS Code** | `.vscode/mcp.json` 或用户全局 | JSON | `servers` | 无独立提示词 |

### 字段差异总结

1. **MCP 字段名**：
   - 大多数：`mcpServers`
   - Codex CLI：`mcp_servers`（TOML）
   - VS Code：`servers`
2. **传输模式字段**：
   - 大多数：隐式（`command`/`args` 表示 stdio，`url`/`type` 表示 HTTP）
   - GitHub Copilot CLI：显式 `type` 字段（`"local"`/`"stdio"`/`"http"`）
   - VS Code：`type` 字段（`"stdio"` 或 `"http"`）
3. **工具选择**：GitHub Copilot CLI 支持 `tools` 字段限制可用工具

---

## 5. 在 mnemo 中实现的建议方案

### 5.1 总体策略

遵循 mnemo 现有的 **CLI-first, fallback to file write** 策略：

1. **检测客户端**：在 `client_detector.py` 中添加 Windsurf 和 GitHub Copilot CLI 的条目
2. **配置注入**：在 `config_writer.py` 中适配新的配置格式差异
3. **CLI 命令支持**：如果客户端有 CLI 命令则优先使用，否则直接编辑文件

### 5.2 具体实现步骤

#### 步骤 1：更新 `client_detector.py`

添加以下条目到 `_CLIENTS` 列表：

```python
{
    "name": "windsurf",
    "config_path": "~/.codeium/windsurf/mcp_config.json",
    "prompt_path": None,  # Windsurf 不支持提示词注入
    "prompt_target": None,
    "format": "json",
    "mcp_field": "mcpServers",
},
{
    "name": "github-copilot-cli",
    "config_path": "~/.copilot/mcp-config.json",
    "prompt_path": None,  # GitHub Copilot CLI 无独立提示词文件
    "prompt_target": None,
    "format": "json",
    "mcp_field": "mcpServers",
},
# 注意：GitHub Copilot in VS Code 使用 VS Code 的配置机制
# 可考虑添加 "vscode" 客户端支持，但需要处理工作区/全局双作用域
```

#### 步骤 2：更新 `config_writer.py`

需要处理以下差异：

1. **GitHub Copilot CLI 的 `type` 字段**：
   - stdio 模式：`"type": "local"`（或 `"stdio"`）
   - HTTP 模式：`"type": "http"`
2. **GitHub Copilot CLI 的 `tools` 字段**：可设置为 `["*"]` 启用所有工具
3. **字段名称统一**：使用 `mcpServers` 字段

#### 步骤 3：CLI 命令映射

检查 Windsurf 和 GitHub Copilot CLI 是否有可用的 CLI 命令：

1. **Windsurf**：目前无官方 CLI 用于 MCP 配置，使用文件编辑
2. **GitHub Copilot CLI**：有 `copilot /mcp add` 命令，可在 `_CLIENT_CLI_MAP` 中添加映射

```python
_CLIENT_CLI_MAP: dict[str, list[str]] = {
    # ... 现有映射
    "github-copilot-cli": ["copilot"],
}
```

#### 步骤 4：配置模板生成

为每个客户端生成正确的配置块：

**Windsurf (stdio)**:
```json
{
  "command": "mnemo",
  "args": ["mcp"]
}
```

**GitHub Copilot CLI (stdio)**:
```json
{
  "type": "local",
  "command": "mnemo",
  "args": ["mcp"],
  "tools": ["*"]
}
```

**GitHub Copilot CLI (HTTP)**:
```json
{
  "type": "http",
  "url": "http://127.0.0.1:8787/mcp/http/mcp",
  "tools": ["*"]
}
```

### 5.3 关于 GitHub Copilot in VS Code 的考虑

VS Code 的 MCP 配置机制较为特殊：

1. **双作用域**：需要支持工作区 (`.vscode/mcp.json`) 和用户全局配置
2. **字段名称**：使用 `servers` 而非 `mcpServers`
3. **信任机制**：首次使用需要用户确认

**建议方案**：
1. **短期**：不直接支持 VS Code，让用户手动配置或通过其他 agent（如 Claude Code）使用 mnemo
2. **长期**：添加 `vscode` 客户端支持，处理工作区/全局配置，并添加信任提示

### 5.4 关于 Windsurf 的 HTTP 支持

当前 Windsurf 文档仅提及 stdio 支持，但 GitHub 集成示例显示可能支持 HTTP。需要进一步验证：

1. **测试 HTTP 配置**：尝试配置 HTTP 模式的 MCP 服务器
2. **版本要求**：Windsurf ≥1.101 可能支持 Streamable HTTP
3. **备用方案**：如果 HTTP 不支持，则仅提供 stdio 配置

---

## 6. 验证与测试计划

### 6.1 验证环境

1. **Windsurf**：
   - 安装最新版 Windsurf IDE
   - 验证 `~/.codeium/windsurf/mcp_config.json` 文件存在
   - 测试 stdio 模式配置

2. **GitHub Copilot CLI**：
   - 安装 GitHub Copilot CLI (`npm install -g @githubnext/github-copilot-cli`)
   - 验证 `~/.copilot/mcp-config.json` 文件存在
   - 测试 stdio 和 HTTP 两种模式

### 6.2 测试用例

1. **配置注入测试**：
   - `mnemo setup` 应能检测到 Windsurf 和 GitHub Copilot CLI
   - 配置应正确写入对应文件
   - 配置格式应符合客户端要求

2. **功能测试**：
   - 在 Windsurf 中调用 mnemo 工具
   - 在 GitHub Copilot CLI 中调用 mnemo 工具
   - 验证工具响应正常

3. **配置移除测试**：
   - `mnemo setup --uninstall` 应能正确移除配置
   - 配置文件应恢复原状

### 6.3 兼容性考虑

1. **向后兼容**：新增客户端不应影响现有客户端检测
2. **配置合并**：如果配置文件已存在其他 MCP 服务器，应保留原有配置
3. **错误处理**：文件权限问题、格式错误等应有适当错误提示

---

## 7. 实施优先级建议

### P0（核心功能）

1. **GitHub Copilot CLI 支持**：
   - 配置路径明确，格式标准
   - 有官方 CLI 命令支持
   - 用户基数较大

2. **Windsurf stdio 支持**：
   - 配置路径明确，格式简单
   - 仅需支持 stdio 模式

### P1（增强功能）

1. **Windsurf HTTP 支持验证**：
   - 测试 HTTP 模式是否可用
   - 如果支持则添加 HTTP 配置选项

2. **GitHub Copilot in VS Code 基础支持**：
   - 用户全局配置注入
   - 基础信任处理

### P2（高级功能）

1. **VS Code 工作区配置支持**：
   - 项目级 `.vscode/mcp.json` 配置
   - 团队协作场景

2. **配置同步优化**：
   - 多客户端配置一致性
   - 配置冲突解决

---

## 8. 结论

Windsurf 和 GitHub Copilot 的 MCP 集成在技术上是可行的，且与 mnemo 现有架构兼容。主要差异在于配置路径和字段格式，这些差异可以通过扩展 `client_detector.py` 和 `config_writer.py` 来处理。

**建议立即实施：**
1. 添加 GitHub Copilot CLI 和 Windsurf 到客户端检测列表
2. 适配 GitHub Copilot CLI 的 `type` 和 `tools` 字段
3. 提供 stdio 模式配置（两者都支持）

**需要进一步调研：**
1. Windsurf 对 HTTP 模式 MCP 的支持情况
2. VS Code MCP 配置的最佳集成方式

通过上述实现，mnemo 将能够为 Windsurf 和 GitHub Copilot 用户提供无缝的知识管理体验，进一步扩大用户覆盖范围。