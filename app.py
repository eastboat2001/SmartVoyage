"""
app.py 是一个 Streamlit 前端客户端脚本，将所有的智能体和流程串联起来，完成对话流。
作用：在 SmartVoyage 项目中，前端客户端作为用户交互层，提供图形界面输入查询，展示路由结果和响应，提升用户体验，同时展示代理卡片信息。
项目中定位：客户端是用户入口，收集查询，调用路由服务器识别意图，路由到代理，显示结果。
数据流：用户输入 → 意图识别 → 代理调用 → 结果展示。

核心功能：
    初始化网络
    对用户的意图进行识别，并进行改写
    处理用户查询，路由到代理，发送任务，解析结果。
    展示聊天消息、代理卡片和页脚。
"""
import os
import sys
sys.path.append(os.path.join(os.path.dirname(__file__), ".."))
import streamlit as st

from config import Config
from create_logger import logger
from utils.orchestrator import SmartVoyageOrchestrator

conf = Config()

# 设置页面配置
st.set_page_config(page_title="基于A2A的SmartVoyage旅行助手系统", layout="wide", page_icon="🤖")

# 自定义 CSS 打造高端大气科技感，优化对比度
st.markdown("""
<style>
/* 聊天消息框样式 */
.stChatMessage {
    background-color: #2c3e50 !important;
    border-radius: 12px !important;
    padding: 15px !important;
    margin-bottom: 15px !important;
    box-shadow: 0 3px 6px rgba(0,0,0,0.2) !important;
}

/* 用户消息框稍亮 */
.stChatMessage.user {
    background-color: #34495e !important;
}

/* ✅ 核心：强制所有文字变为白色（包括 markdown 内部） */
.stChatMessage .stMarkdown, 
.stChatMessage .stMarkdown p, 
.stChatMessage .stMarkdown span, 
.stChatMessage .stMarkdown div, 
.stChatMessage .stMarkdown strong, 
.stChatMessage .stMarkdown em, 
.stChatMessage .stMarkdown code {
    color: #ffffff !important; 
}

/* 如果你想让 emoji 图标更亮一点 */
.stChatMessage [data-testid="stChatMessageAvatarIcon"] {
    filter: brightness(1.2);
}
</style>
""", unsafe_allow_html=True)

# 初始化会话状态
if "messages" not in st.session_state:
    st.session_state.messages = []
if "agent_network" not in st.session_state:
    orchestrator = SmartVoyageOrchestrator(conf)
    st.session_state.agent_urls = orchestrator.agent_urls
    st.session_state.agent_network = orchestrator.agent_network
    st.session_state.orchestrator = orchestrator
    st.session_state.conversation_history = ""

# 主界面布局
st.title("🤖 基于A2A的SmartVoyage旅行智能助手")
st.markdown("欢迎体验智能对话！输入问题，系统将精准识别意图并提供服务。")

# 两栏布局：左侧对话，右侧 Agent Card
col1, col2 = st.columns([2, 1])

# 左侧对话区域
with col1:
    st.subheader("💬 对话")
    # 对话历史
    for message in st.session_state.messages:
        with st.chat_message(message["role"]):
            st.markdown(message["content"])

    # 输入框
    if prompt := st.chat_input("请输入您的问题..."):
        # 显示用户消息
        with st.chat_message("user"):
            st.markdown(prompt)
        st.session_state.messages.append({"role": "user", "content": prompt})
        st.session_state.conversation_history += f"\nUser: {prompt}"

        with st.spinner("正在分析您的意图..."):
            try:
                result = st.session_state.orchestrator.process_user_input(
                    prompt,
                    st.session_state.conversation_history,
                )
                response = result["response"]
                if result["routed_agents"]:
                    logger.info(f"路由到代理：{result['routed_agents']}")
                st.session_state.conversation_history += f"\nAssistant: {response}"

                # 显示助手消息
                with st.chat_message("assistant"):
                    st.markdown(response)
                st.session_state.messages.append({"role": "assistant", "content": response})
            except Exception as e:
                logger.error(f"处理异常: {str(e)}")
                error_message = f"处理失败：{str(e)}。请重试。"
                with st.chat_message("assistant"):
                    st.markdown(error_message)
                st.session_state.messages.append({"role": "assistant", "content": error_message})

# 右侧 Agent Card 区域
with col2:
    st.subheader("🛠️ AgentCard")
    for agent_name in st.session_state.agent_network.agents.keys():
        agent_card = st.session_state.agent_network.get_agent_card(agent_name)
        agent_url = st.session_state.agent_urls.get(agent_name, "未知地址")
        with st.expander(f"Agent: {agent_name}", expanded=False):
            st.markdown(f"<div class='card-title'>技能</div>", unsafe_allow_html=True)
            st.markdown(f"<div class='card-content'>{agent_card.skills}</div>", unsafe_allow_html=True)
            st.markdown(f"<div class='card-title'>描述</div>", unsafe_allow_html=True)
            st.markdown(f"<div class='card-content'>{agent_card.description}</div>", unsafe_allow_html=True)
            st.markdown(f"<div class='card-title'>地址</div>", unsafe_allow_html=True)
            st.markdown(f"<div class='card-content'>{agent_url}</div>", unsafe_allow_html=True)
            st.markdown(f"<div class='card-title'>状态</div>", unsafe_allow_html=True)
            st.markdown(f"<div class='card-content'>在线</div>", unsafe_allow_html=True)

# 页脚
st.markdown("---")
st.markdown('<div class="footer">Powered by 黑马程序员 | 基于Agent2Agent的旅行助手系统 v2.0</div>', unsafe_allow_html=True)
