# 新建 Agent SOP（标准操作流程）

## 概述

本文档描述了在 OpenLOA Server 中创建新 Agent 的标准步骤。请按照以下步骤进行操作。

## 步骤

### 1. 创建 Agent 主文件

在 `agents` 目录下创建一个以 Agent 名字命名的 `{agent_name}.py` 文件。

参考现有的 `Lucy.py` 的实现方法进行编写。

### 2. 创建 Agent 工具定义文件

在 `agents/tools` 目录下创建 `{agent_name}_tools.py` 文件。

**任务内容：**
- 定义新 Agent 会需要用到的所有 tool 和 schema
- 参考 `Lucy_tools.py` 的格式和写法
- 如果tool内部需要调用LLM，可以导入使用agents\utils\com.py里定义的request_LLM_api函数，并在configs\tools.yaml里添加对应LLM的API配置

### 3. 优化 Agent 配置

根据 tools 的设计方式，优化 `{agent_name}.py` 中的 `system_prompt`。

**要求：**
- 确保 Agent 能够按照规范合理运用 tools
- 在 `local_tool_boxes` 中导入 `{agent_name}_tools`

### 4. 添加 Agent 配置信息

根据新 Agent 的功能，在 `configs/profiles.yaml` 中添加新 Agent 的信息。

**参考方式：** 仿照已有的 Lucy Agent 的写法

## 相关文件结构

```
agents/
├── Lucy.py                    # 现有 Agent 实例（参考）
├── {agent_name}.py       # 新建的 Agent 主文件
├── tools/
│   ├── {agent_name}_tools.py # 新建的 Agent 工具定义
│   └── Lucy_tools.py         # 现有 Agent 工具定义（参考）

configs/
└── profiles.yaml             # Agent 配置文件
```