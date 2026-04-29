import os
import contextlib
import importlib
from mcp.client.stdio import stdio_client
from mcp.client.session import ClientSession
from mcp import StdioServerParameters
from mcp.client.sse import sse_client


class MCPToolSession:
    def __init__(self, command=None, args=None, env=None, sse_url=None):
        self.command = command
        self.args = args
        self.env = env
        self.sse_url = sse_url
        self.session = None
        self.read = None
        self.write = None
        self.client = None
    async def start(self):
        if self.sse_url:
            self.client = sse_client(self.sse_url)
        else:
            self.client = stdio_client(StdioServerParameters(command=self.command, args=self.args, env=self.env))
        self.read, self.write = await self.client.__aenter__()
        self.session = ClientSession(self.read, self.write)
        await self.session.__aenter__()
        await self.session.initialize()
    async def list_tools(self):
        tools_resp = await self.session.list_tools()
        return tools_resp.tools
    async def call_tool(self, tool_name, input_args):
        return await self.session.call_tool(tool_name, input_args)
    async def close(self):
        with contextlib.suppress(Exception):
            if self.session:
                await self.session.__aexit__(None, None, None)
            if self.client:
                await self.client.__aexit__(None, None, None)

async def load_all_tools_from_MCP_servers(mcp_servers=[]):
    tool_registry = {}
    tools = []
    mcp_sessions = []
    for mcp in mcp_servers:
        await mcp.start()
        mcp_sessions.append(mcp)
        # print(f"--------------------正在获取来自{mcp.args}的工具-----------------------------")
        for tool in await mcp.list_tools():
            name = tool.name
            session = mcp.session

            async def tool_func_wrapper(session=session, name=name):
                async def tool_func(**kwargs):
                    return await session.call_tool(name, kwargs)
                return tool_func

            tool_func = await tool_func_wrapper()
            tool_registry[name] = tool_func
            tools.append({
                "type": "function",
                "function": {
                    "name": name,
                    "description": tool.description,
                    "parameters": tool.inputSchema
                }
            })
            # print(f"✅ 成功注册来自MCP_Server的工具：{name}")
    return tools, tool_registry, mcp_sessions

async def load_all_tools_from_local_toolboxes(local_toolboxes=[]):
    mcp_tools,mcp_registry = [],{}
    for toolbox in local_toolboxes:
        # print(f"--------------------正在获取来自本地自定义{toolbox}的工具-----------------------------")
        tools_path_1 = os.path.join(os.getcwd(), 'tools', f'{toolbox}.py')
        tools_path_2 = os.path.join(os.getcwd(), 'agents', 'tools', f'{toolbox}.py')
        if os.path.exists(tools_path_1):
            module_path = f"tools.{toolbox}"
        elif os.path.exists(tools_path_2):
            module_path = f"agents.tools.{toolbox}"
        else:
            continue
        tools_module = importlib.import_module(module_path)   # 动态导入模块
        mcp_registry.update(tools_module.tool_registry)
        mcp_tools.extend(tools_module.tools)
        # print("成功注册本地自定义工具：", [tool["function"]["name"] for tool in mcp_tools])
    return mcp_tools,mcp_registry


