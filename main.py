"""
功能：提供命令行交互入口，驱动 Supervisor 处理用户输入。
作用：用于本地手工调试完整对话链路和订单补槽流程。
实现方式：初始化配置与编排器，在循环中读取输入并打印结构化结果。
"""

from core.config import Config
from core.logging import logger
from agents.supervisor import SmartVoyageSupervisor

conf = Config()

# 初始化全局变量，用于保存 CLI 会话状态
messages = []  # 存储对话历史消息列表，每个元素为字典{"role": "user/assistant", "content": "消息内容"}
agent_metadata = {}
conversation_history = ""  # 存储整个对话历史字符串，用于意图识别
supervisor = None
pending_order_context = {}


# 初始化 supervisor 与 CLI 所需的会话状态
def initialize_system():
    """
    初始化系统组件，包括 supervisor、代理元信息和会话状态。
    """
    global agent_metadata, conversation_history, supervisor, pending_order_context
    supervisor = SmartVoyageSupervisor(conf)
    agent_metadata = supervisor.agent_metadata

    # 初始化对话历史为空字符串
    conversation_history = ""
    pending_order_context = {}

# 处理用户输入的核心函数
def process_user_input(prompt):
    """
    处理用户输入：识别意图、调用代理、生成响应。
    """
    global messages, conversation_history, supervisor, pending_order_context
    # 添加用户消息到历史
    messages.append({"role": "user", "content": prompt})
    conversation_history += f"\nUser: {prompt}"

    print("正在分析您的意图...")
    try:
        result = supervisor.process_user_input(prompt, conversation_history, pending_order_context)
        response = result["response"]
        pending_order_context = result.get("pending_order_context", {})
        if result.get("metrics"):
            logger.info(f"本轮 metrics: {result['metrics']}")
        if result["routed_agents"]:
            logger.info(f"路由到代理：{result['routed_agents']}")
        conversation_history += f"\nAssistant: {response}"  # 更新历史

        print(f"\n助手回复：\n{response}\n")
        if pending_order_context.get("action") == "hitl_review":
            print("审批模式：请输入 yes 确认执行，或输入 no 取消执行。\n")
        # 添加到消息历史
        messages.append({"role": "assistant", "content": response})

    except Exception as e:
        # 处理其他异常
        logger.error(f"处理异常: {str(e)}")
        error_message = f"处理失败：{str(e)}。请重试。"
        print(f"\n助手回复：\n{error_message}\n")  # 打印错误
        messages.append({"role": "assistant", "content": error_message})

# 显示代理卡片信息
def display_agent_cards():
    """
    显示所有子代理的卡片信息，包括技能、描述和状态。
    """
    print("\n🛠️ Subagent Cards:")
    for agent_name, metadata in agent_metadata.items():
        print(f"\n--- Subagent: {agent_name} ---")
        print(f"技能: {metadata.get('skills', [])}")
        print(f"描述: {metadata.get('description', '')}")
        print(f"状态: 在线")  # 固定状态为在线

if __name__ == "__main__":
    initialize_system()
    print("🤖 基于 Supervisor + MCP + Skill Runtime 的 SmartVoyage 旅行智能助手")
    print("欢迎体验智能对话！输入问题，按回车提交；输入'quit'退出；输入'cards'查看子代理卡片。")
    print(f"当前演示用户：{conf.default_username}")

    # 显示初始代理卡片
    display_agent_cards()

    while True:
        prompt = input("\n请输入您的问题: ").strip()
        if prompt.lower() == 'quit':
            print("感谢使用SmartVoyage！再见！")
            break
        elif prompt.lower() == 'cards':
            display_agent_cards()
            continue
        elif not prompt:
            continue
        else:
            process_user_input(prompt)

    print("\n---")
    print("基于 Supervisor-style Multi-Agent 的旅行助手系统 v3.0")
