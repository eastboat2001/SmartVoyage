from config import Config
from create_logger import logger
from utils.orchestrator import SmartVoyageOrchestrator

conf = Config()

# 初始化全局变量，用于模拟会话状态   这些变量替换了Streamlit的session_state
messages = []  # 存储对话历史消息列表，每个元素为字典{"role": "user/assistant", "content": "消息内容"}
agent_network = None  # 代理网络实例
agent_urls = {}  # 存储代理的URL信息字典
conversation_history = ""  # 存储整个对话历史字符串，用于意图识别
orchestrator = None
pending_order_context = {}


# 初始化代理网络和相关组件   此部分在脚本启动时执行一次，模拟Streamlit的初始化
def initialize_system():
    """
    初始化系统组件，包括代理网络、路由器、LLM和会话状态
    核心逻辑：构建AgentNetwork，添加代理，创建路由器和LLM
    """
    global agent_network, agent_urls, conversation_history, orchestrator, pending_order_context
    orchestrator = SmartVoyageOrchestrator(conf)
    agent_urls = orchestrator.agent_urls
    agent_network = orchestrator.agent_network

    # 初始化对话历史为空字符串
    conversation_history = ""
    pending_order_context = {}

# 处理用户输入的核心函数
# 此函数模拟Streamlit的输入处理逻辑，包括意图识别、路由和响应生成
def process_user_input(prompt):
    """
    处理用户输入：识别意图、调用代理、生成响应
    核心逻辑：使用LLM进行意图识别，根据意图路由到相应代理或直接生成内容
    """
    global messages, conversation_history, orchestrator, pending_order_context
    # 添加用户消息到历史
    messages.append({"role": "user", "content": prompt})
    conversation_history += f"\nUser: {prompt}"

    print("正在分析您的意图...")
    try:
        result = orchestrator.process_user_input(prompt, conversation_history, pending_order_context)
        response = result["response"]
        pending_order_context = result.get("pending_order_context", {})
        if result["routed_agents"]:
            logger.info(f"路由到代理：{result['routed_agents']}")
        conversation_history += f"\nAssistant: {response}"  # 更新历史

        # 输出助手响应（模拟Streamlit的显示）
        print(f"\n助手回复：\n{response}\n")  # 打印响应
        # 添加到消息历史
        messages.append({"role": "assistant", "content": response})

    except Exception as e:
        # 处理其他异常
        logger.error(f"处理异常: {str(e)}")
        error_message = f"处理失败：{str(e)}。请重试。"
        print(f"\n助手回复：\n{error_message}\n")  # 打印错误
        messages.append({"role": "assistant", "content": error_message})

# 显示代理卡片信息
# 此函数模拟Streamlit的右侧Agent Card，打印代理详情
def display_agent_cards():
    """
    显示所有代理的卡片信息，包括技能、描述、地址和状态
    核心逻辑：遍历代理网络，获取并打印卡片内容
    """
    print("\n🛠️ Agent Cards:")
    for agent_name in agent_network.agents.keys():
        # 获取代理卡片
        agent_card = agent_network.get_agent_card(agent_name)
        agent_url = agent_urls.get(agent_name, "未知地址")
        print(f"\n--- Agent: {agent_name} ---")
        print(f"技能: {agent_card.skills}")
        print(f"描述: {agent_card.description}")
        print(f"地址: {agent_url}")
        print(f"状态: 在线")  # 固定状态为在线

# 主函数：脚本入口
# 初始化系统并进入交互循环
if __name__ == "__main__":
    # 预定2025年12月18日北京到上海的火车票，要求二等座

    # 初始化系统
    initialize_system()
    print("🤖 基于A2A的SmartVoyage旅行智能助手")
    print("欢迎体验智能对话！输入问题，按回车提交；输入'quit'退出；输入'cards'查看代理卡片。")
    print(f"当前演示用户：{conf.default_username}")

    # 显示初始代理卡片
    display_agent_cards()

    # 交互循环：模拟Streamlit的连续输入
    while True:
        # 获取用户输入
        prompt = input("\n请输入您的问题: ").strip()
        if prompt.lower() == 'quit':
            print("感谢使用SmartVoyage！再见！")
            break
        elif prompt.lower() == 'cards':  # 查看卡片条件
            display_agent_cards()  # 重新显示卡片
            continue
        elif not prompt:  # 空输入跳过
            continue
        else:
            # 处理输入
            process_user_input(prompt)  # 调用核心处理函数

    # 脚本结束时打印页脚信息
    print("\n---")
    print("Powered by 黑马程序员 | 基于Agent2Agent的旅行助手系统 v2.0")
