// ========================================================
// AgentTool Mobile Client - Interactive Logic
// ========================================================

const API_BASE = window.location.origin;
const WS_PROTOCOL = window.location.protocol === "https:" ? "wss:" : "ws:";

// Persist Device/Session ID across browser refreshes so conversation history is never lost
let storedDeviceId = localStorage.getItem("agenttool_device_id");
if (!storedDeviceId) {
  storedDeviceId = "mobile_" + Math.random().toString(36).substring(2, 9);
  localStorage.setItem("agenttool_device_id", storedDeviceId);
}
const DEVICE_ID = storedDeviceId;

let isRecording = false;
let mediaRecorder = null;
let audioChunks = [];
let speechRecognition = null;
let currentSpeechLang = localStorage.getItem("agenttool_speech_lang") || "en-IN";
let currentCallTimer = null;
let callSeconds = 0;
let voiceOutputEnabled = true;
let continuousVoice = false;
let activeAudio = null;
let agentState = "idle";
let socket = null;

// Initialize on DOM Ready
document.addEventListener("DOMContentLoaded", () => {
  setupTabs();
  setupChatInputs();
  setupSpeechRecognition();
  setupWebSocket();
  setupFloatingAgentDrag();
  checkSystemStatus();
  loadContacts();
  loadCallLogs();
});

// ========================================================
// Tab Navigation
// ========================================================
function setupTabs() {
  const tabs = document.querySelectorAll(".tab-btn");
  tabs.forEach(tab => {
    tab.addEventListener("click", () => {
      tabs.forEach(t => t.classList.remove("active"));
      tab.classList.add("active");

      const targetId = tab.getAttribute("data-tab");
      document.querySelectorAll(".tab-content").forEach(c => c.classList.remove("active"));
      const targetContent = document.getElementById(targetId);
      if (targetContent) {
        targetContent.classList.add("active");
      }

      if (targetId === "tab-contacts") {
        loadContacts();
        loadCallLogs();
      } else if (targetId === "tab-settings") {
        checkSystemStatus();
      }
    });
  });

  const soundBtn = document.getElementById("sound-toggle-btn");
  soundBtn.addEventListener("click", () => {
    voiceOutputEnabled = !voiceOutputEnabled;
    soundBtn.textContent = voiceOutputEnabled ? "🔊" : "🔇";
    soundBtn.title = voiceOutputEnabled ? "Voice Enabled" : "Voice Muted";
  });
}

// ========================================================
// WebSocket Connection (Device Bridge)
// ========================================================
function setupWebSocket() {
  const wsUrl = `${WS_PROTOCOL}//${window.location.host}/ws/device/${DEVICE_ID}`;
  try {
    socket = new WebSocket(wsUrl);

    socket.onopen = () => {
      console.log("[WebSocket] Connected to AgentTool Device Bridge as", DEVICE_ID);
      document.getElementById("system-status-dot").style.backgroundColor = "#10b981";
    };

    socket.onmessage = (event) => {
      try {
        const payload = JSON.parse(event.data);
        console.log("[WebSocket] Received event:", payload);
        handleDeviceEvent(payload);
      } catch (e) {
        console.error("Error parsing WS message:", e);
      }
    };

    socket.onclose = () => {
      console.warn("[WebSocket] Disconnected. Reconnecting in 3s...");
      document.getElementById("system-status-dot").style.backgroundColor = "#f59e0b";
      setTimeout(setupWebSocket, 3000);
    };

    socket.onerror = (err) => {
      console.error("[WebSocket] Error:", err);
    };
  } catch (e) {
    console.error("Could not initialize WebSocket:", e);
  }
}

function handleDeviceEvent(event) {
  if (event.type === "TRIGGER_PHONE_CALL") {
    const data = event.data;
    triggerMobilePhoneCall(data.phone_number, data.contact_name);
  } else if (event.type === "TRIGGER_WHATSAPP" || event.type === "TRIGGER_SMS" || event.type === "TRIGGER_EMAIL") {
    triggerMobileMessage({
      type: event.type === "TRIGGER_WHATSAPP" ? "WHATSAPP" : (event.type === "TRIGGER_EMAIL" ? "EMAIL" : "SMS"),
      ...event.data
    });
  } else if (event.type === "CALL_ENDED") {
    console.log("[Call Ended] Phone call was ended/cut on mobile device. Refocusing app...");
    endCurrentCall();
    setAgentState("idle", "Ready", "⚡");
    appendMessage("assistant", "📴 Phone call ended. I'm ready for your next command.");
    checkContinuousListenResume();
  } else if (event.type === "SCREEN_TAP") {
    simulateTap(event.data.target);
  } else if (event.type === "OPEN_APP") {
    simulateTap(event.data.app_name);
  }
}

