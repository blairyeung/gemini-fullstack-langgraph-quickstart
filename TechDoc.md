# Gemini Fullstack LangGraph 快速入门

本文档提供了 Gemini Fullstack LangGraph 快速入门项目的技术概述，包括其架构、代理设计、工具和工作流程。

## 项目结构

该项目是一个单一代码库，包含两个主要组件：

- `frontend/`：一个基于 React 的用户界面，使用 Vite、TypeScript 和 Tailwind CSS 构建。
- `backend/`：一个由 LangGraph 和 FastAPI 驱动的基于 Python 的后端，托管研究代理。

### 关键文件和目录

- `frontend/src/App.tsx`：管理应用程序状态和用户交互的主要 React 组件。
- `backend/src/agent/graph.py`：定义 LangGraph 代理，包括其节点、边和整体工作流程。
- `backend/src/agent/prompts.py`：包含在研究过程的每个步骤中指导代理行为的提示。
- `backend/src/agent/state.py`：定义 LangGraph 代理使用的状态对象。
- `backend/src/agent/tools_and_schemas.py`：包含用于数据验证的 Pydantic 模式和代理使用的工具。
- `docker-compose.yml`：配置用于生产部署的服务，包括后端、Redis 和 Postgres。
- `Makefile`：提供用于开发的便捷命令，例如启动前端和后端服务器。

## 代理架构

该项目的核心是由 LangGraph 驱动的研究代理。该代理旨在通过迭代搜索网页、反思结果并优化搜索，直到获得足够的信息以提供有充分依据的答案，从而对给定主题进行全面研究。

### 代理状态

代理的状态由 `OverallState` TypedDict 管理，其中包括以下关键字段：

- `messages`：对话中的消息列表。
- `search_query`：当前搜索查询。
- `web_research_result`：网络搜索结果。
- `sources_gathered`：研究期间找到的来源的 URL 列表。
- `research_loop_count`：研究迭代次数。

### 代理工作流程

代理的工作流程在 `backend/src/agent/graph.py` 中定义为一个状态图。该图由以下节点组成：

1. **`generate_query`**：根据用户输入生成初始搜索查询。此节点使用 `query_writer_instructions` 提示生成 `SearchQueryList`。
2. **`web_research`**：使用 DuckDuckGo 搜索 API 执行网络搜索。此节点使用 `web_searcher_instructions` 提示总结搜索结果。
3. **`reflection`**：分析搜索结果以识别知识差距并生成后续查询。此节点使用 `reflection_instructions` 提示生成 `Reflection` 对象。
4. **`finalize_answer`**：将收集到的信息综合成带有引用的最终答案。此节点使用 `answer_instructions` 提示生成最终响应。

代理根据图中定义的条件在这些状态之间转换，例如研究是否充分或是否已达到最大迭代次数。

### 详细图表分析：`backend/src/agent/graph.py`

`graph.py` 文件是代理的核心，定义了其结构和逻辑。以下是其关键组件的细分：

- **节点**：每个函数（`generate_query`、`web_research`、`reflection`、`finalize_answer`）代表图中的一个节点。这些节点负责研究过程中的特定任务。
- **边**：`builder.add_edge()` 和 `builder.add_conditional_edges()` 调用定义了节点之间的连接。这些边控制代理的流程，确定接下来执行哪个节点。
- **状态**：`OverallState` TypedDict 是图的共享状态。每个节点都可以读取和写入此状态，从而允许它们共享信息并协作完成研究任务。
- **配置**：`Configuration` 类允许您自定义代理的行为，例如要使用的语言模型和要执行的研究循环次数。
- **提示**：`backend/src/agent/prompts.py` 中的提示对于指导代理的行为至关重要。每个提示都经过精心设计，以指导语言模型如何执行其任务，例如生成搜索查询或反思研究结果。

## 工具和模式

代理使用以下工具和模式：

- **`SearchQueryList`**：用于验证代理生成的搜索查询列表的 Pydantic 模式。
- **`Reflection`**：用于验证反思过程的 Pydantic 模式，包括研究是否充分以及任何已识别的知识差距。
- **DuckDuckGo 搜索 API**：用于执行网络搜索的工具。

## 技术细节

- **后端框架**：FastAPI
- **代理框架**：LangGraph
- **LLM**：Google Gemini
- **前端框架**：React (with Vite)
- **样式**：Tailwind CSS 和 Shadcn UI
- **部署**：Docker

## 工作流程

### 开发

1. 安装前端和后端的依赖项。
2. 使用您的 Gemini API 密钥设置 `.env` 文件。
3. 运行 `make dev` 以启动前端和后端的开发服务器。

### 生产

1. 使用提供的 `Dockerfile` 构建 Docker 镜像。
2. 运行 `docker-compose up` 以启动生产服务，包括后端、Redis 和 Postgres。