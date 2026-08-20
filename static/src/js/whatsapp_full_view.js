/** @odoo-module **/

import { registry } from "@web/core/registry";
import { rpc } from "@web/core/network/rpc";

const { Component, useState, onWillStart, onMounted, onWillUnmount, useRef } = owl;

// الإيموجيز السريعة اللي بتظهر أول ما تدوس على رسالة - زي واتساب بالظبط.
const QUICK_REACTIONS = ["👍", "❤️", "😂", "😮", "😢", "🙏"];

// لما المستخدم يدوس على "+" بيفتحله مجموعة إيموجيز أوسع يختار منها.
const EXTRA_REACTIONS = [
    "😀", "😁", "😆", "😅", "🤣", "😊", "😇", "🙂", "🙃", "😉",
    "😍", "🥰", "😘", "😜", "🤪", "😎", "🤩", "🥳", "😴", "🤔",
    "🤭", "🤗", "🙄", "😐", "😏", "😢", "😭", "😤", "😡", "🤬",
    "😱", "😨", "🥺", "😬", "🤢", "🤮", "🤧", "🥵", "🥶", "😷",
    "🤯", "😳", "🤐", "🤫", "🫡", "🤝", "👏", "🙌", "👌", "✌️",
    "🤞", "💪", "🙏", "👍", "👎", "👋", "🔥", "🎉", "💯", "❤️",
    "🧡", "💛", "💚", "💙", "💜", "🖤", "🤍", "💔", "✨", "⭐",
];

export class WhatsappFullView extends Component {
    setup() {
        this.messagesRef = useRef("messagesEnd");
        this.state = useState({
            phase: "loading", // loading | qr | chat
            qrImage: false,
            pairingCode: false,
            connectionState: "connecting",
            statusError: false,
            conversations: [],
            activeTab: "all", // all | unread | groups | favorites
            selectedPhone: false,
            selectedName: false,
            messages: [],
            search: "",
            draft: "",
            sending: false,
            attachment: null,
            attachmentPreviewUrl: false,
            attachmentIsVoice: false,
            attachmentVoiceDuration: "",
            failedImageIds: [],
            loadingOlder: false,
            noMoreOlder: false,
            // قائمة اختيارات الرسالة (React / Forward / Copy / حذف)
            actionMenu: { open: false, messageId: false, top: 0, left: 0, moreOpen: false },
            // شاشة اختيار المحادثة اللي هتتفورورد لها الرسالة
            forwardPanel: { open: false, messageId: false, phone: "" },
            // حالة تسجيل الرسالة الصوتية (مايك حقيقي جوه الصفحة)
            recording: { active: false, seconds: 0 },
        });

        this.fileInputRef = useRef("fileInput");
        this._statusTimer = null;
        this._convTimer = null;
        this._msgTimer = null;

        // خاصين بالتسجيل الصوتي - مش جوه state عشان مش محتاجين reactivity
        // عليهم (الـ MediaRecorder/Stream نفسهم، مش قيم بسيطة).
        this._mediaRecorder = null;
        this._recordedChunks = [];
        this._recordingStream = null;
        this._recordingTimer = null;
        this._pendingRecordingAction = null; // "attach" | "discard"

        this._onDocumentClick = (ev) => {
            // أي دوسة برّه القائمة أو شاشة الفورورد تقفلهم.
            if (this.state.actionMenu.open && !ev.target.closest(".o_wa_action_menu, .o_wa_bubble")) {
                this.closeActionMenu();
            }
            if (this.state.forwardPanel.open && !ev.target.closest(".o_wa_forward_panel")) {
                this.closeForwardPanel();
            }
        };

        onWillStart(async () => {
            await this._checkStatus();
        });

        onMounted(() => {
            this._statusTimer = setInterval(() => this._checkStatus(), 4000);
            document.addEventListener("click", this._onDocumentClick, true);
        });

        onWillUnmount(() => {
            clearInterval(this._statusTimer);
            clearInterval(this._convTimer);
            clearInterval(this._msgTimer);
            document.removeEventListener("click", this._onDocumentClick, true);
            // لو المستخدم قفل الصفحة أو غيّر المحادثة والمايك لسه شغال،
            // نوقف الـ stream عشان مؤشر المايك يقفل من المتصفح.
            clearInterval(this._recordingTimer);
            if (this._recordingStream) {
                this._recordingStream.getTracks().forEach((t) => t.stop());
                this._recordingStream = null;
            }
        });
    }