// ========================================================
// Chat & Agent Communication
// ========================================================
function setupChatInputs() {
  const input = document.getElementById("chat-input");
  const sendBtn = document.getElementById("send-btn");

  // Barge-in: immediately stop assistant speech when user starts typing
  input.addEventListener("input", () => {
    stopAssistantSpeech();
  });

  sendBtn.addEventListener("click", () => {
    sendMessage(input.value);
  });

  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter") {
      sendMessage(input.value);
    }
  });

  const micBtn = document.getElementById("mic-btn");
  micBtn.addEventListener("click", () => {
    toggleVoiceRecording();
  });

  // Native Audio File/Microphone Capture Fallback
  const audioFileInput = document.getElementById("audio-file-input");
  if (audioFileInput) {
    audioFileInput.addEventListener("change", async (e) => {
      stopAssistantSpeech();
      const file = e.target.files[0];
      if (!file) return;
      const formData = new FormData();
      formData.append("file", file, file.name || "voice_capture.wav");

      const statusId = appendMessage("assistant", "🎙️ Transcribing voice via ElevenLabs...");
      setAgentState("thinking", "Transcribing...", "🎙️");
      try {
        const res = await fetch(`${API_BASE}/api/voice/stt`, {
          method: "POST",
          body: formData
        });
        const data = await res.json();
        removeMessage(statusId);

        if (data.text && data.text.trim()) {
          document.getElementById("chat-input").value = data.text;
          sendMessage(data.text);
        } else {
          setAgentState("idle", "Ready", "⚡");
          appendMessage("assistant", "Could not transcribe audio. Please try speaking clearly or typing.");
        }
      } catch (err) {
        removeMessage(statusId);
        setAgentState("idle", "Ready", "⚡");
        appendMessage("assistant", "Audio upload failed: " + err.message);
      }
    });
  }
}

// Mobile Phone Contacts Sync API
async function importPhoneContacts() {
  if ("contacts" in navigator && "ContactsManager" in window) {
    try {
      const props = ["name", "tel"];
      const contacts = await navigator.contacts.select(props, { multiple: true });
      if (!contacts || contacts.length === 0) return;
      let count = 0;
      for (const c of contacts) {
        const cName = (c.name && c.name.length > 0) ? c.name[0] : "";
        const cTel = (c.tel && c.tel.length > 0) ? c.tel[0] : "";
        if (cName && cTel) {
          await fetch(`${API_BASE}/api/contacts`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ name: cName, phone: cTel, notes: "Synced from mobile phone" })
          });
          count++;
        }
      }
      loadContacts();
      appendMessage("assistant", `✅ Synced ${count} contact(s) from your phone into MongoDB! You can now ask me to call them directly.`);
    } catch (err) {
      console.warn("Contact picker cancelled or not granted:", err);
    }
  } else {
    alert("Phone Contact Picker is supported in Chrome on Android. You can also quickly add contacts under the 'Contacts & Calls' tab.");
  }
}

function sendQuickPrompt(promptText) {
  stopAssistantSpeech();
  document.getElementById("chat-input").value = promptText;
  sendMessage(promptText);
}

