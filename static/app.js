/* ==========================================================
   SmartVoyage — 前端交互逻辑
   ========================================================== */
(function () {
    'use strict';

    /* ---------- DOM 引用 ---------- */
    const $ = (s, root) => (root || document).querySelector(s);
    const $$ = (s, root) => [...(root || document).querySelectorAll(s)];

    const pageShell = $('.page-shell');
    const sidebar = $('#sidebar');
    const sidebarToggle = $('#sidebarToggle');
    const sidebarOverlay = $('#sidebarOverlay');
    const chatLog = $('#chatLog');
    const input = $('#messageInput');
    const sendBtn = $('#sendBtn');
    const resetBtn = $('#resetBtn');
    const welcomeState = $('#welcomeState');
    const routeMeta = $('#routeMeta');
    const hitlOverlay = $('#hitlOverlay');
    const hitlSummary = $('#hitlSummary');
    const approveBtn = $('#approveBtn');
    const rejectBtn = $('#rejectBtn');

    /* ---------- 状态 ---------- */
    const boot = window.SMARTVOYAGE_BOOTSTRAP || {};
    let sessionId = boot.sessionId || '';
    let hitlContext = null;
    let isSending = false;

    /* ---------- 工具函数 ---------- */
    function renderMd(text) {
        if (!text) return '';
        return text
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
            .replace(/`([^`]+)`/g, '<code>$1</code>')
            .replace(/\n/g, '<br>');
    }

    function escHtml(s) {
        return String(s)
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;');
    }

    function timeNow() {
        const d = new Date();
        return `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`;
    }

    function setLock(locked) {
        isSending = locked;
        input.disabled = locked;
        sendBtn.disabled = locked;
        $$('.quick-chip').forEach((b) => {
            b.disabled = locked;
        });
    }

    function hasHitlPending(data) {
        return Boolean(data && data.hitl_pending && data.pending_order_context && data.pending_order_context.action === 'hitl_review');
    }

    function buildRouteMeta(data) {
        if (!data) return null;
        return {
            intent: Array.isArray(data.intents) && data.intents.length ? data.intents.join(', ') : '',
            routedAgent: Array.isArray(data.routed_agents) && data.routed_agents.length ? data.routed_agents.join(', ') : '',
            pending: hasHitlPending(data)
        };
    }

    /* ---------- 侧边栏 ---------- */
    function initSidebar() {
        if (window.innerWidth <= 1080) {
            pageShell.classList.add('sidebar-collapsed');
        }
    }

    sidebarToggle.addEventListener('click', () => {
        pageShell.classList.toggle('sidebar-collapsed');
    });

    sidebarOverlay.addEventListener('click', () => {
        pageShell.classList.add('sidebar-collapsed');
    });

    /* ---------- Agent Cards 折叠 ---------- */
    $$('.panel-header[data-collapse]').forEach((btn) => {
        btn.addEventListener('click', () => {
            const targetId = btn.getAttribute('data-collapse');
            const panel = document.getElementById(targetId);
            if (panel) {
                panel.classList.toggle('collapsed');
                const chevron = btn.querySelector('.collapse-chevron');
                if (chevron) {
                    chevron.style.transform = panel.classList.contains('collapsed') ? 'rotate(-90deg)' : '';
                }
            }
        });
    });

    /* ---------- 自适应 Textarea ---------- */
    function autoResize() {
        input.style.height = 'auto';
        input.style.height = Math.min(input.scrollHeight, 160) + 'px';
    }
    input.addEventListener('input', autoResize);

    /* ---------- 消息渲染 ---------- */
    function addMessage(role, content) {
        if (welcomeState && !welcomeState.classList.contains('hidden')) {
            welcomeState.classList.add('hidden');
        }

        const wrap = document.createElement('div');
        wrap.className = `message ${role}`;

        const isUser = role === 'user';
        const header = document.createElement('div');
        header.className = 'message-header';
        header.innerHTML = `
            <span class="message-label">${isUser ? '你' : 'SmartVoyage'}</span>
            <span class="message-time">${timeNow()}</span>
        `;
        wrap.appendChild(header);

        const body = document.createElement('div');
        body.className = 'message-content';
        body.innerHTML = isUser ? escHtml(content) : renderMd(content);
        wrap.appendChild(body);

        if (!isUser) {
            const copyBtn = document.createElement('button');
            copyBtn.className = 'message-copy';
            copyBtn.title = '复制';
            copyBtn.innerHTML = `<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="9" width="13" height="13" rx="2" ry="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>`;
            copyBtn.addEventListener('click', () => {
                navigator.clipboard.writeText(content).then(() => {
                    copyBtn.classList.add('copied');
                    copyBtn.innerHTML = `<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"/></svg>`;
                    setTimeout(() => {
                        copyBtn.classList.remove('copied');
                        copyBtn.innerHTML = `<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="9" width="13" height="13" rx="2" ry="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>`;
                    }, 2000);
                });
            });
            wrap.appendChild(copyBtn);
        }

        chatLog.appendChild(wrap);
        scrollToBottom();
    }

    function scrollToBottom() {
        requestAnimationFrame(() => {
            chatLog.scrollTo({ top: chatLog.scrollHeight, behavior: 'smooth' });
        });
    }

    /* ---------- 打字指示器 ---------- */
    function showTyping() {
        removeTyping();
        const el = document.createElement('div');
        el.id = 'typingIndicator';
        el.className = 'typing-indicator message assistant';
        el.innerHTML = '<span></span><span></span><span></span>';
        chatLog.appendChild(el);
        scrollToBottom();
    }

    function removeTyping() {
        const el = document.getElementById('typingIndicator');
        if (el) el.remove();
    }

    /* ---------- 路由元信息渲染 ---------- */
    function renderRouteMeta(data) {
        routeMeta.innerHTML = '';
        const meta = buildRouteMeta(data);
        if (!meta) return;

        const pills = [];
        if (meta.intent) {
            pills.push(`<span class="route-pill intent">意图 ${escHtml(meta.intent)}</span>`);
        }
        if (meta.routedAgent) {
            pills.push(`<span class="route-pill agent">→ ${escHtml(meta.routedAgent)}</span>`);
        }
        if (meta.pending) {
            pills.push('<span class="route-pill hitl">⏳ 待审批</span>');
        }
        routeMeta.innerHTML = pills.join('');
    }

    /* ---------- HITL 审批 ---------- */
    function showHitl(ctx) {
        hitlContext = ctx;
        const summary = (ctx && ctx.review_payload && ctx.review_payload.summary) || ctx.summary || JSON.stringify(ctx, null, 2);
        hitlSummary.textContent = summary;
        hitlOverlay.classList.remove('hidden');
    }

    function hideHitl() {
        hitlContext = null;
        hitlOverlay.classList.add('hidden');
    }

    async function handleHitl(approved) {
        if (!hitlContext) return;
        hideHitl();
        setLock(true);
        showTyping();

        try {
            const res = await fetch('/api/chat', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ message: approved ? 'yes' : 'no' })
            });
            const data = await res.json();
            removeTyping();

            if (data.response) addMessage('assistant', data.response);
            renderRouteMeta(data);
            if (hasHitlPending(data)) {
                showHitl(data.pending_order_context);
            }
        } catch (err) {
            removeTyping();
            addMessage('assistant', '⚠️ 网络异常，请稍后重试。');
        } finally {
            setLock(false);
        }
    }

    approveBtn.addEventListener('click', () => handleHitl(true));
    rejectBtn.addEventListener('click', () => handleHitl(false));

    /* ---------- 发送消息 ---------- */
    async function sendMessage(text) {
        if (!text.trim() || isSending) return;
        if (hitlContext) {
            showHitl(hitlContext);
            return;
        }

        addMessage('user', text.trim());
        input.value = '';
        autoResize();
        setLock(true);
        showTyping();

        try {
            const res = await fetch('/api/chat', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ message: text.trim() })
            });
            const data = await res.json();
            removeTyping();

            if (data.response) addMessage('assistant', data.response);
            renderRouteMeta(data);
            if (hasHitlPending(data)) {
                showHitl(data.pending_order_context);
            }
        } catch (err) {
            removeTyping();
            addMessage('assistant', '⚠️ 网络异常，请稍后重试。');
        } finally {
            setLock(false);
            input.focus();
        }
    }

    /* ---------- 事件绑定 ---------- */
    sendBtn.addEventListener('click', () => sendMessage(input.value));

    input.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault();
            sendMessage(input.value);
        }
    });

    document.addEventListener('click', (e) => {
        const chip = e.target.closest('.quick-chip');
        if (chip && !chip.disabled) {
            const prompt = chip.getAttribute('data-prompt');
            if (prompt) sendMessage(prompt);
        }
    });

    resetBtn.addEventListener('click', async () => {
        try {
            await fetch('/api/reset', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' }
            });
        } catch (e) {
            // ignore reset transport failure and still clear UI
        }
        chatLog.innerHTML = '';
        routeMeta.innerHTML = '';
        hideHitl();
        if (welcomeState) welcomeState.classList.remove('hidden');
    });

    /* ---------- Bootstrap ---------- */
    async function bootstrap() {
        try {
            const res = await fetch('/api/bootstrap');
            const data = await res.json();

            if (Array.isArray(data.messages) && data.messages.length) {
                data.messages.forEach((msg) => addMessage(msg.role, msg.content));
            }
            renderRouteMeta(data);
            if (hasHitlPending(data)) {
                showHitl(data.pending_order_context);
            }
        } catch {
            // bootstrap failure should not block initial render
        }
    }

    /* ---------- 初始化 ---------- */
    initSidebar();
    bootstrap();
})();