    get quickReactions() {
        return QUICK_REACTIONS;
    }

    // ------------------------------------------------------------------
    async _checkStatus() {
        try {
            const result = await rpc("/whatsapp/fullview/status", {});
            this.state.connectionState = result.connection_state;
            this.state.statusError = result.error || false;
            if (result.connection_state === "open") {
                if (this.state.phase !== "chat") {
                    this.state.phase = "chat";
                    clearInterval(this._statusTimer);
                    await this._loadConversations();
                    this._convTimer = setInterval(() => this._loadConversations(), 6000);
                }
            } else {
                this.state.phase = "qr";
                this.state.qrImage = result.qr_image || false;
                this.state.pairingCode = result.pairing_code || false;
            }
        } catch (e) {
            // Background poll - never let a transient failure (e.g. a
            // momentary DB connection pool spike) surface as the generic
            // Odoo "Server Error" toast every few seconds. Log it so it's
            // still diagnosable in the console, and just retry on the
            // next tick.
            console.warn("WhatsApp fullview: status check failed, will retry", e);
        }
    }

    // ------------------------------------------------------------------
    async _loadConversations() {
        try {
            const conversations = await rpc("/whatsapp/fullview/conversations", {});
            this.state.conversations = conversations;
        } catch (e) {
            console.warn("WhatsApp fullview: failed to load conversations, will retry", e);
        }
    }

    get filteredConversations() {
        const term = (this.state.search || "").trim().toLowerCase();
        let list = this.state.conversations;
        if (this.state.activeTab === "unread") {
            list = list.filter((c) => c.unread_count > 0);
        } else if (this.state.activeTab === "groups") {
            list = list.filter((c) => c.is_group);
        } else if (this.state.activeTab === "favorites") {
            list = list.filter((c) => c.is_favorite);
        }
        if (!term) {
            return list;
        }
        return list.filter((c) =>
            (c.contact_name || "").toLowerCase().includes(term) ||
            (c.phone_number || "").includes(term)
        );
    }

    setActiveTab(tab) {
        this.state.activeTab = tab;
    }

    async toggleFavorite(conv, ev) {
        if (ev) ev.stopPropagation();
        try {
            const isFavorite = await rpc("/whatsapp/fullview/toggle_favorite", {
                phone_number: conv.phone_number,
            });
            conv.is_favorite = isFavorite;
        } catch (e) {
            this.env.services.notification.add(
                e.message || "تعذر تحديث المفضلة", { type: "danger" }
            );
        }
    }

    initials(name) {
        if (!name) return "?";
        return name.trim().charAt(0).toUpperCase();
    }

    // بتتستخدم في قايمة المحادثات بس (آخر نشاط): وقت لو النهاردة، تاريخ لو أقدم.
    formatDate(dateStr) {
        if (!dateStr) return "";
        const d = new Date(dateStr.replace(" ", "T") + "Z");
        const now = new Date();
        const sameDay = d.toDateString() === now.toDateString();
        if (sameDay) {
            return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
        }
        return d.toLocaleDateString();
    }

    // الوقت اللي بيبان جوه كل فقاعة رسالة - دايمًا وقت بس (زي واتساب
    // الأصلي)، التاريخ نفسه بيتعرض مرة واحدة كـ "فاصل يوم" فوق كل مجموعة
    // رسايل (شوف dayLabel() و get messagesWithSeparators() تحت).
    formatBubbleTime(dateStr) {
        if (!dateStr) return "";
        const d = new Date(dateStr.replace(" ", "T") + "Z");
        return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
    }

    // نص الفاصل فوق مجموعة الرسايل: "النهاردة" / "إمبارح" / تاريخ كامل.
    dayLabel(dateStr) {
        if (!dateStr) return "";
        const d = new Date(dateStr.replace(" ", "T") + "Z");
        const now = new Date();
        const startOfDay = (x) => new Date(x.getFullYear(), x.getMonth(), x.getDate());
        const diffDays = Math.round((startOfDay(now) - startOfDay(d)) / 86400000);
        if (diffDays === 0) return "النهاردة";
        if (diffDays === 1) return "إمبارح";
        return d.toLocaleDateString("ar-EG", { day: "numeric", month: "long", year: "numeric" });
    }