async function sendMessage(text) {
  if (!text || !text.trim()) return;
  const messageText = text.trim();
  document.getElementById("chat-input").value = "";

  // Cut off ongoing speech immediately
  stopAssistantSpeech();

  // Append user message
  appendMessage("user", messageText);

  // Show thinking indicator
  setAgentState("thinking", "Thinking...", "🧠");
  const thinkingId = appendMessage("assistant", "Thinking...");

  try {
    const res = await fetch(`${API_BASE}/api/chat`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        message: messageText,
        session_id: "session_" + DEVICE_ID,
        voice_response: voiceOutputEnabled
      })
    });

    if (!res.ok) {
      let errorMsg = `Server error (${res.status})`;
      try {
        const errJson = await res.json();
        if (errJson && errJson.detail) {
          errorMsg = typeof errJson.detail === "string" ? errJson.detail : JSON.stringify(errJson.detail);
        }
      } catch (_) {
        const errText = await res.text();
        if (errText) errorMsg = errText;
      }
      throw new Error(errorMsg);
    }

    let data;
    try {
      data = await res.json();
    } catch (_) {
      throw new Error(`Server returned non-JSON response (${res.status})`);
    }
    removeMessage(thinkingId);

    // Format tool calls badge if any
    let toolBadges = "";
    if (data.tool_calls && data.tool_calls.length > 0) {
      data.tool_calls.forEach(tc => {
        toolBadges += `<div class="tool-badge">⚡ Tool: <strong>${tc.tool}</strong> (${JSON.stringify(tc.args)})</div>`;
      });
    }

    // Interactive confirmation buttons if action requires user confirmation
    let confirmationHtml = "";
    if (data.confirmation_required && data.pending_action) {
      const pa = data.pending_action;
      const contactLabel = escapeHtml(pa.contact_name || pa.display_target || "target");
      const isCall = pa.action === "make_phone_call" || pa.type === "make_phone_call";
      const channel = pa.channel || (isCall ? "Call" : "Send");
      const confirmIcon = isCall ? "📞" : (channel === "WhatsApp" ? "💬" : (channel === "Email" ? "📧" : "✉️"));
      const confirmText = isCall ? `Yes, Call ${contactLabel}` : `Yes, Send ${channel}`;
      setAgentState("idle", `Confirm ${channel}?`, "❓");

      confirmationHtml = `
        <div class="confirmation-actions">
          <button type="button" class="btn-confirm-action" onclick="confirmCallAction(true)">${confirmIcon} ${confirmText}</button>
          <button type="button" class="btn-cancel-action" onclick="confirmCallAction(false)">❌ Cancel</button>
        </div>
      `;
    }

    appendMessage("assistant", data.reply, toolBadges + confirmationHtml);

    // Play Voice Output (ElevenLabs or fallback Web Speech)
    if (voiceOutputEnabled) {
      if (data.audio_base64) {
        playBase64Audio(data.audio_base64);
      } else if (data.use_client_tts && "speechSynthesis" in window) {
        speakTextFallback(data.reply);
      } else {
        // Voice is disabled or not available: check continuous listening
        checkContinuousListenResume();
      }
    } else {
      checkContinuousListenResume();
    }

    // Trigger phone calling HUD if call action returned
    if (data.call_action) {
      setAgentState("executing", `Calling ${data.call_action.contact_name || data.call_action.phone_number}...`, "📞");
      triggerMobilePhoneCall(data.call_action.phone_number, data.call_action.contact_name);
    }

    // Trigger mobile messaging if message action returned
    if (data.message_action) {
      const ma = data.message_action;
      const ch = ma.type === "WHATSAPP" ? "WhatsApp" : (ma.type === "EMAIL" ? "Email" : "SMS");
      const icon = ma.type === "EMAIL" ? "📧" : (ma.type === "WHATSAPP" ? "💬" : "✉️");
      const target = ma.to_email || ma.contact_name || ma.phone_number;
      setAgentState("executing", `Sent ${ch} to ${target}`, icon);
      triggerMobileMessage(data.message_action);
    }

  } catch (err) {
    removeMessage(thinkingId);
    setAgentState("idle", "Ready", "⚡");
    const displayErr = err.message.includes("Server error") ? err.message : `Error communicating with assistant: ${err.message}`;
    appendMessage("assistant", "⚠️ " + displayErr);
    checkContinuousListenResume();
  }
}

// Global handler for action confirmation buttons
window.confirmCallAction = function(confirmed) {
  document.querySelectorAll(".confirmation-actions button").forEach(b => b.disabled = true);
  sendMessage(confirmed ? "yes" : "no");
};

function appendMessage(sender, text, extraHtml = "") {
  const container = document.getElementById("chat-messages");
  const msgDiv = document.createElement("div");
  const id = "msg_" + Date.now() + "_" + Math.random().toString(36).substring(2, 5);
  msgDiv.id = id;
  msgDiv.className = `message ${sender}`;
  msgDiv.innerHTML = `<div>${escapeHtml(text).replace(/\n/g, "<br>")}</div>${extraHtml}`;
  container.appendChild(msgDiv);
  container.scrollTop = container.scrollHeight;
  return id;
}

function removeMessage(id) {
  const el = document.getElementById(id);
  if (el) el.remove();
}

function escapeHtml(str) {
  const div = document.createElement("div");
  div.innerText = str;
  return div.innerHTML;
}

// ========================================================
// Audio Playback & Barge-In ("Cut in mid") Controller
// ========================================================
function stopAssistantSpeech() {
  if (activeAudio) {
    try {
      activeAudio.pause();
      activeAudio.currentTime = 0;
    } catch (e) {}
    activeAudio = null;
  }
  if (window.speechSynthesis && (window.speechSynthesis.speaking || window.speechSynthesis.pending)) {
    try { window.speechSynthesis.cancel(); } catch (e) {}
  }
  if (agentState === "speaking") {
    setAgentState("idle", "Ready", "⚡");
  }
}

function checkContinuousListenResume() {
  if (continuousVoice) {
    setTimeout(() => {
      if (!isRecording && continuousVoice) {
        startVoiceRecording();
      }
    }, 450);
  }
}

