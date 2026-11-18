document.addEventListener('DOMContentLoaded', () => {
    // --- Elements ---
    const chatBox = document.getElementById('chatBox');
    const chatForm = document.getElementById('chatForm');
    const messageInput = document.getElementById('messageInput');
    const uploadBtn = document.getElementById('uploadBtn');
    const fileInput = document.getElementById('fileInput');
    const sendBtn = document.getElementById('sendBtn');
    const themeToggle = document.getElementById('themeToggle');
    const clearChat = document.getElementById('clearChat');
    const emptyState = document.getElementById('emptyState');
    const dropZone = document.getElementById('dropZone');
    const filePreview = document.getElementById('filePreview');
    const fileNameSpan = document.getElementById('fileName');
    const removeFileBtn = document.getElementById('removeFile');
    const typingTemplate = document.getElementById('typingTemplate');

    let currentFile = null;
    let userId = localStorage.getItem('chatUserId');
    if (!userId) {
        userId = 'user_' + Math.random().toString(36).substr(2, 9);
        localStorage.setItem('chatUserId', userId);
    }

    let currentThreadId = 'default';

    // --- Thread Management ---
    async function loadThreads() {
        try {
            const res = await fetch(`/threads?userId=${userId}`);
            const data = await res.json();
            renderThreads(data.threads);
        } catch (e) { console.error('Failed to load threads', e); }
    }

    function renderThreads(threads) {
        const pinnedList = document.getElementById('pinnedThreadsList');
        const recentList = document.getElementById('recentThreadsList');
        const pinnedGroup = document.getElementById('pinnedThreadsGroup');
        
        pinnedList.innerHTML = '';
        recentList.innerHTML = '';
        
        let hasPinned = false;
        
        threads.forEach(t => {
            const li = document.createElement('li');
            li.className = `thread-item ${t.id === currentThreadId ? 'active' : ''}`;
            li.innerHTML = `
                <span class="thread-title" title="${t.title}">${t.title}</span>
                <div class="thread-actions">
                    <button class="pin-btn" title="${t.is_pinned ? 'إلغاء التثبيت' : 'تثبيت'}">
                        <svg viewBox="0 0 24 24" fill="${t.is_pinned ? 'currentColor' : 'none'}" stroke="currentColor" stroke-width="2"><path d="M21 10c0 7-9 13-9 13s-9-6-9-13a9 9 0 0 1 18 0z"></path><circle cx="12" cy="10" r="3"></circle></svg>
                    </button>
                    <button class="delete-btn" title="حذف">
                        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="3 6 5 6 21 6"></polyline><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"></path></svg>
                    </button>
                </div>
            `;
            
            li.querySelector('.thread-title').addEventListener('click', () => switchThread(t.id));
            li.querySelector('.pin-btn').addEventListener('click', (e) => { e.stopPropagation(); togglePinThread(t.id, !t.is_pinned); });
            li.querySelector('.delete-btn').addEventListener('click', (e) => { e.stopPropagation(); deleteThread(t.id); });
            
            if (t.is_pinned) {
                pinnedList.appendChild(li);
                hasPinned = true;
            } else {
                recentList.appendChild(li);
            }
        });
        
        pinnedGroup.style.display = hasPinned ? 'block' : 'none';
    }

    async function switchThread(threadId) {
        currentThreadId = threadId;
        chatBox.innerHTML = ''; // Clear chat
        loadThreads(); // Update active state
        try {
            const res = await fetch(`/threads/${threadId}/messages?userId=${userId}`);
            const data = await res.json();
            if (data.messages && data.messages.length > 0) {
                data.messages.forEach(m => appendMessage(m.sender, m.text));
            } else {
                chatBox.innerHTML = `
                <div id="emptyState" class="empty-state">
                    <div class="empty-icon"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"></path></svg></div>
                    <h2>أهلاً بك!</h2><p>أنا المساعد الذكي. كيف يمكنني مساعدتك اليوم؟</p>
                </div>`;
            }
        } catch (e) { console.error('Failed to load thread messages', e); }
    }

    async function createNewChat() {
        try {
            const response = await fetch('/threads', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify({ userId: userId, title: 'محادثة جديدة' })
            });
            const data = await response.json();
            switchThread(data.threadId);
        } catch (e) { console.error('Failed to create thread', e); }
    }

    async function togglePinThread(threadId, isPinned) {
        try {
            await fetch(`/threads/${threadId}`, {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ is_pinned: isPinned })
            });
            loadThreads();
        } catch (e) { console.error('Failed to pin thread', e); }
    }

    async function deleteThread(threadId) {
        if (!confirm('هل أنت متأكد من حذف هذه المحادثة؟')) return;
        try {
            await fetch(`/threads/${threadId}`, { method: 'DELETE' });
            if (currentThreadId === threadId) {
                createNewChat();
            } else {
                loadThreads();
            }
        } catch (e) { console.error('Failed to delete thread', e); }
    }

    const newChatBtn = document.getElementById('newChatBtn');
    if(newChatBtn) newChatBtn.addEventListener('click', createNewChat);

    // Initial load
    loadThreads();

    // --- Persona Logic ---
    const personaSelect = document.getElementById('personaSelect');
    const personaDescription = document.getElementById('personaDescription');
    const botTitle = document.querySelector('.header-text h1');
    const botAvatar = document.querySelector('.bot-avatar');

    const personas = {
        assistant: { title: 'المساعد الذكي', desc: 'مساعد رسمي ومهني', icon: '<path d="M12 8V4H8"></path><rect x="4" y="8" width="16" height="12" rx="2"></rect><path d="M2 14h2"></path><path d="M20 14h2"></path><path d="M15 13v2"></path><path d="M9 13v2"></path>' },
        friend: { title: 'صديقك الودود', desc: 'مستعد للدردشة والمرح', icon: '<circle cx="12" cy="12" r="10"></circle><path d="M8 14s1.5 2 4 2 4-2 4-2"></path><line x1="9" y1="9" x2="9.01" y2="9"></line><line x1="15" y1="9" x2="15.01" y2="9"></line>' },
        tech: { title: 'الخبير التقني', desc: 'مبرمج ومهندس نظم', icon: '<polyline points="16 18 22 12 16 6"></polyline><polyline points="8 6 2 12 8 18"></polyline>' },
        teacher: { title: 'المعلم الصبور', desc: 'مبسط للمعلومات', icon: '<path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20"></path><path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z"></path>' },
        writer: { title: 'الكاتب الإبداعي', desc: 'متمكن من اللغة وصياغتها', icon: '<path d="M12 19l7-7 3 3-7 7-3-3z"></path><path d="M18 13l-1.5-7.5L2 2l3.5 14.5L13 18l5-5z"></path><path d="M2 2l7.586 7.586"></path><circle cx="11" cy="11" r="2"></circle>' }
    };

    const savedPersona = localStorage.getItem('chatPersona') || 'assistant';
    personaSelect.value = savedPersona;
    updatePersonaUI(savedPersona);
    syncPersonaWithServer(savedPersona);

    personaSelect.addEventListener('change', (e) => {
        const p = e.target.value;
        localStorage.setItem('chatPersona', p);
        updatePersonaUI(p);
        syncPersonaWithServer(p);
    });

    function updatePersonaUI(p) {
        const data = personas[p];
        if(data) {
            botTitle.textContent = data.title;
            personaDescription.textContent = data.desc;
            botAvatar.innerHTML = data.icon;
        }
    }

    async function syncPersonaWithServer(p) {
        try {
            await fetch('/set_persona', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ userId: userId, persona: p })
            });
        } catch (err) {
            console.error('Failed to sync persona', err);
        }
    }

    // --- Slash Commands (Feature 13) ---
    const slashCommandsContainer = document.getElementById('slashCommands');
    const slashCommandsList = document.getElementById('slashCommandsList');
    
    const availableCommands = [
        { cmd: '/clear', desc: 'مسح المحادثة بالكامل', icon: '<polyline points="3 6 5 6 21 6"></polyline><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"></path>' },
        { cmd: '/summarize', desc: 'تلخيص المحادثة الحالية', icon: '<line x1="8" y1="6" x2="21" y2="6"></line><line x1="8" y1="12" x2="21" y2="12"></line><line x1="8" y1="18" x2="21" y2="18"></line><line x1="3" y1="6" x2="3.01" y2="6"></line><line x1="3" y1="12" x2="3.01" y2="12"></line><line x1="3" y1="18" x2="3.01" y2="18"></line>' },
        { cmd: '/export', desc: 'تصدير المحادثة (Markdown)', icon: '<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"></path><polyline points="7 10 12 15 17 10"></polyline><line x1="12" y1="15" x2="12" y2="3"></line>' }
    ];
    let selectedCommandIndex = 0;

    function renderSlashCommands(filter = '') {
        const filtered = availableCommands.filter(c => c.cmd.startsWith(filter));
        if (filtered.length === 0) {
            slashCommandsContainer.classList.add('hidden');
            return;
        }
        slashCommandsList.innerHTML = filtered.map((c, i) => `
            <li data-cmd="${c.cmd}" class="${i === selectedCommandIndex ? 'selected' : ''}">
                <svg class="cmd-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">${c.icon}</svg>
                <div class="cmd-info">
                    <div class="cmd-text">${c.cmd}</div>
                    <div class="cmd-desc">${c.desc}</div>
                </div>
            </li>
        `).join('');
        slashCommandsContainer.classList.remove('hidden');

        // Bind clicks
        slashCommandsList.querySelectorAll('li').forEach(li => {
            li.addEventListener('click', () => {
                executeSlashCommand(li.dataset.cmd);
            });
        });
    }

    async function executeSlashCommand(cmd) {
        slashCommandsContainer.classList.add('hidden');
        messageInput.value = '';
        messageInput.style.height = 'auto';

        if (cmd === '/clear') {
            clearChat.click();
        } 
        else if (cmd === '/export') {
            exportChatToMarkdown();
        } 
        else if (cmd === '/summarize') {
            const prompt = "يرجى تلخيص هذه المحادثة بالكامل في نقاط رئيسية مختصرة.";
            appendMessage('user', 'أمر النظام: /summarize');
            
            const msgDiv = appendBotStreamBubble();
            const contentEl = msgDiv.querySelector('.message-content');
            const cursor = msgDiv.querySelector('.stream-cursor');
            try {
                const fullText = await streamChat(prompt, contentEl, cursor);
                finalizeStreamBubble(msgDiv, fullText);
                showSuggestions(fullText);
            } catch (err) {
                cursor && cursor.remove();
                contentEl.textContent = 'فشل الاتصال بالخادم.';
                msgDiv.classList.add('error-msg');
            }
        }
    }

    function exportChatToMarkdown() {
        let md = `# محادثة الذكاء الاصطناعي\n\n`;
        document.querySelectorAll('#chatBox .message').forEach(msg => {
            const isUser = msg.classList.contains('user');
            const sender = isUser ? 'أنت' : 'المساعد الذكي';
            const text = msg.querySelector('.message-content').innerText;
            md += `### ${sender}\n${text}\n\n`;
        });

        const blob = new Blob([md], { type: 'text/markdown;charset=utf-8' });
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = `chat_export_${new Date().getTime()}.md`;
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        URL.revokeObjectURL(url);
    }

    // --- Auto Resize Textarea & Slash Commands trigger ---
    messageInput.addEventListener('input', function() {
        this.style.height = 'auto';
        this.style.height = (this.scrollHeight) + 'px';
        
        const val = this.value;
        if(val.trim() === '' && !currentFile) {
            sendBtn.disabled = true;
        } else {
            sendBtn.disabled = false;
        }

        // Check for slash command
        if (val.startsWith('/')) {
            const cmdText = val.split(' ')[0].toLowerCase();
            renderSlashCommands(cmdText);
        } else {
            slashCommandsContainer.classList.add('hidden');
        }
    });

    messageInput.addEventListener('keydown', function(e) {
        const isSlashVisible = !slashCommandsContainer.classList.contains('hidden');
        
        if (isSlashVisible) {
            const items = slashCommandsList.querySelectorAll('li');
            if (e.key === 'ArrowDown') {
                e.preventDefault();
                selectedCommandIndex = (selectedCommandIndex + 1) % items.length;
                renderSlashCommands(this.value.split(' ')[0].toLowerCase());
            } else if (e.key === 'ArrowUp') {
                e.preventDefault();
                selectedCommandIndex = (selectedCommandIndex - 1 + items.length) % items.length;
                renderSlashCommands(this.value.split(' ')[0].toLowerCase());
            } else if (e.key === 'Enter') {
                e.preventDefault();
                const selected = items[selectedCommandIndex];
                if (selected) executeSlashCommand(selected.dataset.cmd);
            }
            return;
        }

        if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault();
            chatForm.dispatchEvent(new Event('submit'));
        }
    });


    // --- Theme Engine (Feature 12) ---
    const swatches = document.querySelectorAll('.theme-swatch');
    
    // Load saved color theme
    const savedColorTheme = localStorage.getItem('colorTheme') || 'midnight';
    document.body.dataset.theme = savedColorTheme;
    swatches.forEach(sw => {
        if(sw.dataset.t === savedColorTheme) sw.classList.add('active');
        else sw.classList.remove('active');
    });

    swatches.forEach(swatch => {
        swatch.addEventListener('click', () => {
            const t = swatch.dataset.t;
            document.body.dataset.theme = t;
            localStorage.setItem('colorTheme', t);
            
            swatches.forEach(s => s.classList.remove('active'));
            swatch.classList.add('active');
        });
    });

    // --- Dark Mode Toggle ---
    themeToggle.addEventListener('click', () => {
        document.body.classList.toggle('dark-mode');
        const isDark = document.body.classList.contains('dark-mode');
        localStorage.setItem('theme', isDark ? 'dark' : 'light');
    });

    // Load saved dark mode
    if (localStorage.getItem('theme') === 'light') {
        document.body.classList.remove('dark-mode');
    } else if (!localStorage.getItem('theme')) {
        // default to dark
        document.body.classList.add('dark-mode');
    }

    // --- File Handling & Drag/Drop ---
    uploadBtn.addEventListener('click', () => fileInput.click());

    fileInput.addEventListener('change', (e) => {
        if (e.target.files.length > 0) {
            setFile(e.target.files[0]);
        }
    });

    removeFileBtn.addEventListener('click', () => {
        setFile(null);
    });

    dropZone.addEventListener('dragover', (e) => {
        e.preventDefault();
        dropZone.classList.add('drag-over');
    });

    dropZone.addEventListener('dragleave', () => {
        dropZone.classList.remove('drag-over');
    });

    dropZone.addEventListener('drop', (e) => {
        e.preventDefault();
        dropZone.classList.remove('drag-over');
        if (e.dataTransfer.files.length > 0) {
            setFile(e.dataTransfer.files[0]);
        }
    });

    function setFile(file) {
        currentFile = file;
        if (file) {
            fileNameSpan.textContent = file.name;
            filePreview.classList.remove('hidden');
            sendBtn.disabled = false;
        } else {
            fileInput.value = '';
            filePreview.classList.add('hidden');
            if (messageInput.value.trim() === '') {
                sendBtn.disabled = true;
            }
        }
    }

    // --- Chat Functions ---
    function scrollToBottom() {
        chatBox.scrollTop = chatBox.scrollHeight;
    }

    function removeEmptyState() {
        if (emptyState && emptyState.parentNode) {
            emptyState.remove();
        }
    }

    function appendMessage(role, text, isError = false, isBranch = false) {
        removeEmptyState();
        const msgDiv = document.createElement('div');
        const messageId = `msg_${Date.now()}_${Math.random().toString(36).substr(2,5)}`;
        msgDiv.dataset.messageId = messageId;
        msgDiv.className = `message ${role} ${isError ? 'error-msg' : ''}`;
        
        const avatarSvg = role === 'user' 
            ? '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"></path><circle cx="12" cy="7" r="4"></circle></svg>'
            : '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 8V4H8"></path><rect x="4" y="8" width="16" height="12" rx="2"></rect></svg>';

        // Branch badge for edited messages
        const branchHtml = isBranch
            ? `<div class="branch-badge">✏️ تعديل من نقطة سابقة</div>`
            : '';

        msgDiv.innerHTML = `
            ${branchHtml}
            <div class="avatar">${avatarSvg}</div>
            <div class="bubble glass-bubble">
                <div class="message-content"></div>
            </div>
        `;
        
        msgDiv.querySelector('.message-content').textContent = text;
        
        if (role === 'bot' && !isError) {
            const btnDiv = document.createElement('div');
            btnDiv.innerHTML = `
                <button class="listen-btn" title="استمع للرد">
                    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5"></polygon><path d="M19.07 4.93a10 10 0 0 1 0 14.14M15.54 8.46a5 5 0 0 1 0 7.07"></path></svg>
                    استمع
                </button>
            `;
            const listenBtn = btnDiv.querySelector('.listen-btn');
            listenBtn.addEventListener('click', () => speakText(text, listenBtn));
            msgDiv.querySelector('.bubble').appendChild(btnDiv);
        }

        // Edit button for user messages
        if (role === 'user' && !isError) {
            const editBtn = document.createElement('button');
            editBtn.className = 'edit-msg-btn';
            editBtn.title = 'تعديل الرسالة';
            editBtn.innerHTML = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"></path><path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"></path></svg> تعديل`;
            editBtn.addEventListener('click', () => startEditMessage(msgDiv, text));
            msgDiv.appendChild(editBtn);
        }
        // Smart Message Actions toolbar
        const toolbar = document.createElement('div');
        toolbar.className = 'msg-toolbar';
        toolbar.innerHTML = `
            <button class="msg-action-btn copy-btn" title="نسخ"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M9 9V5a2 2 0 0 1 2-2h7a2 2 0 0 1 2 2v7a2 2 0 0 1-2 2h-4"></path><rect x="5" y="9" width="7" height="7" rx="2"></rect></svg></button>
            <button class="msg-action-btn regen-btn" title="إعادة توليد"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 12a9 9 0 1 1-9-9"></path><polyline points="12 3 12 12 20 12"></polyline></svg></button>
            <button class="msg-action-btn rate-up" title="👍"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M14 9V5a3 3 0 0 0-3-3l-4 9v11h11.28a2 2 0 0 0 2-1.7l1.38-9a2 2 0 0 0-2-2.3H14z"/><path d="M7 22H4a2 2 0 0 1-2-2v-7a2 2 0 0 1 2-2h3"/></svg></button>
            <button class="msg-action-btn rate-down" title="👎"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M10 15v4a3 3 0 0 0 3 3l4-9V2H5.72a2 2 0 0 0-2 1.7l-1.38 9a2 2 0 0 0 2 2.3H10z"/><path d="M17 2h2.67A2.31 2.31 0 0 1 22 4v7a2.31 2.31 0 0 1-2.33 2H17"/></svg></button>
            <div class="translate-picker">
                <button class="msg-action-btn translate-btn" title="ترجمة"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M3 12h18M3 6h18M3 18h12"/></svg></button>
                <select>
                    <option value="Arabic">العربية</option>
                    <option value="English">الإنجليزية</option>
                    <option value="French">الفرنسية</option>
                    <option value="Spanish">الإسبانية</option>
                    <option value="German">الألمانية</option>
                    <option value="Chinese">الصينية</option>
                    <option value="Russian">الروسية</option>
                </select>
            </div>
        `;
        msgDiv.querySelector('.bubble').appendChild(toolbar);

        // Copy handler
        toolbar.querySelector('.copy-btn').addEventListener('click', () => {
            navigator.clipboard.writeText(text).then(() => {
                const btn = toolbar.querySelector('.copy-btn');
                btn.classList.add('copied');
                setTimeout(() => btn.classList.remove('copied'), 1500);
            });
        });

        // Regenerate handler
        toolbar.querySelector('.regen-btn').addEventListener('click', async () => {
            const userMessages = chatBox.querySelectorAll('.message.user .message-content');
            const lastUserMsg = userMessages.length ? userMessages[userMessages.length - 1].textContent : '';
            if (!lastUserMsg) return;
            msgDiv.remove();
            messageInput.value = lastUserMsg;
            sendBtn.disabled = true;
            chatForm.dispatchEvent(new Event('submit'));
        });

        // Rating handlers
        toolbar.querySelector('.rate-up').addEventListener('click', async () => {
            await fetch('/rate', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ userId, messageId, rating: 1 })
            });
            toolbar.querySelector('.rate-up').classList.add('rated-up');
            toolbar.querySelector('.rate-down').disabled = true;
        });
        toolbar.querySelector('.rate-down').addEventListener('click', async () => {
            await fetch('/rate', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ userId, messageId, rating: -1 })
            });
            toolbar.querySelector('.rate-down').classList.add('rated-down');
            toolbar.querySelector('.rate-up').disabled = true;
        });

        // Translate handler
        const translatePicker = toolbar.querySelector('.translate-picker');
        const translateBtn = translatePicker.querySelector('.translate-btn');
        const langSelect = translatePicker.querySelector('select');
        translateBtn.addEventListener('click', () => {
            translatePicker.classList.toggle('open');
        });
        langSelect.addEventListener('change', async (e) => {
            const targetLang = e.target.value;
            const res = await fetch('/translate', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ text, targetLang, userId })
            });
            const data = await res.json();
            if (res.ok) {
                const existing = msgDiv.querySelector('.translation-block');
                if (existing) existing.remove();
                const transDiv = document.createElement('div');
                transDiv.className = 'translation-block';
                const labelSpan = document.createElement('span');
                labelSpan.className = 'translation-label';
                labelSpan.textContent = `${targetLang}: `;
                transDiv.appendChild(labelSpan);
                transDiv.appendChild(document.createTextNode(data.translation || ''));
                msgDiv.querySelector('.bubble').appendChild(transDiv);
            }
            translatePicker.classList.remove('open');
        });
        
        chatBox.appendChild(msgDiv);
        scrollToBottom();
        return msgDiv;
    }

    // --- Smart Suggestions ---
    async function showSuggestions(botReply) {
        // Create container with 3 shimmer placeholders
        const container = document.createElement('div');
        container.className = 'suggestions-container';
        for (let i = 0; i < 3; i++) {
            const shimmer = document.createElement('span');
            shimmer.className = 'suggestion-chip loading';
            container.appendChild(shimmer);
        }
        chatBox.appendChild(container);
        scrollToBottom();

        try {
            const res = await fetch('/suggestions', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ reply: botReply, userId })
            });
            const data = await res.json();
            container.innerHTML = '';
            if (data.suggestions && data.suggestions.length > 0) {
                data.suggestions.forEach(q => {
                    const chip = document.createElement('button');
                    chip.className = 'suggestion-chip';
                    chip.innerHTML = `<span class="chip-icon">💡</span>${q}`;
                    chip.addEventListener('click', () => {
                        container.remove();
                        messageInput.value = q;
                        messageInput.dispatchEvent(new Event('input'));
                        chatForm.dispatchEvent(new Event('submit'));
                    });
                    container.appendChild(chip);
                });
            } else {
                container.remove();
            }
        } catch (e) {
            container.remove();
        }
        scrollToBottom();
    }

    // --- Edit & Branch (Feature 9) ---
    function startEditMessage(msgDiv, originalText) {
        const bubble = msgDiv.querySelector('.bubble');
        const originalContent = bubble.innerHTML;

        // Replace bubble content with inline edit form
        bubble.innerHTML = `
            <form class="edit-inline-form">
                <textarea class="edit-inline-textarea" rows="3">${originalText}</textarea>
                <div class="edit-inline-actions">
                    <button type="submit" class="edit-confirm-btn">✔ إرسال</button>
                    <button type="button" class="edit-cancel-btn">✖ إلغاء</button>
                </div>
            </form>
        `;

        const textarea = bubble.querySelector('.edit-inline-textarea');
        textarea.focus();
        textarea.setSelectionRange(textarea.value.length, textarea.value.length);

        // Cancel
        bubble.querySelector('.edit-cancel-btn').addEventListener('click', () => {
            bubble.innerHTML = originalContent;
        });

        // Confirm submit
        bubble.querySelector('.edit-inline-form').addEventListener('submit', (e) => {
            e.preventDefault();
            const newText = textarea.value.trim();
            if (!newText) return;

            // Remove this message and everything after it in the DOM
            const allMessages = [...chatBox.children];
            const thisIdx = allMessages.indexOf(msgDiv);
            // Remove from thisIdx onwards
            allMessages.slice(thisIdx).forEach(el => el.remove());

            // Re-submit as branch
            messageInput.value = newText;
            sendBtn.disabled = false;
            // Append branch user message
            appendMessage('user', newText, false, true);
            messageInput.value = '';

            // Trigger the actual API call
            branchSubmit(newText);
        });
    }

    async function branchSubmit(text) {
        const msgDiv = appendBotStreamBubble();
        const contentEl = msgDiv.querySelector('.message-content');
        const cursor = msgDiv.querySelector('.stream-cursor');
        try {
            const fullText = await streamChat(text, contentEl, cursor);
            finalizeStreamBubble(msgDiv, fullText);
            showSuggestions(fullText);
        } catch (err) {
            cursor && cursor.remove();
            contentEl.textContent = 'فشل الاتصال بالخادم.';
            msgDiv.classList.add('error-msg');
        }
    }

    // --- Auto Data Visualization (Feature 10) ---
    const _chartInstances = {};
    function renderChartCard(chartData) {
        const card = document.createElement('div');
        card.className = 'chart-card';
        const chartId = `chart_${Date.now()}`;
        card.innerHTML = `
            <div class="chart-card-header">
                <span>📊</span>
                <span>${chartData.title || 'بيانات الملف'}</span>
                <span class="chart-badge">تصور تلقائي</span>
            </div>
            <div class="chart-canvas-wrapper">
                <canvas id="${chartId}"></canvas>
            </div>
        `;
        chatBox.appendChild(card);
        scrollToBottom();

        // Render Chart.js
        const ctx = document.getElementById(chartId).getContext('2d');
        Chart.defaults.color = getComputedStyle(document.body)
            .getPropertyValue('--text-muted').trim() || '#9ca3af';
        Chart.defaults.font.family = 'Tajawal';

        _chartInstances[chartId] = new Chart(ctx, {
            type: chartData.type || 'bar',
            data: {
                labels: chartData.labels,
                datasets: chartData.datasets,
            },
            options: {
                responsive: true,
                maintainAspectRatio: true,
                animation: { duration: 800, easing: 'easeOutQuart' },
                plugins: {
                    legend: { position: 'bottom', labels: { padding: 16, boxWidth: 12 } },
                    tooltip: { mode: 'index', intersect: false },
                },
                scales: {
                    x: { grid: { color: 'rgba(255,255,255,0.05)' } },
                    y: { grid: { color: 'rgba(255,255,255,0.05)' }, beginAtZero: true },
                },
            }
        });
    }

    function showTyping() {
        removeEmptyState();
        const clone = typingTemplate.content.cloneNode(true);
        chatBox.appendChild(clone);
        scrollToBottom();
    }

    function hideTyping() {
        const typingMsg = document.querySelector('.typing-message');
        if (typingMsg) typingMsg.remove();
    }

    // --- SSE Streaming helpers ---
    function appendBotStreamBubble() {
        removeEmptyState();
        const msgDiv = document.createElement('div');
        msgDiv.className = 'message bot';
        msgDiv.innerHTML = `
            <div class="avatar"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 8V4H8"></path><rect x="4" y="8" width="16" height="12" rx="2"></rect></svg></div>
            <div class="bubble glass-bubble">
                <div class="message-content"></div>
                <span class="stream-cursor"></span>
            </div>
        `;
        chatBox.appendChild(msgDiv);
        scrollToBottom();
        return msgDiv;
    }

    function finalizeStreamBubble(msgDiv, fullText) {
        const cursor = msgDiv.querySelector('.stream-cursor');
        if (cursor) cursor.remove();

        // Add the listen + smart actions toolbar (same as appendMessage does for bot)
        const bubble = msgDiv.querySelector('.bubble');
        const btnDiv = document.createElement('div');
        btnDiv.innerHTML = `
            <button class="listen-btn" title="استمع للرد">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5"></polygon><path d="M19.07 4.93a10 10 0 0 1 0 14.14M15.54 8.46a5 5 0 0 1 0 7.07"></path></svg>
                استمع
            </button>`;
        btnDiv.querySelector('.listen-btn').addEventListener('click', function() { speakText(fullText, this); });
        bubble.appendChild(btnDiv);

        const messageId = `msg_${Date.now()}_${Math.random().toString(36).substr(2,5)}`;
        msgDiv.dataset.messageId = messageId;
        const toolbar = document.createElement('div');
        toolbar.className = 'msg-toolbar';
        toolbar.innerHTML = `
            <button class="msg-action-btn copy-btn" title="نسخ"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M9 9V5a2 2 0 0 1 2-2h7a2 2 0 0 1 2 2v7a2 2 0 0 1-2 2h-4"></path><rect x="5" y="9" width="7" height="7" rx="2"></rect></svg></button>
            <button class="msg-action-btn rate-up" title="👍"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M14 9V5a3 3 0 0 0-3-3l-4 9v11h11.28a2 2 0 0 0 2-1.7l1.38-9a2 2 0 0 0-2-2.3H14"/><path d="M7 22H4a2 2 0 0 1-2-2v-7a2 2 0 0 1 2-2h3"/></svg></button>
            <button class="msg-action-btn rate-down" title="👎"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M10 15v4a3 3 0 0 0 3 3l4-9V2H5.72a2 2 0 0 0-2 1.7l-1.38 9a2 2 0 0 0 2 2.3H10"/><path d="M17 2h2.67A2.31 2.31 0 0 1 22 4v7a2.31 2.31 0 0 1-2.33 2H17"/></svg></button>
        `;
        toolbar.querySelector('.copy-btn').addEventListener('click', () => {
            navigator.clipboard.writeText(fullText).then(() => {
                toolbar.querySelector('.copy-btn').classList.add('copied');
                setTimeout(() => toolbar.querySelector('.copy-btn').classList.remove('copied'), 1500);
            });
        });
        const userId_ = userId;
        toolbar.querySelector('.rate-up').addEventListener('click', async () => {
            await fetch('/rate', { method: 'POST', headers: {'Content-Type':'application/json'},
                body: JSON.stringify({ userId: userId_, messageId, rating: 1 }) });
            toolbar.querySelector('.rate-up').classList.add('rated-up');
            toolbar.querySelector('.rate-down').disabled = true;
        });
        toolbar.querySelector('.rate-down').addEventListener('click', async () => {
            await fetch('/rate', { method: 'POST', headers: {'Content-Type':'application/json'},
                body: JSON.stringify({ userId: userId_, messageId, rating: -1 }) });
            toolbar.querySelector('.rate-down').classList.add('rated-down');
            toolbar.querySelector('.rate-up').disabled = true;
        });
        bubble.appendChild(toolbar);
    }

    async function streamChat(text, contentEl, cursor) {
        const res = await fetch('/chat/stream', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ message: text, userId, threadId: currentThreadId })
        });
        if (!res.ok) throw new Error('SSE request failed');

        const reader = res.body.getReader();
        const decoder = new TextDecoder();
        let buffer = '';
        let fullText = '';

        while (true) {
            const { done, value } = await reader.read();
            if (done) break;
            buffer += decoder.decode(value, { stream: true });
            const lines = buffer.split('\n');
            buffer = lines.pop(); // keep incomplete line
            for (const line of lines) {
                if (!line.startsWith('data: ')) continue;
                try {
                    const payload = JSON.parse(line.slice(6));
                    if (payload.error) throw new Error(payload.error);
                    if (payload.token) {
                        fullText += payload.token;
                        contentEl.textContent = fullText;
                        scrollToBottom();
                    }
                    if (payload.done) return payload.full || fullText;
                } catch (parseErr) {
                    if (parseErr.message !== 'Unexpected end of JSON input') throw parseErr;
                }
            }
        }
        return fullText;
    }

    // --- Submission ---
    chatForm.addEventListener('submit', async (e) => {
        e.preventDefault();
        
        const text = messageInput.value.trim();
        const file = currentFile;
        
        if (!text && !file) return;

        // Reset input
        messageInput.value = '';
        messageInput.style.height = 'auto';
        setFile(null);
        sendBtn.disabled = true;

        if (file) {
            appendMessage('user', `📎 المرفق: ${file.name}\n${text}`);
        } else {
            appendMessage('user', text);
        }

        if (file) {
            // File upload — still uses normal JSON response
            showTyping();
            try {
                const formData = new FormData();
                formData.append('file', file);
                formData.append('userId', userId);
                formData.append('threadId', currentThreadId);
                const res = await fetch('/upload', { method: 'POST', body: formData });
                const data = await res.json();
                hideTyping();
                if (res.ok) {
                    appendMessage('bot', data.reply);
                    if (data.chart_data) renderChartCard(data.chart_data);
                    showSuggestions(data.reply);
                } else {
                    appendMessage('bot', data.reply || 'حدث خطأ غير معروف.', true);
                }
            } catch (err) {
                hideTyping();
                appendMessage('bot', 'فشل الاتصال بالخادم.', true);
            }
        } else {
            // Text chat — SSE streaming
            const msgDiv = appendBotStreamBubble();
            const contentEl = msgDiv.querySelector('.message-content');
            const cursor = msgDiv.querySelector('.stream-cursor');
            try {
                const fullText = await streamChat(text, contentEl, cursor);
                finalizeStreamBubble(msgDiv, fullText);
                showSuggestions(fullText);
            } catch (err) {
                cursor && cursor.remove();
                contentEl.textContent = 'فشل الاتصال بالخادم. يرجى التحقق من اتصالك.';
                msgDiv.classList.add('error-msg');
            }
        }
    });

    // --- Export Chat (Feature 14) ---
    const exportChatBtn = document.getElementById('exportChatBtn');
    if (exportChatBtn) {
        exportChatBtn.addEventListener('click', () => {
            exportChatToMarkdown();
        });
    }

    // --- Clear Chat ---
    clearChat.addEventListener('click', () => {
        if(confirm('هل أنت متأكد من مسح المحادثة؟')) {
            userId = 'user_' + Math.random().toString(36).substr(2, 9);
            localStorage.setItem('chatUserId', userId);
            chatBox.innerHTML = '';
            chatBox.appendChild(emptyState);
        }
    });

    sendBtn.disabled = true;

    // --- Compare Mode ---
    const compareBtn = document.getElementById('compareBtn');
    const compareBar = document.getElementById('compareBar');
    let compareMode = false;

    const PROVIDER_ICONS = {
        gemini: '🟢', openai: '⚪', claude: '🟠', ollama: '🟣'
    };

    function appendCompareResult(question, p1Name, p1Reply, p2Name, p2Reply) {
        removeEmptyState();

        // User message
        const userDiv = document.createElement('div');
        userDiv.className = 'message user';
        userDiv.innerHTML = `
            <div class="avatar"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"></path><circle cx="12" cy="7" r="4"></circle></svg></div>
            <div class="bubble glass-bubble"><div class="message-content"></div></div>
        `;
        userDiv.querySelector('.message-content').textContent = question;
        chatBox.appendChild(userDiv);

        // Compare panels
        const compareDiv = document.createElement('div');
        compareDiv.className = 'compare-result';

        [{ name: p1Name, reply: p1Reply }, { name: p2Name, reply: p2Reply }].forEach(({ name, reply }) => {
            const panel = document.createElement('div');
            panel.className = 'compare-panel';
            panel.innerHTML = `
                <div class="compare-panel-header ${name}">
                    <span>${PROVIDER_ICONS[name] || '🤖'}</span>
                    <span>${name.charAt(0).toUpperCase() + name.slice(1)}</span>
                </div>
                <div class="compare-panel-body"></div>
            `;
            panel.querySelector('.compare-panel-body').textContent = reply;
            compareDiv.appendChild(panel);
        });

        chatBox.appendChild(compareDiv);
        scrollToBottom();
    }

    if (compareBtn) {
        compareBtn.addEventListener('click', () => {
            compareMode = !compareMode;
            compareBtn.classList.toggle('active', compareMode);
            compareBar.classList.toggle('hidden', !compareMode);
        });
    }

    // Intercept form submit for Compare Mode
    const originalFormSubmit = chatForm.onsubmit;
    chatForm.addEventListener('submit', async (e) => {
        if (!compareMode) return; // let normal handler proceed

        e.preventDefault();
        e.stopImmediatePropagation();

        const text = messageInput.value.trim();
        if (!text) return;

        const p1 = document.getElementById('compareP1').value;
        const p2 = document.getElementById('compareP2').value;

        if (p1 === p2) {
            alert('يرجى اختيار مزودين مختلفين للمقارنة.');
            return;
        }

        messageInput.value = '';
        messageInput.style.height = 'auto';
        sendBtn.disabled = true;

        // Show loading panels
        removeEmptyState();
        const loadingDiv = document.createElement('div');
        loadingDiv.className = 'compare-result';
        loadingDiv.id = 'compareLoading';
        loadingDiv.innerHTML = `
            <div class="compare-panel">
                <div class="compare-panel-header ${p1}">${PROVIDER_ICONS[p1]||'🤖'} ${p1}</div>
                <div class="typing-indicator"><span></span><span></span><span></span></div>
            </div>
            <div class="compare-panel">
                <div class="compare-panel-header ${p2}">${PROVIDER_ICONS[p2]||'🤖'} ${p2}</div>
                <div class="typing-indicator"><span></span><span></span><span></span></div>
            </div>
        `;
        chatBox.appendChild(loadingDiv);
        scrollToBottom();

        try {
            const res = await fetch('/compare', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    message: text,
                    provider1: p1,
                    provider2: p2,
                    userId: userId,
                    threadId: currentThreadId,
                    persona: (personaSelect ? personaSelect.value : 'assistant')
                })
            });
            const data = await res.json();
            const el = document.getElementById('compareLoading');
            if (el) el.remove();

            if (res.ok) {
                appendCompareResult(
                    text,
                    data.provider1.name, data.provider1.reply,
                    data.provider2.name, data.provider2.reply
                );
            } else {
                appendMessage('bot', data.error || 'فشلت المقارنة.', true);
            }
        } catch (err) {
            const el = document.getElementById('compareLoading');
            if (el) el.remove();
            appendMessage('bot', 'فشل الاتصال بالخادم أثناء المقارنة.', true);
        }
    }, true); // capture=true to intercept before the normal submit

    // --- Voice Mode: Speech-to-Text ---
    const micBtn = document.getElementById('micBtn');
    let recognition;
    if ('webkitSpeechRecognition' in window || 'SpeechRecognition' in window) {
        const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
        recognition = new SpeechRecognition();
        recognition.lang = 'ar-SA';
        recognition.continuous = false;
        recognition.interimResults = false;

        recognition.onstart = function() {
            micBtn.classList.add('recording');
        };

        recognition.onresult = function(event) {
            const transcript = event.results[0][0].transcript;
            messageInput.value = (messageInput.value + ' ' + transcript).trim();
            messageInput.dispatchEvent(new Event('input'));
        };

        recognition.onerror = function(event) {
            console.error('Speech recognition error', event.error);
            micBtn.classList.remove('recording');
        };

        recognition.onend = function() {
            micBtn.classList.remove('recording');
        };

        if(micBtn) {
            micBtn.addEventListener('click', () => {
                if (micBtn.classList.contains('recording')) {
                    recognition.stop();
                } else {
                    recognition.start();
                }
            });
        }
    } else {
        if(micBtn) micBtn.style.display = 'none';
    }

    // --- Voice Mode: Text-to-Speech ---
    let currentUtterance = null;
    window.speakText = function(text, btnElement) {
        if (!('speechSynthesis' in window)) return;

        if (btnElement.classList.contains('playing')) {
            window.speechSynthesis.cancel();
            btnElement.classList.remove('playing');
            return;
        }

        window.speechSynthesis.cancel();
        document.querySelectorAll('.listen-btn').forEach(btn => btn.classList.remove('playing'));

        currentUtterance = new SpeechSynthesisUtterance(text);
        currentUtterance.lang = 'ar-SA';
        
        currentUtterance.onstart = () => btnElement.classList.add('playing');
        currentUtterance.onend = () => btnElement.classList.remove('playing');
        currentUtterance.onerror = () => btnElement.classList.remove('playing');

        window.speechSynthesis.speak(currentUtterance);
    };

});