    // بترجّع نسخة من state.messages بعد ما تحقن فاصل يوم قبل أول رسالة
    // في كل يوم جديد - العنصر ده بيتعرف في الـ XML بعلامة is_day_separator.
    get messagesWithSeparators() {
        const out = [];
        let lastDay = null;
        for (const msg of this.state.messages) {
            const d = new Date((msg.date || "").replace(" ", "T") + "Z");
            const dayKey = d.toDateString();
            if (dayKey !== lastDay) {
                out.push({ is_day_separator: true, id: "sep-" + dayKey, label: this.dayLabel(msg.date) });
                lastDay = dayKey;
            }
            out.push(msg);
        }
        return out;
    }

    // ------------------------------------------------------------------
    async selectConversation(conv) {
        this.state.selectedPhone = conv.phone_number;
        this.state.selectedName = conv.contact_name;
        this.state.messages = [];
        this.state.noMoreOlder = false;
        this.closeActionMenu();
        this.closeForwardPanel();
        clearInterval(this._msgTimer);
        await this._loadMessages(true);
        try {
            await rpc("/whatsapp/fullview/mark_read", { phone_number: conv.phone_number });
        } catch (e) {
            console.warn("WhatsApp fullview: failed to mark conversation as read", e);
        }
        conv.unread_count = 0;
        this._msgTimer = setInterval(() => this._loadMessages(false), 3000);
    }

    async _loadMessages(reset) {
        if (!this.state.selectedPhone) return;
        // Messages are now ordered by their real WhatsApp timestamp, not by
        // insertion order, so the last item in the displayed array isn't
        // guaranteed to have the highest database id (e.g. right after an
        // older/history-synced message lands with a newer id but an older
        // timestamp). Polling must track the max id actually loaded so far,
        // not just the last one shown, or new messages can be missed.
        const afterId = (!reset && this.state.messages.length) ?
            Math.max(...this.state.messages.map((m) => m.id)) : 0;
        try {
            const messages = await rpc("/whatsapp/fullview/messages", {
                phone_number: this.state.selectedPhone,
                after_id: reset ? 0 : afterId,
            });
            if (reset) {
                this.state.messages = messages;
            } else if (messages.length) {
                this.state.messages = this.state.messages.concat(messages);
            }
            this._scrollToEnd();
        } catch (e) {
            // Recurring 3s poll - same reasoning as _checkStatus/_loadConversations:
            // don't spam the user with a toast for every transient hiccup.
            console.warn("WhatsApp fullview: failed to load messages, will retry", e);
        }
    }

    // بتحمّل أقدم رسايل قبل أول واحدة ظاهرة دلوقتي. لو مفيش حاجة تانية
    // مخزنة عندنا في أودو، بتجرب تجيبها من واتساب نفسه (sync_history)
    // مرة واحدة قبل ما تقول "مفيش رسايل أقدم".
    async loadOlderMessages() {
        if (!this.state.selectedPhone || this.state.loadingOlder || !this.state.messages.length) return;
        this.state.loadingOlder = true;
        const beforeId = this.state.messages[0].id;
        const el = this.messagesRef.el;
        const prevScrollHeight = el ? el.scrollHeight : 0;
        try {
            let older = await rpc("/whatsapp/fullview/messages", {
                phone_number: this.state.selectedPhone,
                before_id: beforeId,
            });
            if (!older.length) {
                const synced = await rpc("/whatsapp/fullview/sync_history", {
                    phone_number: this.state.selectedPhone,
                });
                if (synced && synced.imported) {
                    older = await rpc("/whatsapp/fullview/messages", {
                        phone_number: this.state.selectedPhone,
                        before_id: beforeId,
                    });
                }
            }
            if (older.length) {
                this.state.messages = older.concat(this.state.messages);
                requestAnimationFrame(() => {
                    if (el) el.scrollTop = el.scrollHeight - prevScrollHeight;
                });
            } else {
                this.state.noMoreOlder = true;
            }
        } catch (e) {
            this.env.services.notification.add(
                e.message || "تعذر تحميل الرسائل الأقدم", { type: "danger" }
            );
        } finally {
            this.state.loadingOlder = false;
        }
    }

    _scrollToEnd() {
        requestAnimationFrame(() => {
            if (this.messagesRef.el) {
                this.messagesRef.el.scrollTop = this.messagesRef.el.scrollHeight;
            }
        });
    }