function playBase64Audio(base64Audio) {
  try {
    stopAssistantSpeech();
    activeAudio = new Audio("data:audio/mpeg;base64," + base64Audio);
    setAgentState("speaking", "Speaking...", "🔊");

    activeAudio.onended = () => {
      activeAudio = null;
      setAgentState("idle", "Ready", "⚡");
      checkContinuousListenResume();
    };

    activeAudio.onerror = (e) => {
      console.warn("Audio playback error:", e);
      activeAudio = null;
      setAgentState("idle", "Ready", "⚡");
      checkContinuousListenResume();
    };

    activeAudio.play().catch(e => {
      console.warn("Auto-play prevented or audio error:", e);
      activeAudio = null;
      setAgentState("idle", "Ready", "⚡");
      checkContinuousListenResume();
    });
  } catch (e) {
    console.error("Failed to play audio:", e);
    activeAudio = null;
    setAgentState("idle", "Ready", "⚡");
    checkContinuousListenResume();
  }
}

function speakTextFallback(text) {
  if (!window.speechSynthesis) return;
  stopAssistantSpeech();
  const clean = text.replace(/[*#_~`]/g, "");
  const utterance = new SpeechSynthesisUtterance(clean);
  utterance.rate = 1.05;
  utterance.pitch = 1.0;
  setAgentState("speaking", "Speaking...", "🔊");

  utterance.onend = () => {
    setAgentState("idle", "Ready", "⚡");
    checkContinuousListenResume();
  };

  utterance.onerror = () => {
    setAgentState("idle", "Ready", "⚡");
    checkContinuousListenResume();
  };

  window.speechSynthesis.speak(utterance);
}

// ========================================================
// Floating Agent Widget & State Management (Dots & Muse Style)
// ========================================================
function setAgentState(state, labelText = "Ready", iconEmoji = "⚡") {
  agentState = state;
  const container = document.getElementById("floating-agent");
  const textEl = document.getElementById("agent-status-label");
  const iconEl = document.getElementById("agent-core-icon");

  if (container) {
    container.classList.remove("state-idle", "state-listening", "state-thinking", "state-speaking", "state-executing");
    container.classList.add(`state-${state}`);
  }
  if (textEl && labelText) {
    textEl.textContent = labelText;
  }
  if (iconEl && iconEmoji) {
    iconEl.textContent = iconEmoji;
  }
}

window.handleAgentOrbClick = function() {
  // If assistant is speaking, cut off immediately and start listening!
  if (activeAudio || (window.speechSynthesis && window.speechSynthesis.speaking)) {
    stopAssistantSpeech();
    startVoiceRecording();
    return;
  }
  // Turn on continuous assistant mode on orb click
  if (!continuousVoice) {
    window.toggleContinuousVoice();
  } else {
    toggleVoiceRecording();
  }
};

window.toggleContinuousVoice = function() {
  continuousVoice = !continuousVoice;
  const btn = document.getElementById("continuous-voice-btn");
  if (btn) {
    btn.style.background = continuousVoice ? "linear-gradient(135deg, #10b981 0%, #059669 100%)" : "rgba(16, 185, 129, 0.15)";
    btn.style.color = continuousVoice ? "#ffffff" : "#34d399";
    btn.style.borderColor = continuousVoice ? "#10b981" : "rgba(16, 185, 129, 0.4)";
    btn.textContent = continuousVoice ? "🎙️ Active (Always Ready)" : "🎙️ Always Ready";
  }
  if (continuousVoice && !isRecording && agentState !== "speaking") {
    startVoiceRecording();
  }
};

function setupFloatingAgentDrag() {
  const agent = document.getElementById("floating-agent");
  if (!agent) return;

  let isDragging = false;
  let startX, startY, initialX, initialY;

  const onStart = (e) => {
    if (e.target && e.target.closest("#floating-agent-orb")) return;
    isDragging = true;
    const clientX = e.touches ? e.touches[0].clientX : e.clientX;
    const clientY = e.touches ? e.touches[0].clientY : e.clientY;
    startX = clientX;
    startY = clientY;
    const rect = agent.getBoundingClientRect();
    initialX = rect.left;
    initialY = rect.top;
  };

  const onMove = (e) => {
    if (!isDragging) return;
    const clientX = e.touches ? e.touches[0].clientX : e.clientX;
    const clientY = e.touches ? e.touches[0].clientY : e.clientY;
    const dx = clientX - startX;
    const dy = clientY - startY;
    agent.style.left = `${initialX + dx}px`;
    agent.style.top = `${initialY + dy}px`;
    agent.style.right = "auto";
  };

  const onEnd = () => {
    isDragging = false;
  };

  agent.addEventListener("mousedown", onStart);
  window.addEventListener("mousemove", onMove);
  window.addEventListener("mouseup", onEnd);

  agent.addEventListener("touchstart", onStart, { passive: true });
  window.addEventListener("touchmove", onMove, { passive: true });
  window.addEventListener("touchend", onEnd);
}

// Trigger mobile WhatsApp, SMS, or Email from browser
function triggerMobileMessage(action) {
  if (action.type === "EMAIL") {
    const to = encodeURIComponent(action.to_email || "");
    const subj = encodeURIComponent(action.subject || "Message from AgentTool Assistant");
    const body = encodeURIComponent(action.message || "");
    console.log(`[Auto-Email] Triggering mailto:${to}?subject=${subj}&body=${body}`);
    setTimeout(() => {
      window.location.href = `mailto:${to}?subject=${subj}&body=${body}`;
    }, 600);
    return;
  }
  const cleanNum = (action.phone_number || "").replace(/[^\d+]/g, "");
  const text = encodeURIComponent(action.message || "");
  if (action.type === "WHATSAPP") {
    console.log(`[Auto-WhatsApp] Opening https://api.whatsapp.com/send?phone=${cleanNum}&text=${text}`);
    setTimeout(() => {
      window.open(`https://api.whatsapp.com/send?phone=${cleanNum}&text=${text}`, "_blank");
    }, 600);
  } else {
    console.log(`[Auto-SMS] Triggering sms:${cleanNum}?body=${text}`);
    setTimeout(() => {
      window.location.href = `sms:${cleanNum}?body=${text}`;
    }, 600);
  }
}

// ========================================================
// Speech-to-Text (STT) Recording & Barge-in
// ========================================================
function setupSpeechRecognition() {
  const langSelect = document.getElementById("voice-lang-select");
  if (langSelect) {
    langSelect.value = currentSpeechLang;
    langSelect.addEventListener("change", (e) => {
      currentSpeechLang = e.target.value;
      localStorage.setItem("agenttool_speech_lang", currentSpeechLang);
      if (speechRecognition) {
        speechRecognition.lang = currentSpeechLang;
      }
      console.log("[STT Language Changed]:", currentSpeechLang);
    });
  }

  const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (SpeechRecognition) {
    speechRecognition = new SpeechRecognition();
    speechRecognition.continuous = false;
    speechRecognition.interimResults = false;
    speechRecognition.lang = currentSpeechLang;

    // Barge-in: immediately stop assistant speech when user starts talking!
    speechRecognition.onspeechstart = () => {
      stopAssistantSpeech();
      setAgentState("listening", "Listening...", "🎙️");
    };

    speechRecognition.onsoundstart = () => {
      stopAssistantSpeech();
      setAgentState("listening", "Listening...", "🎙️");
    };

    speechRecognition.onresult = (event) => {
      const transcript = event.results[0][0].transcript;
      console.log("[STT Transcript]:", transcript);
      document.getElementById("chat-input").value = transcript;
      sendMessage(transcript);
    };

    speechRecognition.onerror = (event) => {
      console.warn("Speech recognition error:", event.error);
      stopRecordingUI();
      if (continuousVoice && event.error !== "not-allowed" && !currentCallTimer) {
        setTimeout(() => {
          if (!isRecording && continuousVoice && agentState !== "speaking" && !currentCallTimer) {
            startVoiceRecording();
          }
        }, 800);
      }
    };

    speechRecognition.onend = () => {
      stopRecordingUI();
      if (continuousVoice && agentState !== "speaking" && !currentCallTimer) {
        setTimeout(() => {
          if (!isRecording && continuousVoice && agentState !== "speaking" && !currentCallTimer) {
            startVoiceRecording();
          }
        }, 400);
      }
    };
  }
}

function toggleVoiceRecording() {
  if (isRecording) {
    stopVoiceRecording();
  } else {
    startVoiceRecording();
  }
}

function startVoiceRecording() {
  // Cut in mid: stop any assistant speech immediately!
  stopAssistantSpeech();

  isRecording = true;
  const micBtn = document.getElementById("mic-btn");
  if (micBtn) micBtn.classList.add("recording");
  setAgentState("listening", "Listening...", "🎙️");

  // Prefer native Web Speech API recognition if supported
  if (speechRecognition) {
    try {
      speechRecognition.start();
      return;
    } catch (e) {
      console.warn("Could not start Web Speech Recognition:", e);
    }
  }

  // Fallback to MediaRecorder for server-side ElevenLabs STT
  if (navigator.mediaDevices && navigator.mediaDevices.getUserMedia) {
    navigator.mediaDevices.getUserMedia({ audio: true })
      .then(stream => {
        mediaRecorder = new MediaRecorder(stream);
        audioChunks = [];
        mediaRecorder.ondataavailable = e => audioChunks.push(e.data);
        mediaRecorder.onstop = uploadRecordedAudio;
        mediaRecorder.start();
      })
      .catch(err => {
        console.warn("MediaRecorder permission or access issue:", err);
        // Seamless fallback to phone's native voice recorder!
        const audioFileInput = document.getElementById("audio-file-input");
        if (audioFileInput) {
          audioFileInput.click();
        } else {
          alert("Microphone permission was denied. Please allow microphone access or type your command.");
        }
        stopRecordingUI();
      });
  } else {
    // Seamless fallback to phone's native voice recorder for HTTP origins!
    const audioFileInput = document.getElementById("audio-file-input");
    if (audioFileInput) {
      audioFileInput.click();
    } else {
      alert("Voice recording is not supported in this browser. Please type your message.");
    }
    stopRecordingUI();
  }
}

function stopVoiceRecording() {
  if (speechRecognition) {
    try { speechRecognition.stop(); } catch (e) {}
  }
  if (mediaRecorder && mediaRecorder.state !== "inactive") {
    mediaRecorder.stop();
  }
  stopRecordingUI();
}

function stopRecordingUI() {
  isRecording = false;
  const micBtn = document.getElementById("mic-btn");
  if (micBtn) micBtn.classList.remove("recording");
  if (agentState === "listening") {
    setAgentState("idle", "Ready", "⚡");
  }
}

async function uploadRecordedAudio() {
  if (audioChunks.length === 0) return;
  const audioBlob = new Blob(audioChunks, { type: "audio/wav" });
  const formData = new FormData();
  formData.append("file", audioBlob, "user_voice.wav");

  const statusId = appendMessage("assistant", "🎙️ Transcribing voice via ElevenLabs...");

  try {
    const res = await fetch(`${API_BASE}/api/voice/stt`, {
      method: "POST",
      body: formData
    });
    const data = await res.json();
    removeMessage(statusId);

    if (data.text && data.text.trim()) {
      document.getElementById("chat-input").value = data.text;
      sendMessage(data.text);
    } else {
      appendMessage("assistant", "Could not transcribe audio. Please try speaking clearly or typing.");
    }
  } catch (e) {
    removeMessage(statusId);
    appendMessage("assistant", "Audio upload failed: " + e.message);
  }
}

// ========================================================
// Automated Phone Call Execution & HUD
// ========================================================
function triggerMobilePhoneCall(phoneNumber, contactName) {
  if (!phoneNumber) {
    appendMessage("assistant", `⚠️ Cannot place call: No phone number specified for "${contactName || 'contact'}".`);
    return;
  }

  const cleanNum = phoneNumber.replace(/[^\d+]/g, "");
  if (!cleanNum || cleanNum.length < 3) {
    console.warn("Cannot call: No valid phone digits in", phoneNumber);
    appendMessage("assistant", `⚠️ Cannot place call: No valid phone digits found for "${contactName || phoneNumber}". Please check Contacts.`);
    return;
  }

  const hud = document.getElementById("call-hud");
  const nameEl = document.getElementById("hud-name");
  const numEl = document.getElementById("hud-number");
  const statusEl = document.getElementById("hud-status");
  const timerEl = document.getElementById("hud-timer");

  nameEl.textContent = contactName || cleanNum;
  numEl.textContent = cleanNum;
  statusEl.textContent = "Initiating Call...";
  timerEl.textContent = "00:00";
  callSeconds = 0;

  // Show full-screen Call HUD
  hud.classList.add("active");


  // Start Call Duration Timer
  if (currentCallTimer) clearInterval(currentCallTimer);
  currentCallTimer = setInterval(() => {
    callSeconds++;
    const mins = String(Math.floor(callSeconds / 60)).padStart(2, "0");
    const secs = String(callSeconds % 60).padStart(2, "0");
    timerEl.textContent = `${mins}:${secs}`;
    if (callSeconds === 3) {
      statusEl.textContent = "Connected • Live Call";
    }
  }, 1000);

  // AUTOMATED NATIVE PHONE CALL TRIGGER:
  // On real Android / iOS devices, setting window.location.href to "tel:..."
  // directly invokes the native device dialer with the number ready!
  console.log(`[Auto-Call] Triggering native tel:${cleanNum}`);
  setTimeout(() => {
    try {
      window.location.href = `tel:${cleanNum}`;
    } catch (e) {
      console.warn("Could not trigger tel: protocol automatically:", e);
    }
  }, 700);

  // Send update to WebSocket
  if (socket && socket.readyState === WebSocket.OPEN) {
    socket.send(JSON.stringify({
      type: "CALL_STATUS",
      data: {
        phone_number: cleanNum,
        contact_name: contactName,
        status: "ringing"
      }
    }));
  }

  // Refresh call logs in MongoDB
  setTimeout(loadCallLogs, 2000);
}

function endCurrentCall() {
  const hud = document.getElementById("call-hud");
  hud.classList.remove("active");
  if (currentCallTimer) {
    clearInterval(currentCallTimer);
    currentCallTimer = null;
  }

  appendMessage("assistant", "📴 Phone call ended.");

  if (socket && socket.readyState === WebSocket.OPEN) {
    socket.send(JSON.stringify({
      type: "CALL_STATUS",
      data: {
        status: "completed",
        duration_seconds: callSeconds
      }
    }));
  }
  loadCallLogs();
}

// ========================================================
// Screen Tracker & Real Device Navigation
// ========================================================
function simulateTap(target) {
  const targetId = "sim-app-" + target.toLowerCase();
  const el = document.getElementById(targetId);
  if (el) {
    el.classList.add("active-highlight");
    setTimeout(() => el.classList.remove("active-highlight"), 1200);
  }
  appendMessage("assistant", `🎯 Screen tracker tapped: <strong>${target}</strong>`);
}

async function connectAdbDevice() {
  const ipInput = document.getElementById("adb-ip-input");
  const ipPort = ipInput ? ipInput.value.trim() : "";
  const statusEl = document.getElementById("adb-status-indicator");

  statusEl.innerHTML = `<span>⏳ Connecting to device...</span>`;

  try {
    const res = await fetch(`${API_BASE}/api/device/connect-adb`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ip_port: ipPort || null })
    });
    const data = await res.json();
    if (data.device && data.device.connected) {
      statusEl.innerHTML = `<span style="color: var(--success);">🟢 Physical Device Connected: ${data.device.model} (${data.device.device_id})</span>`;
      appendMessage("assistant", `⚡ Connected to physical Android device: ${data.device.model}! Real screen tracking and autonomous navigation enabled.`);
      refreshScreenTracker();
    } else {
      statusEl.innerHTML = `<span style="color: var(--danger);">🔴 No device detected. Connect via USB debugging or enter Wi-Fi IP:5555</span>`;
    }
  } catch (err) {
    statusEl.innerHTML = `<span style="color: var(--danger);">⚠️ Connection error: ${err.message}</span>`;
  }
}

