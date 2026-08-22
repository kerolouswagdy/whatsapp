/** @odoo-module **/

import { registry } from "@web/core/registry";
import { rpc } from "@web/core/network/rpc";

const { Component, useState, onWillStart, onMounted, onWillUnmount, useRef, markup } = owl;

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
            selectedIsGroup: false,
            presence: { state: false, last_seen: false },
            messages: [],
            search: "",
            draft: "",
            sending: false,
            replyTo: false,
            attachment: null,
            attachmentPreviewUrl: false,
            attachmentIsVoice: false,
            attachmentVoiceDuration: "",
            failedImageIds: [],
            loadingOlder: false,
            noMoreOlder: false,
            // قايمة اختيارات الرسالة (React / Forward / Copy / حذف)
            actionMenu: { open: false, messageId: false, top: 0, left: 0, moreOpen: false },
            // البحث جوه المحادثة المفتوحة (مختلف عن state.search اللي
            // بتفلتر بيه قايمة المحادثات في الشمال).
            msgSearch: { open: false, query: "", results: [], loading: false },
            // معرض الصور/الفيديوهات المشتركة في المحادثة المفتوحة.
            gallery: { open: false, items: [], loading: false },
            // تحديد كذا رسالة مع بعض (Multi-select) - selectedIds كـ
            // object {id: true} عشان يبقى سهل نتأكد لو رسالة محددة ولا لأ.
            selectMode: false,
            selectedIds: {},
            // شاشة اختيار المحادثة اللي هتتفورورد لها الرسالة
            forwardPanel: { open: false, messageIds: [], phone: "" },
            // حالة تسجيل الرسالة الصوتية (مايك حقيقي جوه الصفحة)
            recording: { active: false, seconds: 0 },
            // إيموجي بيكر لمربع الكتابة (مختلف عن الـ reactions)
            emojiPicker: { open: false },
            // صفحة معلومات جهة الاتصال/الجروب - بتفتح لما تدوس على اسم المحادثة فوق
            infoPanel: { open: false, loading: false, data: false },
            // قايمة "📎" اللي بتفتح على خيارات إرفاق مختلفة (مش بس ملف)
            attachMenu: { open: false },
            // اللوحات (panels) الخاصة بكل نوع رسالة واتساب جديد نقدر نبعته
            locationPanel: { open: false, latitude: "", longitude: "", name: "", address: "", geoLoading: false, sending: false },
            contactPanel: { open: false, name: "", phone: "", organization: "", sending: false },
            pollPanel: { open: false, question: "", options: ["", ""], multi: false, sending: false },
            // تعديل رسالة صادرة بعتناها احنا (شريط فوق الـ composer زي شريط الرد)
            editPanel: { open: false, messageId: false, text: "", sending: false },
        });

        this.fileInputRef = useRef("fileInput");
        this.stickerFileInputRef = useRef("stickerFileInput");
        this.draftInputRef = useRef("draftInput");
        this._statusTimer = null;
        this._convTimer = null;
        this._msgTimer = null;
        // "بيكتب الآن..." - آخر مرة بعتنا فيها composing، ومؤقّت الرجوع
        // لـ paused تلقائيًا لو المستخدم بطّل يكتب (زي واتساب بالظبط).
        this._lastTypingSent = 0;
        this._typingStopTimer = null;

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
            if (this.state.emojiPicker.open && !ev.target.closest(".o_wa_composer_emoji_panel, .o_wa_emoji_btn")) {
                this.state.emojiPicker.open = false;
            }
            if (this.state.attachMenu.open && !ev.target.closest(".o_wa_attach_menu, .o_wa_attach_btn")) {
                this.state.attachMenu.open = false;
            }
        };

        onWillStart(async () => {
            await this._checkStatus();
        });

        onMounted(() => {
            this._statusTimer = setInterval(() => this._checkStatus(), 4000);
            document.addEventListener("click", this._onDocumentClick, true);
            // إشعارات فعلية (Notification API) - لو المتصفح لسه محدد ولا
            // موافق ولا رافض، بنسأل مرة واحدة. لو رفض، هنعتمد بس على
            // التوست الداخلي جوه أودو (شوف _notifyIncomingMessage).
            if (window.Notification && Notification.permission === "default") {
                Notification.requestPermission().catch(() => {});
            }
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
            clearTimeout(this._typingStopTimer);
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
    // إشعارات popup فعلية على رسالة واردة جديدة - إشعار سطح مكتب حقيقي
    // (Notification API) لو التاب مش فاتح/مش في الفوكس، وإلا توست داخلي
    // جوه أودو (نفس نمط env.services.notification المستخدم في باقي
    // الشاشة) + صوت "تنبيه" قصير في الحالتين.
    // ------------------------------------------------------------------
    _playNotificationSound() {
        try {
            const Ctx = window.AudioContext || window.webkitAudioContext;
            if (!Ctx) return;
            const ctx = this._audioCtx || (this._audioCtx = new Ctx());
            const osc = ctx.createOscillator();
            const gain = ctx.createGain();
            osc.frequency.value = 880;
            gain.gain.setValueAtTime(0.15, ctx.currentTime);
            gain.gain.exponentialRampToValueAtTime(0.001, ctx.currentTime + 0.35);
            osc.connect(gain);
            gain.connect(ctx.destination);
            osc.start();
            osc.stop(ctx.currentTime + 0.35);
        } catch (e) {
            // بعض المتصفحات بتمنع تشغيل صوت من غير تفاعل مستخدم قبل كده -
            // مش حرج، الـ popup نفسه هيفضل يظهر عادي.
        }
    }

    _notifyIncomingMessage(contactName, phoneNumber, preview) {
        const title = contactName || phoneNumber;
        const body = preview || "📎 رسالة جديدة";
        this._playNotificationSound();
        if (document.hidden && window.Notification && Notification.permission === "granted") {
            try {
                const n = new Notification(title, { body, tag: "wa-" + phoneNumber });
                n.onclick = () => {
                    window.focus();
                    const conv = this.state.conversations.find((c) => c.phone_number === phoneNumber);
                    if (conv) this.selectConversation(conv);
                    n.close();
                };
                return;
            } catch (e) {
                // فشل إنشاء الإشعار (مثلاً صلاحية اتشالت فجأة) - نرجع للتوست الداخلي تحت.
            }
        }
        this.env.services.notification.add(`${title}: ${body}`, { type: "info" });
    }

    // بتقارن قايمة المحادثات القديمة بالجديدة وتطلع popup لأي محادثة
    // (غير المفتوحة دلوقتي) زاد عدد رسايلها غير المقروءة.
    _detectNewIncoming(oldList, newList) {
        if (!oldList.length) return; // أول تحميل للشاشة - متنبهش على كل حاجة قديمة موجودة أصلاً
        const oldMap = {};
        for (const c of oldList) oldMap[c.phone_number] = c;
        for (const c of newList) {
            const prev = oldMap[c.phone_number];
            const prevUnread = prev ? prev.unread_count : 0;
            if (c.unread_count > prevUnread && c.phone_number !== this.state.selectedPhone) {
                this._notifyIncomingMessage(c.contact_name, c.phone_number, c.last_message);
            }
        }
    }

    // ------------------------------------------------------------------
    async _loadConversations() {
        try {
            const conversations = await rpc("/whatsapp/fullview/conversations", {});
            this._detectNewIncoming(this.state.conversations, conversations);
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

    _escapeHtml(text) {
        const div = document.createElement("div");
        div.textContent = text;
        return div.innerHTML;
    }

    // بتحوّل أي لينك (http/https/www) لعنصر <a> قابل للدوس زي واتساب، وأي
    // "@<رقم>" جوه رسالة جروب متعرّف عليه في mentions لاسم الشخص الملوّن
    // (زي إشارة واتساب) - وبتسيب باقي النص عادي (متهرّب/escaped من غير أي
    // HTML حقيقي فيه عشان منفتحش باب لـ XSS). بترجع Markup عشان t-out
    // يعرضها كـ HTML حقيقي بدل ما يطبعها كنص خام.
    linkify(text, mentions) {
        if (!text) return "";
        mentions = mentions || {};
        // بنمرّ على النص مرة واحدة بس، ندوّر في كل مرة على أقرب حاجة من
        // النوعين (لينك أو @mention) عشان الترتيب يفضل صح.
        const combinedRegex = /(@(\d{5,15}))|((?:https?:\/\/|www\.)[^\s<>"']+)/g;
        let result = "";
        let lastIndex = 0;
        let match;
        while ((match = combinedRegex.exec(text)) !== null) {
            result += this._escapeHtml(text.slice(lastIndex, match.index));
            if (match[1]) {
                const phone = match[2];
                const name = mentions[phone];
                if (name) {
                    result += `<span class="o_wa_mention">@${this._escapeHtml(name)}</span>`;
                } else {
                    result += this._escapeHtml(match[0]);
                }
            } else {
                let url = match[3];
                // شيل أي علامات ترقيم في الآخر مش جزء من اللينك نفسه (زي
                // نقطة آخر الجملة أو قوس قفل).
                let trailing = "";
                const trailingMatch = url.match(/[.,!?;:)\]]+$/);
                if (trailingMatch) {
                    trailing = trailingMatch[0];
                    url = url.slice(0, url.length - trailing.length);
                }
                const href = /^https?:\/\//i.test(url) ? url : "https://" + url;
                const safeHref = this._escapeHtml(href);
                const safeText = this._escapeHtml(url);
                result += `<a href="${safeHref}" target="_blank" rel="noopener noreferrer" ` +
                    `class="o_wa_bubble_link" onclick="event.stopPropagation()">${safeText}</a>`;
                result += this._escapeHtml(trailing);
            }
            lastIndex = match.index + match[0].length;
        }
        result += this._escapeHtml(text.slice(lastIndex));
        return markup(result);
    }

    // بترجّع لون ثابت لاسم مرسل معيّن جوه الجروب (نفس الاسم = نفس اللون
    // دايمًا) زي ما واتساب بيلوّن أسماء أعضاء الجروب فوق كل رسالة.
    senderColor(name) {
        const palette = [
            "#d9739f", "#2a9d8f", "#e07a5f", "#3d5a80", "#8338ec",
            "#e63946", "#5f9e4d", "#c9184a", "#457b9d", "#b5657f",
        ];
        if (!name) return palette[0];
        let hash = 0;
        for (let i = 0; i < name.length; i++) hash = (hash * 31 + name.charCodeAt(i)) >>> 0;
        return palette[hash % palette.length];
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

    // علامة الصح جنب رسايلنا احنا (زي واتساب: صح واحدة = اتبعتت، صح
    // اتنين رمادي = وصلت، صح اتنين زرقا = اتقرت). in_progress/failed
    // بياخدوا شكل مبدئي بدل ما نسيب المكان فاضي.
    statusTickIcon(state) {
        if (state === 'read' || state === 'delivered') return "✓✓";
        if (state === 'failed') return "⚠";
        return "✓"; // sent / in_progress
    }

    statusLabel(state) {
        const labels = {
            in_progress: "جاري الإرسال",
            sent: "اتبعتت",
            delivered: "وصلت",
            read: "اتقرت",
            failed: "فشل الإرسال",
        };
        return labels[state] || "";
    }

    // النص اللي بيبان تحت اسم جهة الاتصال في هيدر الشات - "بيكتب
    // دلوقتي..." أو "بيسجل رسالة صوتية..." أو "آخر ظهور HH:MM". لو مفيش
    // presence حالية أو محادثة جروب، بيرجع نص فاضي (مفيش سطر يتعرض خالص).
    get presenceLabel() {
        const p = this.state.presence;
        if (!p) return "";
        if (p.state === "composing") return "بيكتب الآن...";
        if (p.state === "recording") return "بيسجل رسالة صوتية...";
        if (p.state === "available") return "أونلاين";
        if (p.last_seen) {
            const d = new Date(p.last_seen.replace(" ", "T") + "Z");
            return "آخر ظهور " + d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
        }
        return "";
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
        // آخر مين ظهر (اسم مرسل، أو "__me__" لرسايلنا احنا) - بيتصفّر مع
        // كل فاصل يوم جديد. بيتستخدم عشان في الجروبات، اسم وصورة المرسل
        // يظهروا مرة واحدة بس فوق أول رسالة في كل "مجموعة" رسايل متتالية
        // من نفس الشخص، زي واتساب بالظبط - مش فوق كل رسالة لوحدها.
        let lastSenderKey = null;
        for (const msg of this.state.messages) {
            const d = new Date((msg.date || "").replace(" ", "T") + "Z");
            const dayKey = d.toDateString();
            if (dayKey !== lastDay) {
                out.push({ is_day_separator: true, id: "sep-" + dayKey, label: this.dayLabel(msg.date) });
                lastDay = dayKey;
                lastSenderKey = null;
            }
            const senderKey = msg.from_me ? "__me__" : (msg.sender_name || "__unknown__");
            msg.show_sender = this.state.selectedIsGroup && !msg.from_me && senderKey !== lastSenderKey;
            lastSenderKey = senderKey;
            out.push(msg);
        }
        return out;
    }

    // ------------------------------------------------------------------
    async selectConversation(conv) {
        this.state.selectedPhone = conv.phone_number;
        this.state.selectedName = conv.contact_name;
        this.state.selectedIsGroup = !!conv.is_group;
        this.state.messages = [];
        this.state.noMoreOlder = false;
        this.state.replyTo = false;
        this.state.presence = { state: false, last_seen: false };
        this.exitSelectMode();
        this.closeMsgSearch();
        this.closeGallery();
        this.closeInfoPanel();
        this.state.emojiPicker.open = false;
        this.closeActionMenu();
        this.closeForwardPanel();
        this.state.attachMenu.open = false;
        this.closeLocationPanel();
        this.closeContactPanel();
        this.closePollPanel();
        this.cancelEditMessage();
        clearTimeout(this._typingStopTimer);
        this._lastTypingSent = 0;
        clearInterval(this._msgTimer);
        await this._loadMessages(true);
        try {
            await rpc("/whatsapp/fullview/mark_read", { phone_number: conv.phone_number });
        } catch (e) {
            console.warn("WhatsApp fullview: failed to mark conversation as read", e);
        }
        conv.unread_count = 0;
        this._loadPresence();
        this._msgTimer = setInterval(() => {
            this._loadMessages(false);
            this._loadPresence();
        }, 3000);
    }

    // "بيكتب دلوقتي..." / "أونلاين" / "آخر ظهور" - بتتسأل مع كل poll
    // للرسايل، بس للمحادثة الفردية المفتوحة (الجروبات مالهاش presence
    // واحدة موحّدة، فبنسيبها من غيرها).
    async _loadPresence() {
        if (!this.state.selectedPhone || this.state.selectedIsGroup) return;
        try {
            this.state.presence = await rpc("/whatsapp/fullview/presence", {
                phone_number: this.state.selectedPhone,
            });
        } catch (e) {
            // مش حرج زي فشل تحميل الرسايل - نسيب الحالة القديمة زي ما هي.
        }
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
                // المحادثة دي مفتوحة فعليًا قدامك - لو التاب مش في الفوكس
                // (مثلاً فاتح شاشة تانية في أودو) نطلع popup برضو على أي
                // رسالة واردة جديدة، عشان مايفوتكش حاجة.
                if (document.hidden) {
                    const incoming = messages.filter((m) => !m.from_me);
                    if (incoming.length) {
                        const last = incoming[incoming.length - 1];
                        this._notifyIncomingMessage(
                            this.state.selectedName, this.state.selectedPhone,
                            last.body || (last.has_media ? "📎 وسائط" : "")
                        );
                    }
                }
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
    // قايمة "📎" - بتفتح على خيارات إرفاق مختلفة (ملف عادي، ستيكر، موقع،
    // جهة اتصال، استطلاع رأي) بدل ما "📎" تفتح نافذة اختيار ملف على طول.
    // ------------------------------------------------------------------
    toggleAttachMenu(ev) {
        if (ev) ev.stopPropagation();
        this.state.attachMenu.open = !this.state.attachMenu.open;
    }

    closeAttachMenu() {
        this.state.attachMenu.open = false;
    }

    onAttachMediaClick() {
        this.closeAttachMenu();
        this.onAttachClick();
    }

    onAttachStickerClick() {
        this.closeAttachMenu();
        if (this.stickerFileInputRef.el) {
            this.stickerFileInputRef.el.click();
        }
    }

    // الستيكر بيتبعت على طول لما تختار الصورة (مفيش composer preview
    // زي المرفقات العادية) - نفس نمط send_media بس ملف مباشر بـ fetch.
    async onStickerFileChange(ev) {
        const file = ev.target.files[0];
        ev.target.value = "";
        if (!file || !this.state.selectedPhone) return;
        try {
            const formData = new FormData();
            formData.append("phone_number", this.state.selectedPhone);
            formData.append("file", file);
            const response = await fetch("/whatsapp/fullview/send_sticker", {
                method: "POST", body: formData,
            });
            const data = await response.json();
            if (!data.ok) {
                throw new Error(data.error || "send_failed");
            }
            this.state.messages.push(data.message);
            this._scrollToEnd();
        } catch (e) {
            this.env.services.notification.add(
                e.message || "تعذر إرسال الستيكر", { type: "danger" }
            );
        }
    }

    // ------------------------------------------------------------------
    // إرسال موقع (📍) - إما موقعك الحالي (Geolocation API) أو إحداثيات
    // بتكتبها يدوي.
    // ------------------------------------------------------------------
    openLocationPanel() {
        this.closeAttachMenu();
        this.state.locationPanel = {
            open: true, latitude: "", longitude: "", name: "", address: "",
            geoLoading: false, sending: false,
        };
    }

    closeLocationPanel() {
        this.state.locationPanel.open = false;
    }

    useCurrentLocation() {
        if (!navigator.geolocation) {
            this.env.services.notification.add(
                "المتصفح ده مش بيدعم تحديد الموقع", { type: "danger" }
            );
            return;
        }
        this.state.locationPanel.geoLoading = true;
        navigator.geolocation.getCurrentPosition(
            (pos) => {
                this.state.locationPanel.latitude = String(pos.coords.latitude);
                this.state.locationPanel.longitude = String(pos.coords.longitude);
                this.state.locationPanel.geoLoading = false;
            },
            () => {
                this.state.locationPanel.geoLoading = false;
                this.env.services.notification.add(
                    "مش قادر أوصل لموقعك - تأكد إنك سمحت للمتصفح بالوصول له", { type: "danger" }
                );
            },
            { enableHighAccuracy: true, timeout: 10000 }
        );
    }

    async sendLocationPanel(ev) {
        if (ev) ev.preventDefault();
        const p = this.state.locationPanel;
        const lat = parseFloat(p.latitude);
        const lng = parseFloat(p.longitude);
        if (!this.state.selectedPhone || isNaN(lat) || isNaN(lng) || p.sending) return;
        p.sending = true;
        try {
            const msg = await rpc("/whatsapp/fullview/send_location", {
                phone_number: this.state.selectedPhone, latitude: lat, longitude: lng,
                name: (p.name || "").trim(), address: (p.address || "").trim(),
            });
            this.state.messages.push(msg);
            this._scrollToEnd();
            this.closeLocationPanel();
        } catch (e) {
            this.env.services.notification.add(
                e.message || "تعذر إرسال الموقع", { type: "danger" }
            );
        } finally {
            p.sending = false;
        }
    }

    // ------------------------------------------------------------------
    // إرسال جهة اتصال (👤) كـ vCard
    // ------------------------------------------------------------------
    openContactPanel() {
        this.closeAttachMenu();
        this.state.contactPanel = { open: true, name: "", phone: "", organization: "", sending: false };
    }

    closeContactPanel() {
        this.state.contactPanel.open = false;
    }

    async sendContactPanel(ev) {
        if (ev) ev.preventDefault();
        const p = this.state.contactPanel;
        const name = (p.name || "").trim();
        const phone = (p.phone || "").trim();
        if (!this.state.selectedPhone || !name || !phone || p.sending) return;
        p.sending = true;
        try {
            const msg = await rpc("/whatsapp/fullview/send_contact", {
                phone_number: this.state.selectedPhone, contact_name: name, contact_phone: phone,
                organization: (p.organization || "").trim(),
            });
            this.state.messages.push(msg);
            this._scrollToEnd();
            this.closeContactPanel();
        } catch (e) {
            this.env.services.notification.add(
                e.message || "تعذر إرسال جهة الاتصال", { type: "danger" }
            );
        } finally {
            p.sending = false;
        }
    }

    // ------------------------------------------------------------------
    // إنشاء استطلاع رأي (📊)
    // ------------------------------------------------------------------
    openPollPanel() {
        this.closeAttachMenu();
        this.state.pollPanel = { open: true, question: "", options: ["", ""], multi: false, sending: false };
    }

    closePollPanel() {
        this.state.pollPanel.open = false;
    }

    addPollOption() {
        if (this.state.pollPanel.options.length >= 12) return;
        this.state.pollPanel.options.push("");
    }

    removePollOption(index) {
        if (this.state.pollPanel.options.length <= 2) return;
        this.state.pollPanel.options.splice(index, 1);
    }

    onPollOptionInput(index, ev) {
        this.state.pollPanel.options[index] = ev.target.value;
    }

    async sendPollPanel(ev) {
        if (ev) ev.preventDefault();
        const p = this.state.pollPanel;
        const question = (p.question || "").trim();
        const options = p.options.map((o) => (o || "").trim()).filter(Boolean);
        if (!this.state.selectedPhone || !question || options.length < 2 || p.sending) return;
        p.sending = true;
        try {
            const msg = await rpc("/whatsapp/fullview/send_poll", {
                phone_number: this.state.selectedPhone, question, options,
                selectable_count: p.multi ? options.length : 1,
            });
            this.state.messages.push(msg);
            this._scrollToEnd();
            this.closePollPanel();
        } catch (e) {
            this.env.services.notification.add(
                e.message || "تعذر إرسال الاستطلاع", { type: "danger" }
            );
        } finally {
            p.sending = false;
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
        const replyToId = this.state.replyTo ? this.state.replyTo.id : false;
        try {
            let msg;
            if (this.state.attachment) {
                const formData = new FormData();
                formData.append("phone_number", this.state.selectedPhone);
                formData.append("caption", body);
                formData.append("file", this.state.attachment);
                if (replyToId) formData.append("reply_to_message_id", replyToId);
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
                    reply_to_message_id: replyToId,
                });
            }
            this.state.messages.push(msg);
            this.state.draft = "";
            this.state.replyTo = false;
            this._scrollToEnd();
            // اتبعتت الرسالة فعليًا - وقّف "بيكتب الآن..." فورًا من غير
            // ما ننتظر الـ ٤ ثواني بتاعة _notifyTyping().
            clearTimeout(this._typingStopTimer);
            if (this._lastTypingSent) {
                this._lastTypingSent = 0;
                rpc("/whatsapp/fullview/typing", {
                    phone_number: this.state.selectedPhone, state: "paused",
                }).catch(() => {});
            }
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
            return;
        }
        this._notifyTyping();
    }

    // "بيكتب الآن..." عند الطرف التاني - بنبعتها مرة كل ٣ ثواني بالكتير
    // (مش مع كل ضغطة كيبورد) عشان منضربش الـ API، وبعد ٤ ثواني من غير
    // كتابة بنبعت "paused" تلقائيًا (زي واتساب لما توقف تكتب لحظة).
    _notifyTyping() {
        if (!this.state.selectedPhone) return;
        const now = Date.now();
        if (!this._lastTypingSent || now - this._lastTypingSent > 3000) {
            this._lastTypingSent = now;
            rpc("/whatsapp/fullview/typing", {
                phone_number: this.state.selectedPhone, state: "composing",
            }).catch(() => {});
        }
        clearTimeout(this._typingStopTimer);
        this._typingStopTimer = setTimeout(() => {
            this._lastTypingSent = 0;
            rpc("/whatsapp/fullview/typing", {
                phone_number: this.state.selectedPhone, state: "paused",
            }).catch(() => {});
        }, 4000);
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
        if (this.state.selectMode) {
            this.toggleSelect(msg);
            return;
        }
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

    // بتحذف الرسالة "لدى الجميع" فعليًا (زي واتساب) - عكس deleteLocally
    // اللي بس بتشيلها عندك انت. متاحة بس لرسايلنا احنا اللي لسه مش
    // متحذوفة أصلاً.
    async deleteForEveryone() {
        const msg = this.actionMenuMessage;
        this.closeActionMenu();
        if (!msg) return;
        try {
            const updated = await rpc("/whatsapp/fullview/delete_everyone", { message_id: msg.id });
            const idx = this.state.messages.findIndex((m) => m.id === updated.id);
            if (idx !== -1) this.state.messages[idx] = updated;
        } catch (e) {
            this.env.services.notification.add(
                e.message || "تعذر حذف الرسالة لدى الجميع", { type: "danger" }
            );
        }
    }

    // ------------------------------------------------------------------
    // تعديل رسالة صادرة بعتناها احنا - شريط فوق الـ composer زي شريط
    // الرد بالظبط، بس بمربع نص قابل للتعديل بدل معاينة بس.
    // ------------------------------------------------------------------
    startEditMessage() {
        const msg = this.actionMenuMessage;
        if (!msg || !msg.from_me || !msg.body) return;
        this.closeActionMenu();
        this.cancelReply();
        this.state.editPanel = { open: true, messageId: msg.id, text: msg.body, sending: false };
    }

    cancelEditMessage() {
        this.state.editPanel = { open: false, messageId: false, text: "", sending: false };
    }

    async saveEditMessage(ev) {
        if (ev) ev.preventDefault();
        const p = this.state.editPanel;
        const text = (p.text || "").trim();
        if (!p.messageId || !text || p.sending) return;
        p.sending = true;
        try {
            const updated = await rpc("/whatsapp/fullview/edit_message", {
                message_id: p.messageId, new_text: text,
            });
            const idx = this.state.messages.findIndex((m) => m.id === updated.id);
            if (idx !== -1) this.state.messages[idx] = updated;
            this.cancelEditMessage();
        } catch (e) {
            this.env.services.notification.add(
                e.message || "تعذر تعديل الرسالة", { type: "danger" }
            );
            p.sending = false;
        }
    }

    // ------------------------------------------------------------------
    // تحديد كذا رسالة مع بعض (Multi-select)
    // ------------------------------------------------------------------
    _selectedIdsArray() {
        return Object.keys(this.state.selectedIds)
            .filter((id) => this.state.selectedIds[id])
            .map(Number);
    }

    get selectedCount() {
        return this._selectedIdsArray().length;
    }

    enterSelectMode() {
        const msg = this.actionMenuMessage;
        this.closeActionMenu();
        this.state.selectMode = true;
        this.state.selectedIds = msg ? { [msg.id]: true } : {};
    }

    exitSelectMode() {
        this.state.selectMode = false;
        this.state.selectedIds = {};
    }

    toggleSelect(msg) {
        const next = { ...this.state.selectedIds };
        if (next[msg.id]) delete next[msg.id];
        else next[msg.id] = true;
        this.state.selectedIds = next;
    }

    async deleteSelected() {
        const ids = this._selectedIdsArray();
        if (!ids.length) return;
        try {
            for (const id of ids) {
                await rpc("/whatsapp/fullview/delete_local", { message_id: id });
            }
            this.state.messages = this.state.messages.filter((m) => !ids.includes(m.id));
            this.exitSelectMode();
        } catch (e) {
            this.env.services.notification.add(
                e.message || "تعذر حذف الرسايل", { type: "danger" }
            );
        }
    }

    // ------------------------------------------------------------------
    // البحث جوه المحادثة المفتوحة
    // ------------------------------------------------------------------
    openMsgSearch() {
        this.state.msgSearch = { open: true, query: "", results: [], loading: false };
    }

    closeMsgSearch() {
        this.state.msgSearch = { open: false, query: "", results: [], loading: false };
    }

    async onMsgSearchInput(ev) {
        const query = ev.target.value;
        this.state.msgSearch.query = query;
        clearTimeout(this._searchDebounce);
        if (!query || query.trim().length < 2) {
            this.state.msgSearch.results = [];
            return;
        }
        this._searchDebounce = setTimeout(async () => {
            this.state.msgSearch.loading = true;
            try {
                const results = await rpc("/whatsapp/fullview/search", {
                    phone_number: this.state.selectedPhone,
                    query: query.trim(),
                });
                this.state.msgSearch.results = results;
            } catch (e) {
                this.state.msgSearch.results = [];
            } finally {
                this.state.msgSearch.loading = false;
            }
        }, 350);
    }

    async onSearchResultClick(messageId) {
        this.closeMsgSearch();
        await this.jumpToMessage(messageId);
    }

    // بتحمّل نافذة رسايل حوالين رسالة معيّنة (من نتيجة بحث، أو من مربع
    // رد لرسالة مش محمّلة في الشاشة حاليًا) وتعمل scroll ليها.
    async jumpToMessage(messageId) {
        if (!this.state.selectedPhone || !messageId) return;
        try {
            const msgs = await rpc("/whatsapp/fullview/messages", {
                phone_number: this.state.selectedPhone,
                around_id: messageId,
            });
            if (!msgs.length) return;
            this.state.messages = msgs;
            clearInterval(this._msgTimer);
            this._msgTimer = setInterval(() => {
                this._loadMessages(false);
                this._loadPresence();
            }, 3000);
            requestAnimationFrame(() => {
                const el = this.messagesRef.el &&
                    this.messagesRef.el.querySelector(`[data-msg-id="${messageId}"]`);
                if (!el) return;
                el.scrollIntoView({ behavior: "auto", block: "center" });
                el.classList.add("o_wa_bubble_row_highlight");
                setTimeout(() => el.classList.remove("o_wa_bubble_row_highlight"), 1500);
            });
        } catch (e) {
            // لو فشل، نسيب الشاشة زي ما هي - مش حرج بما يكفي إننا نوقف اليوزر.
        }
    }

    // ------------------------------------------------------------------
    // معرض الصور/الفيديوهات المشتركة
    // ------------------------------------------------------------------
    async openGallery() {
        this.state.gallery = { open: true, items: [], loading: true };
        try {
            const items = await rpc("/whatsapp/fullview/media_gallery", {
                phone_number: this.state.selectedPhone,
            });
            this.state.gallery.items = items;
        } catch (e) {
            this.state.gallery.items = [];
        } finally {
            this.state.gallery.loading = false;
        }
    }

    closeGallery() {
        this.state.gallery = { open: false, items: [], loading: false };
    }

    async onGalleryItemClick(item) {
        this.closeGallery();
        await this.jumpToMessage(item.id);
    }

    // ------------------------------------------------------------------
    // إيموجي بيكر لمربع الكتابة - مختلف عن الـ reactions (اللي بتتفاعل
    // بيها على رسالة موجودة)، ده بيدخل الإيموجي جوه النص اللي بتكتبه.
    // ------------------------------------------------------------------
    get composerEmojis() {
        return QUICK_REACTIONS.concat(EXTRA_REACTIONS);
    }

    toggleEmojiPicker(ev) {
        if (ev) ev.stopPropagation();
        this.state.emojiPicker.open = !this.state.emojiPicker.open;
    }

    insertEmoji(emoji) {
        const el = this.draftInputRef.el;
        const value = this.state.draft || "";
        if (el && typeof el.selectionStart === "number") {
            const start = el.selectionStart;
            const end = el.selectionEnd;
            this.state.draft = value.slice(0, start) + emoji + value.slice(end);
            const pos = start + emoji.length;
            requestAnimationFrame(() => {
                el.focus();
                el.setSelectionRange(pos, pos);
            });
        } else {
            this.state.draft = value + emoji;
        }
    }

    // ------------------------------------------------------------------
    // صفحة معلومات جهة الاتصال/الجروب - بتفتح لما تدوس على اسم/صورة
    // المحادثة فوق. للجروب بتوري الأعضاء (لو Evolution بيدعم جلبهم).
    // ------------------------------------------------------------------
    async openInfoPanel() {
        if (!this.state.selectedPhone) return;
        this.state.infoPanel = { open: true, loading: true, data: false };
        try {
            const data = await rpc("/whatsapp/fullview/info", {
                phone_number: this.state.selectedPhone,
            });
            this.state.infoPanel.data = data;
        } catch (e) {
            this.state.infoPanel.data = false;
        } finally {
            this.state.infoPanel.loading = false;
        }
    }

    closeInfoPanel() {
        this.state.infoPanel = { open: false, loading: false, data: false };
    }

    openGalleryFromInfo() {
        this.closeInfoPanel();
        this.openGallery();
    }

    // ------------------------------------------------------------------
    // رد (Reply): بتحط الرسالة المختارة في شريط "بترد على..." فوق مربع
    // الكتابة - أول رسالة تتبعت بعد كده بتترسل كرد عليها فعليًا (زي
    // بالظبط لو كنت رديت من التليفون نفسه).
    // ------------------------------------------------------------------
    startReply() {
        const msg = this.actionMenuMessage;
        if (!msg) return;
        this.closeActionMenu();
        let senderName;
        if (msg.from_me) {
            senderName = "أنت";
        } else {
            senderName = msg.sender_name || this.state.selectedName || "";
        }
        let preview = msg.body || "";
        if (preview.length > 120) preview = preview.slice(0, 117) + "...";
        if (!preview && msg.has_media) preview = "📎 وسائط";
        this.state.replyTo = { id: msg.id, sender_name: senderName, preview: preview };
    }

    cancelReply() {
        this.state.replyTo = false;
    }

    // بتودّيك للرسالة الأصلية لما تدوس على مربع الرد فوق أي رسالة -
    // شغالة بس لو الرسالة الأصلية محمّلة أصلاً في نفس الشاشة (مش لو كانت
    // قديمة جدًا وبرّه الصفحة الحالية المحمّلة).
    jumpToQuoted(ev, localId) {
        if (ev) ev.stopPropagation();
        if (!localId) return;
        const el = this.messagesRef.el && this.messagesRef.el.querySelector(`[data-msg-id="${localId}"]`);
        if (el) {
            el.scrollIntoView({ behavior: "smooth", block: "center" });
            el.classList.add("o_wa_bubble_row_highlight");
            setTimeout(() => el.classList.remove("o_wa_bubble_row_highlight"), 1500);
            return;
        }
        // مش محمّلة في الشاشة دلوقتي (مثلاً قديمة جدًا) - نجيبها بنافذة
        // جديدة حواليها زي ما بنعمل مع نتيجة بحث بالظبط.
        this.jumpToMessage(localId);
    }

    // ------------------------------------------------------------------
    // فورورد: بيظهر بانل فيه المحادثات الحالية + خانة رقم يدوي
    // ------------------------------------------------------------------
    openForwardPanel() {
        const msg = this.actionMenuMessage;
        if (!msg) return;
        this.closeActionMenu();
        this.state.forwardPanel = { open: true, messageIds: [msg.id], phone: "" };
    }

    openForwardPanelForSelection() {
        const ids = this._selectedIdsArray();
        if (!ids.length) return;
        this.state.forwardPanel = { open: true, messageIds: ids, phone: "" };
    }

    closeForwardPanel() {
        this.state.forwardPanel = { open: false, messageIds: [], phone: "" };
    }

    get forwardableConversations() {
        return this.state.conversations.filter((c) => c.phone_number !== this.state.selectedPhone);
    }

    async forwardTo(phoneNumber) {
        const ids = this.state.forwardPanel.messageIds || [];
        phoneNumber = (phoneNumber || "").trim();
        if (!ids.length || !phoneNumber) return;
        this.closeForwardPanel();
        try {
            for (const id of ids) {
                await rpc("/whatsapp/fullview/forward", { message_id: id, to_phone_number: phoneNumber });
            }
            this.env.services.notification.add(
                ids.length > 1 ? `اتبعت ${ids.length} رسايل` : "اتبعتت", { type: "success" }
            );
            if (this.state.selectMode) this.exitSelectMode();
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