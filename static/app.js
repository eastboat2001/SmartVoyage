const bootstrap = window.SMARTVOYAGE_BOOTSTRAP || {};

const chatLog = document.getElementById("chatLog");
const routeMeta = document.getElementById("routeMeta");
const messageInput = document.getElementById("messageInput");
const sendBtn = document.getElementById("sendBtn");
const resetBtn = document.getElementById("resetBtn");
const hitlOverlay = document.getElementById("hitlOverlay");
const hitlSummary = document.getElementById("hitlSummary");
const approveBtn = document.getElementById("approveBtn");
const rejectBtn = document.getElementById("rejectBtn");
const quickButtons = Array.from(document.querySelectorAll(".quick-actions button"));
const defaultApproveLabel = approveBtn.textContent;
const defaultRejectLabel = rejectBtn.textContent;

const state = {
    username: bootstrap.username || "demo_user",
    messages: [],
    routedAgents: [],
    intents: [],
    pendingOrderContext: {},
    hitlPending: false,
    reviewPayload: {},
    busy: false,
};

function escapeHtml(value) {
    return String(value)
        .replaceAll("&", "&amp;")
        .replaceAll("<", "&lt;")
        .replaceAll(">", "&gt;");
}

function formatRole(role) {
    return role === "user" ? "用户" : "助手";
}

function renderMessages() {
    if (!state.messages.length) {
        chatLog.innerHTML = `
            <article class="message assistant">
                <div class="message-label">助手</div>
                <div>欢迎使用 SmartVoyage Web。你可以直接查询时间、天气、票务，也可以发起下单、退票和改签请求。</div>
            </article>
        `;
        return;
    }

    chatLog.innerHTML = state.messages
        .map((message) => {
            const contentClass = message.pending ? "message-content loading" : "message-content";
            return `
                <article class="message ${message.role}">
                    <div class="message-label">${formatRole(message.role)}</div><div class="${contentClass}">${escapeHtml(message.content)}</div>
                </article>
            `;
        })
        .join("");
    chatLog.scrollTop = chatLog.scrollHeight;
}

function renderRouteMeta() {
    const parts = [];
    if (state.intents.length) {
        parts.push(`意图：${state.intents.join(" / ")}`);
    }
    if (state.routedAgents.length) {
        parts.push(`路由：${state.routedAgents.join(" -> ")}`);
    }
    if (state.hitlPending) {
        parts.push("当前状态：待人工审批");
    }
    routeMeta.textContent = parts.join(" ｜ ");
}

function renderHitlSummary() {
    const payload = state.reviewPayload || {};
    if (!payload || typeof payload !== "object") {
        hitlSummary.textContent = "当前有一笔待审批操作，请先确认或取消。";
        return;
    }

    const actionMap = {
        create_order: "下单",
        cancel_order: "退票",
        change_order: "改签",
    };
    const action = actionMap[payload.action] || "订单操作";
    const lines = [`动作：${action}`];
    if (payload.order_type) {
        lines.push(`类型：${payload.order_type}`);
    }
    if (payload.departure_city || payload.arrival_city) {
        lines.push(`路线：${payload.departure_city || "?"} -> ${payload.arrival_city || "?"}`);
    }
    if (payload.departure_date) {
        lines.push(`日期：${payload.departure_date}`);
    }
    if (payload.transport_no) {
        lines.push(`车次/航班：${payload.transport_no}`);
    }
    if (payload.ticket_type) {
        lines.push(`席位/舱位：${payload.ticket_type}`);
    }
    if (payload.quantity) {
        lines.push(`数量：${payload.quantity}`);
    }
    if (payload.new_departure_date) {
        lines.push(`改后日期：${payload.new_departure_date}`);
    }
    if (payload.new_ticket_type) {
        lines.push(`改后席位：${payload.new_ticket_type}`);
    }

    hitlSummary.textContent = lines.join("\n");
}

function applyComposerState() {
    const disabled = state.busy || state.hitlPending;
    messageInput.disabled = disabled;
    sendBtn.disabled = disabled;
    quickButtons.forEach((button) => {
        button.disabled = disabled;
    });
    approveBtn.disabled = state.busy || !state.hitlPending;
    rejectBtn.disabled = state.busy || !state.hitlPending;
    approveBtn.textContent = state.busy && state.hitlPending ? "处理中..." : defaultApproveLabel;
    rejectBtn.textContent = state.busy && state.hitlPending ? "请稍候" : defaultRejectLabel;
    if (state.hitlPending) {
        messageInput.placeholder = "当前有一笔待审批操作，请先点击 yes 或 no。";
    } else {
        messageInput.placeholder = "输入问题，例如：查询2026-03-21北京到上海的高铁票";
    }
}