async function refreshScreenTracker() {
  try {
    const res = await fetch(`${API_BASE}/api/device/screen/live`);
    const data = await res.json();
    const treeBox = document.getElementById("element-tree-box");
    const liveImg = document.getElementById("live-screen-img");
    const simScreen = document.getElementById("sim-screen");
    const statusEl = document.getElementById("adb-status-indicator");

    if (data.mode === "adb_live" && data.has_screenshot && data.screenshot_base64) {
      liveImg.src = "data:image/jpeg;base64," + data.screenshot_base64;
      liveImg.style.display = "block";
      simScreen.style.display = "none";
      if (statusEl && data.device) {
        statusEl.innerHTML = `<span style="color: var(--success);">🟢 Live Physical Screen: ${data.device.model} (${data.device.device_id})</span>`;
      }
    } else {
      liveImg.style.display = "none";
      simScreen.style.display = "flex";
      if (statusEl) {
        statusEl.innerHTML = `<span>⚪ Mode: Web Simulator (Connect via ADB to track real phone)</span>`;
      }
    }

    if (data.interactive_elements && data.interactive_elements.length > 0) {
      treeBox.innerHTML = data.interactive_elements.map(el => `
        <div class="tree-node" style="cursor: pointer;" onclick="tapLiveElement('${el.label || el.text}')">
          <span class="tag">&lt;${el.class || 'View'}&gt;</span>
          <span style="color: #f8fafc;">"${el.label || el.text || el.id}"</span>
          <span style="color: #64748b;">${el.bounds ? '[' + el.bounds.join(',') + ']' : ''}</span>
        </div>
      `).join("");
    } else {
      treeBox.innerHTML = `<p style="color: var(--text-dim); text-align: center;">No on-screen elements extracted yet.</p>`;
    }
  } catch (e) {
    console.error("Error fetching screen state:", e);
  }
}

