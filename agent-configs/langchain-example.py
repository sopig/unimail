"""LangChain 集成示例 - 使用 UniMail tools 创建邮件助手 Agent。

安装依赖：
    pip install unimail[langchain] langchain langchain-openai

运行前确保：
    1. 已通过 `unimail add` 添加至少一个邮箱账户
    2. 设置环境变量 OPENAI_API_KEY
    3. （可选）设置 UNIMAIL_PASSPHRASE
"""

import os

from langchain_openai import ChatOpenAI
from langchain.agents import AgentExecutor, create_tool_calling_agent
from langchain_core.prompts import ChatPromptTemplate

# 导入 UniMail tools
from src.integrations.langchain_tools import get_all_tools


def main():
    # 获取所有 UniMail tools
    tools = get_all_tools()
    print(f"Loaded {len(tools)} UniMail tools:")
    for t in tools:
        print(f"  - {t.name}: {t.description[:60]}...")

    # 创建 LLM
    llm = ChatOpenAI(model="gpt-4o", temperature=0)

    # 创建 prompt
    prompt = ChatPromptTemplate.from_messages([
        ("system", "You are a helpful email assistant. Use the mail tools to help the user manage their emails."),
        ("human", "{input}"),
        ("placeholder", "{agent_scratchpad}"),
    ])

    # 创建 Agent
    agent = create_tool_calling_agent(llm, tools, prompt)
    executor = AgentExecutor(agent=agent, tools=tools, verbose=True)

    # 示例查询
    result = executor.invoke({"input": "Check my inbox for unread emails"})
    print("\n=== Result ===")
    print(result["output"])


if __name__ == "__main__":
    main()