function renderHitlOverlay() {
    if (state.hitlPending) {
        renderHitlSummary();
        hitlOverlay.classList.remove("hidden");
    } else {
        hitlOverlay.classList.add("hidden");
    }
}

function renderAll() {
    renderMessages();
    renderRouteMeta();
    renderHitlOverlay();
    applyComposerState();
}

async function requestJson(url, options = {}) {
    const response = await fetch(url, {
        credentials: "same-origin",
        headers: {
            "Content-Type": "application/json",
            ...(options.headers || {}),
        },
        ...options,
    });
    if (!response.ok) {
        throw new Error(`HTTP ${response.status}`);
    }
    return response.json();
}

function normalizeChatPayload(payload) {
    state.messages = Array.isArray(payload.messages) ? payload.messages : [];
    state.routedAgents = Array.isArray(payload.routed_agents) ? payload.routed_agents : [];
    state.intents = Array.isArray(payload.intents) ? payload.intents : [];
    state.pendingOrderContext = payload.pending_order_context || {};
    state.hitlPending = Boolean(payload.hitl_pending);
    state.reviewPayload = payload.review_payload || {};
}

async function sendMessage(message) {
    const trimmed = String(message || "").trim();
    if (!trimmed || state.busy || state.hitlPending) {
        return;
    }
    const previousMessages = [...state.messages];
    state.busy = true;
    state.messages = [
        ...state.messages,
        { role: "user", content: trimmed },
        { role: "assistant", content: "正在处理中...", pending: true },
    ];
    messageInput.value = "";
    renderAll();
    applyComposerState();
    try {
        const payload = await requestJson("/api/chat", {
            method: "POST",
            body: JSON.stringify({ message: trimmed }),
        });
        normalizeChatPayload(payload);
        renderAll();
    } catch (error) {
        state.messages = [
            ...previousMessages,
            { role: "user", content: trimmed },
            { role: "assistant", content: `页面请求失败：${error.message}。请检查后端服务和日志。` },
        ];
        renderAll();
    } finally {
        state.busy = false;
        applyComposerState();
    }
}

async function resolveHitl(decision) {
    if (!state.hitlPending || state.busy) {
        return;
    }
    state.busy = true;
    applyComposerState();
    try {
        const payload = await requestJson("/api/chat", {
            method: "POST",
            body: JSON.stringify({ message: decision }),
        });
        normalizeChatPayload(payload);
        renderAll();
    } catch (error) {
        state.messages.push({
            role: "assistant",
            content: `审批请求失败：${error.message}。请重试，或查看 logs/app.log 与 logs/a2a.log。`,
        });
        renderAll();
    } finally {
        state.busy = false;
        applyComposerState();
    }
}

async function bootstrapSession() {
    state.busy = true;
    applyComposerState();
    try {
        const payload = await requestJson("/api/bootstrap", { method: "GET" });
        normalizeChatPayload(payload);
    } catch (error) {
        state.messages = [
            {
                role: "assistant",
                content: `初始化页面失败：${error.message}。请确认 web_app.py 已启动。`,
            },
        ];
    } finally {
        state.busy = false;
        renderAll();
    }
}

sendBtn.addEventListener("click", () => {
    void sendMessage(messageInput.value);
});

messageInput.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey) {
        event.preventDefault();
        void sendMessage(messageInput.value);
    }
});

resetBtn.addEventListener("click", async () => {
    if (state.busy) {
        return;
    }
    state.busy = true;
    applyComposerState();
    try {
        await requestJson("/api/reset", { method: "POST", body: "{}" });
        state.messages = [];
        state.routedAgents = [];
        state.intents = [];
        state.pendingOrderContext = {};
        state.hitlPending = false;
        state.reviewPayload = {};
        renderAll();
    } catch (error) {
        state.messages.push({
            role: "assistant",
            content: `重置会话失败：${error.message}。`,
        });
        renderAll();
    } finally {
        state.busy = false;
        applyComposerState();
    }
});

quickButtons.forEach((button) => {
    button.addEventListener("click", () => {
        void sendMessage(button.dataset.prompt || "");
    });
});

approveBtn.addEventListener("click", () => {
    void resolveHitl("yes");
});

rejectBtn.addEventListener("click", () => {
    void resolveHitl("no");
});

void bootstrapSession();