async function tapLiveElement(label) {
  if (!label) return;
  appendMessage("assistant", `🎯 Tapping on physical screen: <strong>${label}</strong>...`);
  try {
    await fetch(`${API_BASE}/api/device/tap`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ target: label })
    });
    setTimeout(refreshScreenTracker, 800);
  } catch (err) {
    console.error("Tap error:", err);
  }
}

async function handleLiveScreenClick(event) {
  const img = event.target;
  const rect = img.getBoundingClientRect();
  const clickX = event.clientX - rect.left;
  const clickY = event.clientY - rect.top;

  const scaleX = (img.naturalWidth || rect.width) / rect.width;
  const scaleY = (img.naturalHeight || rect.height) / rect.height;
  const realX = Math.round(clickX * scaleX);
  const realY = Math.round(clickY * scaleY);

  appendMessage("assistant", `🎯 Clicked physical screen at (${realX}, ${realY})`);

  try {
    await fetch(`${API_BASE}/api/device/tap`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ x: realX, y: realY })
    });
    setTimeout(refreshScreenTracker, 800);
  } catch (err) {
    console.error("Live click error:", err);
  }
}

// ========================================================
// MongoDB Contacts & Call History
// ========================================================
async function loadContacts() {
  try {
    const res = await fetch(`${API_BASE}/api/contacts`);
    const contacts = await res.json();
    const container = document.getElementById("contacts-container");
    if (!contacts || contacts.length === 0) {
      container.innerHTML = `<p style="font-size: 12px; color: var(--text-dim); text-align: center;">No contacts found in MongoDB.</p>`;
      return;
    }
    container.innerHTML = contacts.map(c => `
      <div class="contact-card">
        <div class="contact-meta">
          <h4>${escapeHtml(c.name)}</h4>
          <p>${escapeHtml(c.phone)} ${c.notes ? '• ' + escapeHtml(c.notes) : ''}</p>
        </div>
        <button class="call-action-btn" onclick="triggerMobilePhoneCall('${c.phone}', '${escapeHtml(c.name)}')">
          📞 Call
        </button>
      </div>
    `).join("");
  } catch (e) {
    console.error("Error loading contacts:", e);
  }
}

