(function () {
    const API_BASE = (window.location.hostname === "127.0.0.1" || window.location.hostname === "localhost")
        ? "http://127.0.0.1:8000"
        : "";

    const style = document.createElement("style");
    style.textContent = `
        #chatbot-toggle {
            position: fixed; bottom: 20px; right: 20px;
            width: 56px; height: 56px; border-radius: 50%;
            background: #1a73e8; color: #fff; border: none;
            font-size: 24px; cursor: pointer; z-index: 9999;
            box-shadow: 0 2px 8px rgba(0,0,0,0.3);
        }
        #chatbot-window {
            position: fixed; bottom: 90px; right: 20px;
            width: 320px; max-height: 440px; background: #fff;
            border-radius: 10px; box-shadow: 0 4px 16px rgba(0,0,0,0.25);
            display: none; flex-direction: column; z-index: 9999;
            font-family: sans-serif; overflow: hidden;
        }
        #chatbot-window.open { display: flex; }
        #chatbot-header {
            background: #1a73e8; color: #fff; padding: 10px 14px; font-weight: bold;
        }
        #chatbot-messages {
            flex: 1; overflow-y: auto; padding: 10px; font-size: 14px;
        }
        .chatbot-msg { margin-bottom: 8px; white-space: pre-wrap; }
        .chatbot-msg.user { text-align: right; color: #1a73e8; }
        .chatbot-msg.bot { text-align: left; color: #333; }
        #chatbot-input-row {
            display: flex; border-top: 1px solid #eee;
        }
        #chatbot-input {
            flex: 1; border: none; padding: 10px; font-size: 14px; outline: none;
        }
        #chatbot-send {
            border: none; background: #1a73e8; color: #fff; padding: 0 14px; cursor: pointer;
        }
    `;
    document.head.appendChild(style);

    const toggle = document.createElement("button");
    toggle.id = "chatbot-toggle";
    toggle.textContent = "💬";
    document.body.appendChild(toggle);

    const win = document.createElement("div");
    win.id = "chatbot-window";
    win.innerHTML = `
        <div id="chatbot-header">Visa Assistant</div>
        <div id="chatbot-messages"></div>
        <div id="chatbot-input-row">
            <input id="chatbot-input" type="text" placeholder="Ask a question..." />
            <button id="chatbot-send">Send</button>
        </div>
    `;
    document.body.appendChild(win);

    const messages = win.querySelector("#chatbot-messages");
    const input = win.querySelector("#chatbot-input");
    const sendBtn = win.querySelector("#chatbot-send");

    function addMessage(text, sender) {
        const div = document.createElement("div");
        div.className = `chatbot-msg ${sender}`;
        div.textContent = text;
        messages.appendChild(div);
        messages.scrollTop = messages.scrollHeight;
    }

    async function sendMessage() {
        const text = input.value.trim();
        if (!text) return;
        addMessage(text, "user");
        input.value = "";

        const token = localStorage.getItem("token");
        const headers = { "Content-Type": "application/json" };
        if (token) headers["Authorization"] = `Bearer ${token}`;

        try {
            const resp = await fetch(`${API_BASE}/api/chatbot`, {
                method: "POST",
                headers,
                body: JSON.stringify({ message: text }),
            });
            const raw = await resp.text();
            let data;
            try {
                data = JSON.parse(raw);
            } catch {
                addMessage(`Sorry, something went wrong (status ${resp.status}).`, "bot");
                return;
            }
            addMessage(data.reply || "Sorry, I didn't understand that.", "bot");
        } catch (err) {
            addMessage("Could not reach the assistant. Please try again.", "bot");
        }
    }

    toggle.addEventListener("click", () => {
        win.classList.toggle("open");
        if (win.classList.contains("open") && messages.children.length === 0) {
            addMessage("Hi! Ask me about visa types, documents, fees, or your application status.", "bot");
        }
    });
    sendBtn.addEventListener("click", sendMessage);
    input.addEventListener("keydown", (e) => {
        if (e.key === "Enter") sendMessage();
    });
})();