    onAttachClick() {
        if (this.fileInputRef.el) {
            this.fileInputRef.el.click();
        }
    }

    onFileChange(ev) {
        const file = ev.target.files[0];
        if (!file) return;
        this.state.attachment = file;
        this.state.attachmentPreviewUrl = file.type.startsWith("image/") ?
            URL.createObjectURL(file) : false;
        this.state.attachmentIsVoice = false;
        this.state.attachmentVoiceDuration = "";
    }

    clearAttachment() {
        this.state.attachment = null;
        this.state.attachmentPreviewUrl = false;
        this.state.attachmentIsVoice = false;
        this.state.attachmentVoiceDuration = "";
        if (this.fileInputRef.el) {
            this.fileInputRef.el.value = "";
        }
    }

    // ------------------------------------------------------------------
    // تسجيل رسالة صوتية بالمايك جوه نفس الصفحة (زي واتساب بالظبط) -
    // بدل ما زرار المرفقات يفتح نافذة اختيار ملف المتصفح العادية اللي
    // بتفتح صفحة/تطبيق تسجيل منفصل. لما التسجيل يوقف، الصوت بيتحط
    // كمرفق عادي في الـ composer (زي أي صورة/ملف) وبيتبعت بنفس زرار
    // "إرسال" الموجود أصلاً.
    // ------------------------------------------------------------------
    get recordingTimeLabel() {
        const total = this.state.recording.seconds;
        const m = Math.floor(total / 60).toString().padStart(2, "0");
        const s = (total % 60).toString().padStart(2, "0");
        return `${m}:${s}`;
    }

    get hasDraftOrAttachment() {
        return !!(this.state.draft || "").trim() || !!this.state.attachment;
    }

    _pickRecorderMimeType() {
        const candidates = [
            "audio/ogg;codecs=opus",
            "audio/webm;codecs=opus",
            "audio/webm",
            "audio/mp4",
        ];
        for (const type of candidates) {
            if (window.MediaRecorder && MediaRecorder.isTypeSupported(type)) {
                return type;
            }
        }
        return "";
    }