async function saveNewContactFromUI() {
  const nameInput = document.getElementById("new-contact-name");
  const phoneInput = document.getElementById("new-contact-phone");
  const name = nameInput.value.trim();
  const phone = phoneInput.value.trim();

  if (!name || !phone) {
    alert("Please enter both Name and Phone number.");
    return;
  }

  try {
    const res = await fetch(`${API_BASE}/api/contacts`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name, phone, notes: "Added from UI" })
    });
    if (res.ok) {
      nameInput.value = "";
      phoneInput.value = "";
      loadContacts();
      appendMessage("assistant", `✅ Saved contact ${name} (${phone}) to MongoDB.`);
    }
  } catch (e) {
    alert("Error adding contact: " + e.message);
  }
}

async function loadCallLogs() {
  try {
    const res = await fetch(`${API_BASE}/api/calls`);
    const logs = await res.json();
    const container = document.getElementById("call-logs-container");
    if (!logs || logs.length === 0) {
      container.innerHTML = `<p style="font-size: 12px; color: var(--text-dim); text-align: center;">No call logs in MongoDB yet.</p>`;
      return;
    }
    container.innerHTML = logs.map(l => `
      <div class="contact-card" style="padding: 10px 14px;">
        <div>
          <div style="font-weight: 600; color: #fff;">${escapeHtml(l.contact_name)} <span style="font-weight: 400; color: var(--text-muted);">(${escapeHtml(l.phone_number)})</span></div>
          <div style="font-size: 11px; color: var(--text-dim); margin-top: 2px;">${new Date(l.timestamp).toLocaleTimeString()} • Initiated by: ${l.initiated_by}</div>
        </div>
        <span style="font-size: 11px; color: var(--success); font-weight: 600; text-transform: uppercase;">${l.status}</span>
      </div>
    `).join("");
  } catch (e) {
    console.error("Error loading call logs:", e);
  }
}

// ========================================================
// System Diagnostics & Health Check
// ========================================================
async function checkSystemStatus() {
  try {
    const res = await fetch(`${API_BASE}/api/status`);
    const data = await res.json();

    // MongoDB
    const mongoStatus = document.getElementById("diag-mongo-status");
    const mongoDot = document.getElementById("diag-mongo-dot");
    if (data.mongodb.connected) {
      mongoStatus.textContent = `Connected to ${data.mongodb.database} (${data.mongodb.uri})`;
      mongoDot.style.backgroundColor = "#10b981";
    } else {
      mongoStatus.textContent = `Offline / Fallback Memory Mode (${data.mongodb.uri})`;
      mongoDot.style.backgroundColor = "#ef4444";
    }

    // Gemma
    const gemmaStatus = document.getElementById("diag-gemma-status");
    gemmaStatus.textContent = `Provider: ${data.gemma.provider.toUpperCase()} (Model: ${data.gemma.model})`;

    // Voice
    const voiceStatus = document.getElementById("diag-voice-status");
    if (data.elevenlabs.configured) {
      voiceStatus.textContent = `ElevenLabs API Active (Voice ID: ${data.elevenlabs.voice_id})`;
    } else {
      voiceStatus.textContent = "Browser Speech API (Add ELEVENLABS_API_KEY in .env to activate ElevenLabs)";
    }
  } catch (e) {
    console.error("Error checking status:", e);
  }
}