    async onMicClick() {
        if (this.state.sending || this.state.recording.active) return;
        if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia || !window.MediaRecorder) {
            this.env.services.notification.add(
                "المتصفح ده مش بيدعم التسجيل الصوتي من جوه الصفحة", { type: "danger" }
            );
            return;
        }
        try {
            const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
            this._recordingStream = stream;
            const mimeType = this._pickRecorderMimeType();
            this._mediaRecorder = mimeType ?
                new MediaRecorder(stream, { mimeType }) : new MediaRecorder(stream);
            this._recordedChunks = [];
            this._mediaRecorder.addEventListener("dataavailable", (ev) => {
                if (ev.data && ev.data.size > 0) this._recordedChunks.push(ev.data);
            });
            this._mediaRecorder.addEventListener("stop", () => this._onRecordingStopped());
            this._mediaRecorder.start();
            this.state.recording.active = true;
            this.state.recording.seconds = 0;
            this._recordingTimer = setInterval(() => {
                this.state.recording.seconds++;
            }, 1000);
        } catch (e) {
            console.warn("WhatsApp fullview: mic access failed", e);
            this.env.services.notification.add(
                "مش قادر أوصل للمايك - تأكد إنك سمحت للمتصفح بالوصول له", { type: "danger" }
            );
        }
    }

    // بتوقف التسجيل وتحط الصوت كمرفق عادي في الـ composer.
    stopRecording() {
        if (!this._mediaRecorder || this._mediaRecorder.state === "inactive") return;
        this._pendingRecordingAction = "attach";
        this._mediaRecorder.stop();
    }

    // بتوقف التسجيل وترميه بلا رجعة (زرار سلة المهملات).
    cancelRecording() {
        this._pendingRecordingAction = "discard";
        if (this._mediaRecorder && this._mediaRecorder.state !== "inactive") {
            this._mediaRecorder.stop();
        } else {
            this._cleanupRecordingResources();
        }
    }

    _cleanupRecordingResources() {
        clearInterval(this._recordingTimer);
        this._recordingTimer = null;
        if (this._recordingStream) {
            this._recordingStream.getTracks().forEach((t) => t.stop());
            this._recordingStream = null;
        }
        this.state.recording.active = false;
    }

    _onRecordingStopped() {
        const action = this._pendingRecordingAction;
        this._pendingRecordingAction = null;
        const chunks = this._recordedChunks;
        this._recordedChunks = [];
        const durationLabel = this.recordingTimeLabel;
        const mimeType = (this._mediaRecorder && this._mediaRecorder.mimeType) || "audio/webm";
        this._cleanupRecordingResources();
        if (action !== "attach" || !chunks.length) return;
        const blob = new Blob(chunks, { type: mimeType });
        const ext = mimeType.includes("ogg") ? "ogg" : mimeType.includes("mp4") ? "m4a" : "webm";
        const file = new File([blob], `voice-${Date.now()}.${ext}`, { type: mimeType });
        this.state.attachment = file;
        this.state.attachmentPreviewUrl = false;
        this.state.attachmentIsVoice = true;
        this.state.attachmentVoiceDuration = durationLabel;
    }

    async sendMessage(ev) {
        if (ev) ev.preventDefault();
        const body = (this.state.draft || "").trim();
        if (!this.state.selectedPhone || this.state.sending) return;
        if (!body && !this.state.attachment) return;
        this.state.sending = true;
        try {
            let msg;
            if (this.state.attachment) {
                const formData = new FormData();
                formData.append("phone_number", this.state.selectedPhone);
                formData.append("caption", body);
                formData.append("file", this.state.attachment);
                const response = await fetch("/whatsapp/fullview/send_media", {
                    method: "POST",
                    body: formData,
                });
                const data = await response.json();
                if (!data.ok) {
                    throw new Error(data.error || "send_failed");
                }
                msg = data.message;
                this.clearAttachment();
            } else {
                msg = await rpc("/whatsapp/fullview/send", {
                    phone_number: this.state.selectedPhone,
                    body: body,
                });
            }
            this.state.messages.push(msg);
            this.state.draft = "";
            this._scrollToEnd();
            const conv = this.state.conversations.find((c) => c.phone_number === this.state.selectedPhone);
            if (conv) {
                conv.last_message = msg.body || (msg.has_media ? "📎 مرفق" : "");
                conv.last_date = msg.date;
            }
        } catch (e) {
            this.env.services.notification.add(
                e.message || "تعذر إرسال الرسالة", { type: "danger" }
            );
        } finally {
            this.state.sending = false;
        }
    }

    onDraftKeydown(ev) {
        if (ev.key === "Enter" && !ev.shiftKey) {
            this.sendMessage(ev);
        }
    }

    mediaUrl(msg) {
        return `/whatsapp/fullview/media/${msg.id}`;
    }

    onImageError(msgId) {
        if (!this.state.failedImageIds.includes(msgId)) {
            this.state.failedImageIds.push(msgId);
        }
    }

    fileIcon(mediaType) {
        if (mediaType === "video") return "🎬";
        if (mediaType === "audio") return "🎵";
        return "📄";
    }

    // ------------------------------------------------------------------
    // قائمة اختيارات الرسالة: React / Forward / Copy / حذف عندي بس
    // ------------------------------------------------------------------
    _findMessage(messageId) {
        return this.state.messages.find((m) => m.id === messageId);
    }

    onBubbleClick(msg, ev) {
        ev.stopPropagation();
        if (this.state.actionMenu.open && this.state.actionMenu.messageId === msg.id) {
            this.closeActionMenu();
            return;
        }
        this.closeForwardPanel();
        // القائمة (.o_wa_action_menu) مش جوه الـ .o_wa_messages القابلة للسكرول -
        // هي شقيقة ليها جوه .o_wa_thread. علشان كده لازم نحسب المكان بالنسبة
        // لـ .o_wa_thread (اللي هو الأب المُوضِّع الفعلي بتاعها)، من غير ما نضيف
        // scrollTop تاني، لأن bubbleRect أصلاً بيعكس مكان الرسالة على الشاشة
        // بعد السكرول.
        const threadEl = ev.currentTarget.closest(".o_wa_thread") || this.messagesRef.el;
        const containerRect = threadEl ? threadEl.getBoundingClientRect() : { top: 0, left: 0, width: 0, height: 0 };
        const bubbleRect = ev.currentTarget.getBoundingClientRect();

        // تقدير مبدئي لحجم القائمة عشان نمنعها تطلع برّه حدود الشات.
        const estMenuWidth = 230;
        const estMenuHeight = 170;

        let top = bubbleRect.bottom - containerRect.top + 4;
        if (top + estMenuHeight > containerRect.height) {
            // مفيش مكان تحت الرسالة - افتح القائمة فوقها بدل تحتها.
            top = bubbleRect.top - containerRect.top - estMenuHeight - 4;
            if (top < 0) top = 4;
        }

        let left = bubbleRect.left - containerRect.left;
        const maxLeft = Math.max(4, containerRect.width - estMenuWidth - 4);
        if (left > maxLeft) left = maxLeft;
        if (left < 4) left = 4;

        this.state.actionMenu = { open: true, messageId: msg.id, top, left, moreOpen: false };
    }

    closeActionMenu() {
        this.state.actionMenu = { open: false, messageId: false, top: 0, left: 0, moreOpen: false };
    }

    get actionMenuMessage() {
        return this.state.actionMenu.open ? this._findMessage(this.state.actionMenu.messageId) : false;
    }

    // بيفتح/يقفل لوحة الإيموجيز الإضافية اللي بتظهر لما تدوس على "+".
    toggleMoreReactions(ev) {
        if (ev) ev.stopPropagation();
        this.state.actionMenu.moreOpen = !this.state.actionMenu.moreOpen;
    }

    get extraReactions() {
        return EXTRA_REACTIONS;
    }

    async pickReaction(emoji) {
        const msg = this.actionMenuMessage;
        if (!msg) return;
        this.closeActionMenu();
        await this._sendReaction(msg, emoji);
    }

    async removeMyReaction(msg, ev) {
        if (ev) ev.stopPropagation();
        await this._sendReaction(msg, "");
    }

    async _sendReaction(msg, emoji) {
        try {
            const updated = await rpc("/whatsapp/fullview/react", { message_id: msg.id, emoji });
            const idx = this.state.messages.findIndex((m) => m.id === msg.id);
            if (idx !== -1) {
                this.state.messages[idx] = updated;
            }
        } catch (e) {
            this.env.services.notification.add(
                e.message || "تعذر إرسال التفاعل", { type: "danger" }
            );
        }
    }

    myReactionEmoji(msg) {
        const mine = (msg.reactions || []).find((r) => r.from_me);
        return mine ? mine.emoji : false;
    }

    copyMessage() {
        const msg = this.actionMenuMessage;
        this.closeActionMenu();
        if (!msg || !msg.body) return;
        navigator.clipboard.writeText(msg.body).then(() => {
            this.env.services.notification.add("اتنسخت", { type: "success" });
        }).catch(() => {});
    }

    async deleteLocally() {
        const msg = this.actionMenuMessage;
        this.closeActionMenu();
        if (!msg) return;
        try {
            await rpc("/whatsapp/fullview/delete_local", { message_id: msg.id });
            this.state.messages = this.state.messages.filter((m) => m.id !== msg.id);
        } catch (e) {
            this.env.services.notification.add(
                e.message || "تعذر حذف الرسالة", { type: "danger" }
            );
        }
    }

    // ------------------------------------------------------------------
    // فورورد: بيظهر بانل فيه المحادثات الحالية + خانة رقم يدوي
    // ------------------------------------------------------------------
    openForwardPanel() {
        const msg = this.actionMenuMessage;
        if (!msg) return;
        this.closeActionMenu();
        this.state.forwardPanel = { open: true, messageId: msg.id, phone: "" };
    }

    closeForwardPanel() {
        this.state.forwardPanel = { open: false, messageId: false, phone: "" };
    }

    get forwardableConversations() {
        return this.state.conversations.filter((c) => c.phone_number !== this.state.selectedPhone);
    }

    async forwardTo(phoneNumber) {
        const messageId = this.state.forwardPanel.messageId;
        if (!messageId || !phoneNumber) return;
        this.closeForwardPanel();
        try {
            await rpc("/whatsapp/fullview/forward", {
                message_id: messageId,
                to_phone_number: phoneNumber,
            });
            this.env.services.notification.add("اتبعتت", { type: "success" });
        } catch (e) {
            this.env.services.notification.add(
                e.message || "تعذر تحويل الرسالة", { type: "danger" }
            );
        }
    }

    onForwardManualSubmit(ev) {
        ev.preventDefault();
        this.forwardTo((this.state.forwardPanel.phone || "").trim());
    }
}

WhatsappFullView.template = "whatsapp_fullview.WhatsappFullView";

registry.category("actions").add("whatsapp_full_view", WhatsappFullView);