from odoo import fields, models, _, api, tools
import base64
import requests
import json
import uuid
from datetime import datetime, timezone
from urllib.parse import quote
from odoo.exceptions import ValidationError
import logging

_logger = logging.getLogger(__name__)


class WaWebHookMessages(models.Model):
    _name = 'wa.webhook.messages'
    _order = "create_date DESC"

    json_content = fields.Char()

    @api.depends('json_content')
    def message_process(self):
        for record in self:
            if not record.json_content:
                record.trigger_message_process = False if record.trigger_message_process else True
                continue
            payload = json.loads(record.json_content)
            event = (payload.get('event') or '').lower().replace('_', '.')

            if event == 'messages.upsert':
                data = payload.get('data') or {}
                # Evolution can send either a single message dict, or
                # {"messages": [...]} depending on version/config.
                items = data.get('messages') if isinstance(data.get('messages'), list) else [data]
                for item in items:
                    if not item or not item.get('key'):
                        continue
                    message_id = item['key'].get('id')
                    from_me = bool(item['key'].get('fromMe'))
                    # fromMe=True covers two very different situations that
                    # we must NOT treat the same way:
                    #  1) The echo Evolution sends back for a message WE
                    #     just sent through Odoo (send_message /
                    #     send_message_media) - Odoo already created a
                    #     wa.message with this exact dialog_message_id the
                    #     moment the API call returned, so this webhook
                    #     event is pure noise and must be skipped or we'd
                    #     duplicate the bubble.
                    #  2) A genuine message (or emoji reaction) sent from
                    #     ANY other device linked to this WhatsApp number -
                    #     most commonly the actual phone, exactly like
                    #     WhatsApp Web multi-device sync - which Odoo has
                    #     never seen before. Skipping this unconditionally
                    #     (the old behaviour) is why replies/reactions typed
                    #     directly on the phone never showed up in Odoo.
                    # We tell the two apart by whether we already have a
                    # local wa.message for this wamid - not by fromMe alone.
                    if message_id and self.env['wa.message'].sudo().search_count(
                            [('dialog_message_id', '=', message_id)]):
                        continue
                    # This must never let an exception escape: message_process
                    # runs as a compute triggered by the create() of THIS
                    # wa.webhook.messages record (see whatsapp_webhook_controller
                    # ._store_webhook_payload), all inside one transaction. If
                    # anything downstream (channel/partner creation, group name
                    # lookup, auto-reply send, ...) raises, the whole create()
                    # fails, the controller catches it and returns a 500, and
                    # Odoo rolls back the transaction - silently discarding the
                    # incoming WhatsApp message we were trying to save along
                    # with it (this is a primary cause of "messages not being
                    # received"). One bad item must also not stop the rest of
                    # a batched payload from being processed.
                    try:
                        self.env['wa.message'].sudo()._process_incoming_message(item, record, from_me=from_me)
                    except Exception:
                        _logger.exception(
                            "Failed to process incoming WhatsApp message (wamid=%s) - "
                            "webhook payload id=%s", message_id, record.id)

            elif event == 'messages.update':
                data = payload.get('data') or {}
                items = data if isinstance(data, list) else [data]
                for item in items:
                    key = item.get('key') or {}
                    message_id = key.get('id') or item.get('keyId')
                    status_value = self._map_evo_status(item.get('status') or item.get('update', {}).get('status'))
                    if message_id and status_value:
                        wa_message = self.env['wa.message'].sudo().search(
                            [('dialog_message_id', '=', message_id)])
                        if wa_message:
                            wa_message[0].status = status_value
                            wa_message[0].webhook_message_ids = [(4, record.id)]

            elif event == 'presence.update':
                # "بيكتب الآن..." / "آخر ظهور" - شكل الـ payload مش موحّد
                # بين نسخ Evolution، فبندور على القيمة في كل الاحتمالات
                # المعروفة قبل ما نسيبها.
                data = payload.get('data') or {}
                remote_jid = data.get('id') or data.get('remoteJid') or ''
                presences = data.get('presences') or {}
                presence_state = False
                if presences:
                    first = next(iter(presences.values()), {}) or {}
                    presence_state = first.get('lastKnownPresence') or first.get('presence')
                else:
                    presence_state = data.get('presence') or data.get('lastKnownPresence')
                phone = self.normalize_phone(remote_jid) if remote_jid else False
                if phone and presence_state:
                    self.sudo()._update_wa_presence(phone, str(presence_state).lower())
                else:
                    _logger.info("Unhandled/unparsed WhatsApp presence.update payload=%s", data)

            else:
                # Unhandled event type (connection.update, qrcode.updated, ...) - ignore.
                if self.env.context.get('wa_developer_mode'):
                    _logger.info("Unhandled WhatsApp webhook event: %s", event or payload)

            record.trigger_message_process = False if record.trigger_message_process else True
    trigger_message_process = fields.Boolean(compute=message_process, store=True)

    @api.model
    def _map_evo_status(self, evo_status):
        """Evolution/Baileys status strings -> our wa.message.status selection."""
        if not evo_status:
            return False
        mapping = {
            'PENDING': 'in_progress',
            'SERVER_ACK': 'sent',
            'DELIVERY_ACK': 'delivered',
            'READ': 'read',
            'PLAYED': 'read',
            'ERROR': 'failed',
        }
        return mapping.get(str(evo_status).upper())


class WaMessageModelAdaptation(models.Model):
    _name = "wa.message.model.adaptation"
    _order = "create_date"

    model_id = fields.Many2one('ir.model')
    model_name = fields.Char(related='model_id.model')
    activity_user_field_id = fields.Many2one('ir.model.fields')
    activity_default_user_id = fields.Many2one('res.users')
    phone_field_ids = fields.Many2many('ir.model.fields')
    operator_ids = fields.Many2many(
        'res.users', string="WhatsApp Operators",
        help="Users added to the Discuss group chat for WhatsApp conversations linked to this model. "
             "If empty, the default operators configured on the WhatsApp Account are used instead.")

    def get_phone_number(self, res_id=False):
        rec = self.env[self.model_name].browse(res_id)
        if not rec:
            raise ValidationError(_('Record %s not found on %s') % (res_id, self.model_name))
        phone = False
        for phone_field in self.phone_field_ids.filtered(lambda x: x.relation == 'res.partner'):
            partner = rec[phone_field.name]
            if partner:
                # 'mobile' may not exist as a field on res.partner in every
                # install/edition - check before reading it.
                if 'mobile' in partner._fields and partner.mobile:
                    phone = partner.mobile
                    break
                elif partner.phone:
                    phone = partner.phone
                    break
        if not phone:
            for phone_field in self.phone_field_ids.filtered(lambda x: x.relation != 'res.partner'):
                field = rec[phone_field.name]
                if field:
                    phone = field
                    break
        if not phone:
            raise ValidationError(_(f'Not phone number found for id {res_id} - {self.model_name}'))
        else:
            return phone


class WaMessageQueue(models.Model):
    _name = "wa.message"
    _order = "create_date DESC"

    message_content = fields.Text()
    res_id = fields.Char()
    res_model = fields.Char()
    status_code = fields.Char()
    mail_message_id = fields.Many2one('mail.message')
    status = fields.Selection(selection=[
        ('in_progress', 'In Progress'),
        ('sent', 'Sent'),
        ('delivered', 'Delivered'),
        ('read', 'Read'),
        ('failed', 'Failed'),
    ])
    dialog_message_id = fields.Char(string="WhatsApp Message ID (wamid)")
    json_response = fields.Char()
    json_payload = fields.Char()
    wa_message_template_id = fields.Many2one('wa.message.template')
    company_id = fields.Many2one('res.company')
    account_id = fields.Many2one('whatsapp.account', string="Account")
    webhook_message_ids = fields.Many2many('wa.webhook.messages')
    direction = fields.Selection(selection=[
        ('outbound', 'Outbound'),
        ('inbound', 'Inbound'),
    ], default='outbound', required=True)
    phone_number = fields.Char()
    conversation_token = fields.Char(index=True, help="Random per-conversation token used by the website widget instead of the raw phone number, so visitors can't read each other's conversations by guessing numbers.")
    media_data = fields.Binary(string="Media",
                                help="Image/video/document attached to this message (website-widget conversations only) so it can be rendered in the chat widget.")
    media_filename = fields.Char(string="Media Filename")
    media_mimetype = fields.Char(string="Media Mimetype")
    discuss_channel_id = fields.Many2one('discuss.channel', string="WhatsApp Conversation", index=True,
                                          help="Discuss group chat that mirrors this WhatsApp conversation, so operators can read/reply from Discuss or the chat bubble.")

    # ------------------------------------------------------------------
    # Discuss bridge: every inbound/outbound WhatsApp message is mirrored
    # into a discuss.channel (group chat) keyed by phone number, so it shows
    # up in the chatter's messaging icon, the chat bubble and the Discuss
    # app - not just in the technical WhatsApp Messages list.
    # ------------------------------------------------------------------
    def _get_or_create_wa_partner(self, phone_number):
        Partner = self.env['res.partner'].sudo()
        # 'mobile' isn't guaranteed to exist as a field on res.partner in
        # every install/edition - check the registry before referencing it
        # instead of assuming, to avoid "Invalid field res.partner.mobile".
        has_mobile = 'mobile' in Partner._fields
        domain = [('phone', '=', phone_number)]
        if has_mobile:
            domain = ['|', ('mobile', '=', phone_number)] + domain
        partner = Partner.search(domain, limit=1)
        display_name = self._wa_display_name_from_context(phone_number)
        if not partner:
            vals = {'name': display_name or phone_number, 'phone': phone_number}
            if has_mobile:
                vals['mobile'] = phone_number
            partner = Partner.create(vals)
        elif display_name and partner.name == phone_number:
            # Partner was created before we had a real name for it (e.g. a
            # group whose subject wasn't fetched yet, or an individual whose
            # WhatsApp pushName wasn't known at the time) - update it now
            # that we have one, but never touch a name someone actually
            # edited/typed in Odoo since.
            partner.name = display_name
        return partner

    def _wa_display_name_from_context(self, phone_number):
        """Resolves a real display name for the current inbound message
        being processed, using wa_remote_jid/wa_push_name set on the
        context by _process_incoming_message - group subject (fetched from
        Evolution API) for a WhatsApp group, or the sender's WhatsApp
        pushName for an individual contact. Returns False if nothing better
        than the bare phone number/JID is available."""
        remote_jid = self.env.context.get('wa_remote_jid')
        if remote_jid and '@g.us' in remote_jid:
            return self._fetch_wa_group_name(remote_jid)
        return self.env.context.get('wa_push_name') or False

    def _fetch_wa_group_name(self, remote_jid):
        """GET /group/fetchAllGroups/{instance}?getParticipants=false ->
        list of this instance's groups with their subject (display name),
        so group chats show up as e.g. 'Marketing Team' instead of the raw
        numeric group JID. Fetches the whole list rather than a single
        group (some Evolution versions don't expose a single-group lookup,
        but all versions expose the manager's own group list this way) and
        looks up remote_jid in it. Best-effort: any failure (network,
        unsupported Evolution version, ...) just falls back to the raw id,
        it must never break message processing."""
        try:
            account = self.get_account()
        except ValidationError:
            return False
        if not account.server_url or not account.instance_name or not account.api_key:
            return False
        try:
            url = account._evo_instance_url("group/fetchAllGroups")
            response = requests.get(
                url, headers=account._evo_headers(), params={'getParticipants': 'false'}, timeout=15)
            data = response.json() if response.content else []
        except (requests.RequestException, ValueError):
            return False
        if account.developer_mode:
            _logger.info("WhatsApp group/fetchAllGroups status=%s response=%s",
                         response.status_code, data)
        if response.status_code != 200 or not isinstance(data, list):
            return False
        for group in data:
            jid = group.get('id') or group.get('remoteJid') or group.get('jid')
            if jid == remote_jid:
                return group.get('subject') or group.get('name') or False
        return False

    def _fetch_wa_group_info(self, remote_jid):
        """زي _fetch_wa_group_name بالظبط بس getParticipants=true - بترجع
        الاسم/الوصف/قايمة الأعضاء لجروب معيّن، لصفحة معلومات المحادثة.
        Best-effort زيها بالظبط - أي فشل يرجع False من غير ما يبوظ حاجة."""
        try:
            account = self.get_account()
        except ValidationError:
            return False
        if not account.server_url or not account.instance_name or not account.api_key:
            return False
        try:
            url = account._evo_instance_url("group/fetchAllGroups")
            response = requests.get(
                url, headers=account._evo_headers(), params={'getParticipants': 'true'}, timeout=20)
            data = response.json() if response.content else []
        except (requests.RequestException, ValueError):
            return False
        if account.developer_mode:
            _logger.info("WhatsApp group/fetchAllGroups(participants) status=%s response=%s",
                         response.status_code, data)
        if response.status_code != 200 or not isinstance(data, list):
            return False
        for group in data:
            jid = group.get('id') or group.get('remoteJid') or group.get('jid')
            if jid != remote_jid:
                continue
            participants = []
            for p in (group.get('participants') or []):
                p_jid = p.get('id') or p.get('jid') or ''
                phone = self.normalize_phone(p_jid) if (p_jid and not p_jid.endswith('@lid')) else False
                is_admin = str(p.get('admin') or '').lower() in ('admin', 'superadmin', 'true') or bool(p.get('isAdmin'))
                participants.append({'phone': phone, 'is_admin': is_admin})
            return {
                'subject': group.get('subject') or group.get('name') or False,
                'description': group.get('desc') or group.get('description') or False,
                'participants_count': group.get('size') or len(participants),
                'participants': participants,
            }
        return False

    def _fetch_wa_avatar_url(self, phone_number):
        """POST /chat/fetchProfilePictureUrl/{instance} -> رابط صورة
        البروفايل بتاعت الرقم ده على واتساب. Best-effort زي _fetch_wa_
        group_name بالظبط - أي فشل (الشخص مالوش صورة بروفايل، برايفسي
        بتاعته مقفولة، الشبكة، نسخة Evolution قديمة...) يرجع False من غير
        ما يبوظ معالجة الرسالة."""
        if not phone_number:
            return False
        try:
            account = self.get_account()
        except ValidationError:
            return False
        if not account.server_url or not account.instance_name or not account.api_key:
            return False
        try:
            url = account._evo_instance_url("chat/fetchProfilePictureUrl")
            response = requests.post(
                url, headers=account._evo_headers(), json={'number': phone_number}, timeout=10)
            data = response.json() if response.content else {}
        except (requests.RequestException, ValueError):
            return False
        if account.developer_mode:
            _logger.info("WhatsApp chat/fetchProfilePictureUrl(%s) status=%s response=%s",
                         phone_number, response.status_code, data)
        if response.status_code != 200 or not isinstance(data, dict):
            return False
        return data.get('profilePictureUrl') or False

    def _get_or_update_wa_participant(self, phone_number, display_name):
        """بترجّع (اسم، رابط صورة) لعضو الجروب ده - بتكاش الصورة لمدة 7
        أيام بدل ما تجيبها من Evolution مع كل رسالة (تكلفة API calls)،
        وبتحدّث الاسم أول ما ييجي واحد جديد (pushName ممكن يتغيّر). لو
        الرقم مش معروف (Evolution مبعتش participant) بترجع الاسم اللي
        جالنا لوحده من غير كاش."""
        if not phone_number:
            return display_name or False, False
        Participant = self.env['wa.group.participant'].sudo()
        company_id = self.env.company.id
        rec = Participant.search(
            [('phone_number', '=', phone_number), ('company_id', '=', company_id)], limit=1)
        vals = {}
        if display_name and (not rec or rec.display_name != display_name):
            vals['display_name'] = display_name
        now = fields.Datetime.now()
        needs_avatar_refresh = not rec or not rec.avatar_fetched_date or \
            (now - rec.avatar_fetched_date).days >= 7
        if needs_avatar_refresh:
            vals['avatar_url'] = self._fetch_wa_avatar_url(phone_number)
            vals['avatar_fetched_date'] = now
        if rec:
            if vals:
                rec.write(vals)
        else:
            vals.update({'phone_number': phone_number, 'company_id': company_id})
            rec = Participant.create(vals)
        resolved_name = rec.display_name or display_name
        if not resolved_name:
            # مفيش pushName اتبعت خالص للشخص ده - آخر محاولة: لو عنده
            # جهة اتصال محفوظة في أودو باسمه، استخدمه بدل الرقم الخام
            # (زي ما طلب اليوزر: الاسم لو موجود، ومش الرقم أبدًا).
            partner = self.env['res.partner'].sudo().search(
                ['|', ('phone', '=', phone_number), ('mobile', '=', phone_number)], limit=1)
            resolved_name = partner.name if partner and partner.name != phone_number else False
        return resolved_name, rec.avatar_url

    def _update_wa_presence(self, phone_number, presence_state):
        """بتسجّل آخر حالة presence وصلتنا لرقم معيّن - composing/recording
        (بيكتب/بيسجل صوت)، available/unavailable (أونلاين/أوفلاين)،
        paused. لو الحالة available، بنحدّث last_seen كمان."""
        Presence = self.env['wa.conversation.presence'].sudo()
        company_id = self.env.company.id
        rec = Presence.search(
            [('phone_number', '=', phone_number), ('company_id', '=', company_id)], limit=1)
        vals = {'state': presence_state, 'updated_at': fields.Datetime.now()}
        if presence_state == 'available':
            vals['last_seen'] = fields.Datetime.now()
        if rec:
            rec.write(vals)
        else:
            vals.update({'phone_number': phone_number, 'company_id': company_id})
            Presence.create(vals)

    @api.model
    def get_conversation_presence(self, phone_number):
        """بترجع {'state': ..., 'last_seen': ...} للرقم ده - شاشة الشات
        بتسألها كل شوية للمحادثة المفتوحة بس (شوف whatsapp_full_view.js)."""
        rec = self.env['wa.conversation.presence'].sudo().search(
            [('phone_number', '=', phone_number), ('company_id', '=', self.env.company.id)], limit=1)
        if not rec:
            return {'state': False, 'last_seen': False}
        # composing/recording بتنتهي صلاحيتها لو معدّاش وقت من غير تحديث -
        # واتساب نفسه بيعمل كده لو الشخص سكت من غير ما يبعت أو يقفل الشات.
        is_stale = rec.updated_at and (fields.Datetime.now() - rec.updated_at).total_seconds() > 20
        state = False if (rec.state in ('composing', 'recording') and is_stale) else rec.state
        return {
            'state': state,
            'last_seen': fields.Datetime.to_string(rec.last_seen) if rec.last_seen else False,
        }

    def _get_wa_operators(self, res_model=False):
        operators = self.env['res.users']
        if res_model:
            config = self.env['wa.message.model.adaptation'].sudo().search(
                [('model_id.model', '=', res_model)], limit=1)
            if config and config.operator_ids:
                operators = config.operator_ids
        if not operators:
            try:
                account = self.get_account()
                operators = account.default_user_ids
            except ValidationError:
                operators = self.env['res.users']
        return operators

    def _wa_pop_open_channel(self, channel, operators):
        """Forces the chat window to pop open in the bottom-right corner for
        the given operators, the same way Odoo's native Live Chat does for
        an active operator - a plain 'group' channel only shows as unread
        in the Discuss sidebar otherwise, it never auto-pops on its own.

        We do this by writing fold_state='open' on each operator's
        discuss.channel.member (creating it first if they haven't been
        added yet) through _channel_fold(), which is the same internal
        call Odoo's mail module uses and takes care of sending the bus
        notification the web client listens to for opening the floating
        chat window."""
        if not channel or not operators:
            return
        # This whole method is purely cosmetic (auto-popping the chat
        # window). It must NEVER be allowed to raise past this point:
        # an unhandled exception here happens during env.cr.flush(),
        # which fails the whole HTTP request with a 500 and rolls back
        # the transaction - silently discarding the incoming WhatsApp
        # message along with it. So every step below is best-effort.
        try:
            Member = self.env['discuss.channel.member'].sudo()
            partner_ids = operators.mapped('partner_id').ids
            members = Member.search([
                ('channel_id', '=', channel.id),
                ('partner_id', 'in', partner_ids),
            ])
            missing_partner_ids = set(partner_ids) - set(members.mapped('partner_id.id'))
            for partner_id in missing_partner_ids:
                members |= Member.create({'channel_id': channel.id, 'partner_id': partner_id})

            if hasattr(members, '_channel_fold'):
                # Odoo versions where this internal helper still exists.
                members._channel_fold('open', manual_action=False)
            elif 'fold_state' in members._fields:
                # Older/alternate versions with a plain fold_state field.
                members.write({'fold_state': 'open'})
            elif 'unpin_dt' in members._fields:
                # Odoo 19: fold_state/_channel_fold are gone. The
                # floating chat window is now controlled by "pinning" -
                # unpin_dt=False (+ a fresh last_interest_dt so it sorts
                # to the top) means the channel is open/pinned for that
                # member, exactly what discuss.channel.channel_pin(True)
                # does for the current user. We do it directly via write()
                # since these are other users' member records, then
                # broadcast the channel header on the bus so each
                # operator's already-open web client actually pops the
                # window open live (channel._broadcast is the same call
                # channel_pin()/_find_or_create_chat() use for this).
                now = fields.Datetime.now()
                members.write({'unpin_dt': False, 'last_interest_dt': now})
                channel._broadcast(partner_ids)
            else:
                # Neither the old nor the new API is available - don't
                # guess at internal APIs, just skip the cosmetic pop-open.
                _logger.info(
                    "Skipping WhatsApp chat auto-pop for channel %s: "
                    "no supported fold/pin API on this Odoo version",
                    channel.id,
                )
        except Exception:
            # Absolute last resort - never let this bubble up.
            _logger.exception("Could not auto-pop WhatsApp chat window for channel %s", channel.id)

    def _get_or_create_wa_channel(self, phone_number, res_model=False, res_id=False):
        phone_number = self.normalize_phone(phone_number)
        Channel = self.env['discuss.channel'].sudo()
        channel = Channel.search([('wa_phone_number', '=', phone_number)], limit=1)
        operators = self._get_wa_operators(res_model)
        if channel:
            self._wa_pop_open_channel(channel, operators)
            return channel
        partner = self._get_or_create_wa_partner(phone_number)
        member_partners = operators.mapped('partner_id') | partner
        channel = Channel.create({
            'name': _("WhatsApp - %s") % (partner.name or phone_number),
            'channel_type': 'group',
            'channel_partner_ids': [(4, pid) for pid in member_partners.ids],
            'wa_phone_number': phone_number,
            'wa_res_model': res_model or False,
            'wa_res_id': str(res_id) if res_id else False,
        })
        self._wa_pop_open_channel(channel, operators)
        return channel

    @api.model
    def _get_conversation_token(self, phone_number):
        """Latest conversation_token used for this phone number, if any -
        so a reply sent from Discuss (which only knows the phone number)
        can be tagged with the same token the website widget is polling on."""
        phone_number = self.normalize_phone(phone_number)
        if not phone_number:
            return False
        msg = self.sudo().search([
            ('phone_number', '=', phone_number),
            ('conversation_token', '!=', False),
        ], limit=1, order='create_date DESC')
        return msg.conversation_token or False

    def _get_or_create_website_channel(self, conversation_token, name, res_model=False, res_id=False):
        """Like _get_or_create_wa_channel, but for anonymous website
        visitors that have no real WhatsApp number: the channel is keyed by
        conversation_token instead of phone_number, is flagged
        wa_internal_only so DiscussChannelWhatsApp.message_post never tries
        to send a real WhatsApp message for it, and keeps a dedicated
        visitor partner (since there's no phone number to dedupe on)."""
        Channel = self.env['discuss.channel'].sudo()
        channel = Channel.search([('wa_conversation_token', '=', conversation_token)], limit=1)
        operators = self._get_wa_operators(res_model)
        if channel:
            self._wa_pop_open_channel(channel, operators)
            return channel
        partner = self.env['res.partner'].sudo().create({
            'name': name or _("Website Visitor"),
        })
        member_partners = operators.mapped('partner_id') | partner
        channel = Channel.create({
            'name': _("Website Chat - %s") % (name or conversation_token[:8]),
            'channel_type': 'group',
            'channel_partner_ids': [(4, pid) for pid in member_partners.ids],
            'wa_conversation_token': conversation_token,
            'wa_internal_only': True,
            'wa_visitor_partner_id': partner.id,
            'wa_res_model': res_model or False,
            'wa_res_id': str(res_id) if res_id else False,
        })
        self._wa_pop_open_channel(channel, operators)
        return channel

    def _post_wa_message_to_channel(self, channel, text, direction, phone_number=False, author_partner=False):
        if not channel or not text:
            return
        if author_partner:
            author = author_partner
        else:
            author = self._get_or_create_wa_partner(phone_number) if direction == 'inbound' else self.env.user.partner_id
        channel.sudo().with_context(wa_skip_forward=True).message_post(
            body=text,
            author_id=author.id,
            message_type='comment',
            subtype_xmlid='mail.mt_comment',
        )

    @api.model
    def _extract_incoming_media(self, message):
        """بتدوّر جوه message object بتاع Baileys/Evolution على أول نوع
        ميديا معروف (صورة/فيديو/صوت/مستند/ستيكر)، وترجع
        (media_b64, mimetype, filename, caption) أو (False, False, False, False)
        لو الرسالة نص عادي أو الميديا معملهاش base64 (لو webhookBase64 مش
        مفعّل على الـ instance)."""
        media_keys = {
            'imageMessage': 'jpg',
            'videoMessage': 'mp4',
            'audioMessage': 'ogg',
            'documentMessage': 'bin',
            'documentWithCaptionMessage': 'bin',
            'stickerMessage': 'webp',
        }
        for key, default_ext in media_keys.items():
            obj = message.get(key)
            if not obj:
                continue
            # documentWithCaptionMessage بيلف المحتوى الحقيقي جوه
            # obj['message']['documentMessage']
            if key == 'documentWithCaptionMessage':
                obj = (obj.get('message') or {}).get('documentMessage') or {}
                if not obj:
                    continue
            # Evolution بيحط الـ base64 كـ sibling field جوه نفس الـ object
            # لما webhookBase64 يبقى مفعّل، أو أحيانًا في مفتاح 'base64' على
            # مستوى الرسالة كلها - بندوّر في المكانين للأمان. لو مفيش base64
            # (webhookBase64 مش مفعّل)، لسه بنرجّع filename/caption عشان
            # الطبقة اللي فوق تقدر تعرض نص مفهوم بدل ما تضيّع الرسالة.
            media_b64 = obj.get('base64') or message.get('base64') or False
            mimetype = obj.get('mimetype') or 'application/octet-stream'
            filename = obj.get('fileName') or ('%s.%s' % (key.replace('Message', ''), default_ext))
            caption = obj.get('caption') or ''
            return media_b64, mimetype, filename, caption
        return False, False, False, False

    @api.model
    def _extract_incoming_text(self, message, media_b64, filename, caption):
        """بتقرر نص الرسالة اللي هيتخزن، وترجع False لو الرسالة دي فعليًا
        مالهاش محتوى مرئي أصلاً (زي senderKeyDistributionMessage الوحيدة
        اللي Baileys بيبعتها لتوزيع مفتاح تشفير جروب - مش رسالة حقيقية)،
        عشان الطبقة اللي فوق تتجاهلها تمامًا بدل ما تعرض اسم النوع الخام."""
        if 'conversation' in message:
            return message.get('conversation') or ''
        if 'extendedTextMessage' in message:
            return (message.get('extendedTextMessage') or {}).get('text', '')
        if 'reactionMessage' in message:
            emoji = (message.get('reactionMessage') or {}).get('text') or ''
            return (_('تفاعل بإيموجي: %s') % emoji) if emoji else _('ألغى تفاعله بإيموجي')
        location = message.get('locationMessage') or message.get('liveLocationMessage')
        if location is not None:
            lat = location.get('degreesLatitude')
            lng = location.get('degreesLongitude')
            label = location.get('name') or location.get('address') or ''
            maps_link = ("https://www.google.com/maps?q=%s,%s" % (lat, lng)) if (lat and lng) else ''
            parts = [p for p in (_('📍 موقع'), label, maps_link) if p]
            return ' - '.join(parts)
        contact = message.get('contactMessage')
        if contact is not None:
            name = contact.get('displayName') or _('جهة اتصال')
            return _('👤 جهة اتصال: %s') % name
        contacts_array = message.get('contactsArrayMessage')
        if contacts_array is not None:
            contacts = contacts_array.get('contacts') or []
            names = [c.get('displayName') for c in contacts if c.get('displayName')]
            if names:
                extra = len(names) - 1
                label = names[0] + (_(' و%d آخرين') % extra if extra > 0 else '')
            else:
                label = _('%d جهة اتصال') % len(contacts)
            return _('👤 جهات اتصال: %s') % label
        poll = message.get('pollCreationMessage') or message.get('pollCreationMessageV3')
        if poll is not None:
            name = poll.get('name') or _('استطلاع')
            options = [o.get('optionName') for o in (poll.get('options') or []) if o.get('optionName')]
            lines = [_('📊 استطلاع: %s') % name] + ['• %s' % o for o in options]
            return '\n'.join(lines)
        media_labels = {
            'imageMessage': _('📷 صورة'),
            'videoMessage': _('🎥 فيديو'),
            'audioMessage': _('🎤 رسالة صوتية'),
            'documentMessage': _('📄 مستند'),
            'documentWithCaptionMessage': _('📄 مستند'),
            'stickerMessage': _('🩹 ستيكر'),
        }
        matched_media_key = next((k for k in media_labels if k in message), False)
        if matched_media_key:
            # فيه ميديا معروفة (سواء وصلها base64 ولا لأ) - استخدم الـ
            # caption لو موجود، وإلا اسم الملف (للمستندات)، وإلا وصف افتراضي.
            if caption:
                return caption
            if filename and matched_media_key in ('documentMessage', 'documentWithCaptionMessage'):
                return filename
            return media_labels[matched_media_key]
        # ملهاش نص، ملهاش ميديا معروفة، وكل اللي فيها مفاتيح
        # بروتوكول/تشفير مالهاش محتوى مرئي (senderKeyDistributionMessage،
        # messageContextInfo، protocolMessage) - يبقى مفيش حاجة تتعرض خالص.
        noise_keys = {'senderKeyDistributionMessage', 'messageContextInfo', 'protocolMessage'}
        real_keys = set(message.keys()) - noise_keys
        if not real_keys:
            return False
        # أنواع تانية بروتوكول/داخلية بحتة - Baileys بيبعتها لأسباب زي
        # مزامنة تصويت استطلاع، أو تعديل رسالة، أو "اتقرت" - مالهاش بابل
        # مرئية في واتساب نفسه، فمينفعش تتعرض عندنا برضو.
        silent_keys = {
            'secretEncryptedMessage', 'pollUpdateMessage', 'pollEncValueMessage',
            'keepInChatMessage', 'markChatAsReadMessage', 'editedMessage',
            'appStateSyncKeyShareMessage',
        }
        if real_keys <= silent_keys:
            return False
        # نوع مش متعامل معاه أصلاً - بدل ما نسرّب اسم المفتاح الخام (زي
        # "[secretEncryptedMessage]") لليوزر، نعرض نص عام مفهوم.
        return _('⚠️ نوع رسالة غير مدعوم')

    @api.model
    def _extract_incoming_context_info(self, message):
        """contextInfo (بيانات "الرد على رسالة") غالبًا بتتحط جوه نفس الـ
        wrapper بتاع نوع الرسالة (extendedTextMessage.contextInfo لو نص،
        imageMessage.contextInfo لو صورة، ...إلخ) - لكن بعض إصدارات
        Evolution/Baileys (خصوصًا لما الرد جاي من التليفون نفسه مش من
        Odoo) بتحطها على مستوى الرسالة كلها مباشرة (message.contextInfo)
        من غير ما تلفها جوه نوع الرسالة. بندوّر في الاتنين قبل ما نستسلم.
        بترجع {} لو مفيش رد."""
        if message.get('contextInfo'):
            return message['contextInfo']
        candidates = [
            message.get('extendedTextMessage'),
            message.get('imageMessage'),
            message.get('videoMessage'),
            message.get('audioMessage'),
            message.get('documentMessage'),
            (message.get('documentWithCaptionMessage') or {}).get('message', {}).get('documentMessage'),
            message.get('stickerMessage'),
        ]
        for obj in candidates:
            if obj and obj.get('contextInfo'):
                return obj['contextInfo']
        return {}

    @api.model
    def _extract_quoted_preview(self, context_info):
        """quotedMessage جوه contextInfo نسخة كاملة من الرسالة المقتبسة
        نفسها (نفس شكل أي message عادي) - فبنعيد استخدام _extract_incoming_
        text عليها زي أي رسالة تانية، ومقصوصة عشان تناسب مربع الاقتباس
        الصغير. بترجع (participant_jid, preview_text) أو (False, False)."""
        quoted_message = context_info.get('quotedMessage') if context_info else False
        if not quoted_message:
            return False, False
        participant = context_info.get('participant') or False
        media_b64, mimetype, filename, caption = self._extract_incoming_media(quoted_message)
        text = self._extract_incoming_text(quoted_message, media_b64, filename, caption)
        if not text:
            text = False
        elif len(text) > 120:
            text = text[:117] + '...'
        return participant, text

    @api.model
    def _apply_incoming_protocol_message(self, protocol, webhook_record):
        """protocolMessage مش رسالة مستقلة - بتشاور على رسالة موجودة عندنا
        بالفعل وتقول لنا نعدّلها: يا إما "اتحذفت لدى الجميع" (REVOKE) يا
        إما "اتعدّلت" (MESSAGE_EDIT). بترجع False زي reactionMessage
        بالظبط (مفيش بابل جديدة تتعرض في الشات، بس تعديل على القديمة)."""
        target_wamid = (protocol.get('key') or {}).get('id')
        if not target_wamid:
            return False
        original = self.sudo().search([('dialog_message_id', '=', target_wamid)], limit=1)
        if not original:
            return False
        ptype = str(protocol.get('type') or '').upper()
        if ptype in ('REVOKE', '0'):
            original.write({'is_revoked': True})
        elif ptype in ('MESSAGE_EDIT', '14') and protocol.get('editedMessage'):
            new_text = self._extract_incoming_text(protocol['editedMessage'], False, False, False)
            if new_text:
                original.write({'message_content': new_text, 'is_edited': True})
        else:
            # نوع مش متعرف عليه - لوج تشخيصي زي ما عملنا مع contextInfo
            # قبل كده، عشان لو اتكرر نقدر نظبطه بسرعة من غير تخمين.
            _logger.info(
                "Unhandled WhatsApp protocolMessage type=%s payload=%s",
                protocol.get('type'), protocol,
            )
        return False

    @api.model
    def _apply_incoming_reaction(self, reaction, from_me, webhook_record):
        """رسالة reactionMessage مش رسالة مستقلة - هي تفاعل بإيموجي على
        رسالة موجودة بالفعل عندنا. بندوّر عليها بالـ wamid بتاعها
        (reaction['key']['id']) ونحدّث reactions_json عليها بدل ما نعمل
        بابل جديدة تلخبط الشات. لو الرسالة الأصلية مش موجودة عندنا (مثلاً
        جت قبل ما الـ webhook يتظبط)، بنتجاهل التفاعل بهدوء."""
        target_wamid = (reaction.get('key') or {}).get('id')
        emoji = reaction.get('text') or ''
        if not target_wamid:
            return False
        target = self.sudo().search([('dialog_message_id', '=', target_wamid)], limit=1)
        if not target:
            return False
        sender_key = '__me__' if from_me else '__them__'
        try:
            data = json.loads(target.reactions_json) if target.reactions_json else {}
        except (ValueError, TypeError):
            data = {}
        if emoji:
            data[sender_key] = emoji
        else:
            data.pop(sender_key, None)
        target.reactions_json = json.dumps(data)
        if webhook_record:
            target.webhook_message_ids = [(4, webhook_record.id)]
        return target

    @api.model
    def _incoming_wa_timestamp(self, incoming):
        """messageTimestamp بيجي من Evolution/Baileys كـ unix seconds (أو
        نص رقمي) - لو مش موجود أو مش قابل للتحويل، النهارده/دلوقتي زي ما
        كان بيحصل ضمنيًا مع create_date قبل كده."""
        ts = incoming.get('messageTimestamp')
        try:
            ts = int(ts)
            if ts > 0:
                return fields.Datetime.to_string(datetime.fromtimestamp(ts, tz=timezone.utc).replace(tzinfo=None))
        except (TypeError, ValueError):
            pass
        return fields.Datetime.now()

    @api.model
    def _process_incoming_message(self, incoming, webhook_record, from_me=False, is_history=False):
        """بتاخد عنصر واحد من مصفوفة data['messages'] (أو data نفسها) القادمة
        من Evolution API (حدث messages.upsert)، وتخزنها - نص و/أو ميديا
        (صورة/صوت/فيديو/مستند) أو ايموجي reaction - وتحاول تربطها بأي Lead
        عنده نفس الرقم عشان تبان في المحادثة (chatter) وفي polling ويدجت
        الموقع.

        from_me=True معناها الرسالة دي جاية من جهاز تاني متسجل بنفس رقم
        الواتساب (غالبًا الموبايل الحقيقي نفسه) - مش من Odoo - ووصلنا هنا
        أصلاً لأن message_process تأكد الأول إنها مش echo لرسالة Odoo نفسه
        بعتها. فبنسجلها كرسالة outbound عادية بالظبط زي أي رسالة بعتها
        Odoo، عشان أي حد يكتب من على التليفون (رد أو حتى reaction) يبان في
        نفس المحادثة فورًا - بالظبط زي WhatsApp Web/multi-device.

        is_history=True معناها إحنا بنستورد رسايل قديمة بالجملة (sync_
        history_from_whatsapp) مش بنعالج حدث webhook حي - فبنتجنب أي حاجة
        "صاخبة" زي فتح شات ديسكص أو popup أو note على الـ Lead."""
        key = incoming.get('key') or {}
        remote_jid = key.get('remoteJid') or ''
        from_number = self.normalize_phone(remote_jid)
        message_id = key.get('id')
        message = incoming.get('message') or {}
        # مفيش "wrapper" واحد بس - Baileys بيلف المحتوى الحقيقي جوه طبقات
        # زي بعض (وممكن أكتر من طبقة مع بعض) في حالات زي: رسالة جاية من
        # جهاز تاني (deviceSentMessage)، أو شات فيه "الرسائل المؤقتة"
        # مفعّلة (ephemeralMessage)، أو رسالة "شاهدها مرة واحدة" (viewOnce
        # /viewOnceV2). النص/الرد/الميديا الحقيقيين دايمًا جوه .message
        # بتاع أي واحد من دول - فبنفك كل الطبقات دي واحدة ورا التانية لحد
        # ما نوصل للمحتوى الفعلي، بدل ما نتجاهل الرسالة كلها بالغلط.
        wrapper_keys = ('deviceSentMessage', 'ephemeralMessage',
                        'viewOnceMessage', 'viewOnceMessageV2', 'viewOnceMessageV2Extension')
        for _unwrap_i in range(5):
            wrapper = next((message[k] for k in wrapper_keys if isinstance(message.get(k), dict)), None)
            if wrapper is None or not isinstance(wrapper.get('message'), dict):
                break
            message = wrapper['message']
        direction = 'outbound' if from_me else 'inbound'
        # For an individual chat, Evolution/Baileys includes the sender's
        # current WhatsApp display name as pushName on almost every event -
        # for a group (remoteJid ending in @g.us) it's the participant's
        # name, not the group's, so the group subject is fetched separately
        # (see _fetch_wa_group_name). Threaded through context rather than
        # extra params so _get_or_create_wa_partner (already called from
        # several places) doesn't need its signature changed everywhere.
        push_name = incoming.get('pushName') or False
        self = self.with_context(wa_remote_jid=remote_jid, wa_push_name=push_name)

        # ------------------------------------------------------------
        # هوية المرسل الفعلي جوه الجروب (زي واتساب بالظبط): في رسايل
        # الجروب، Baileys بيحط الـ JID بتاع الجروب في key.remoteJid لكن
        # الشخص اللي بعت فعليًا موجود في key.participant - وpushName هنا
        # فعلاً اسمه هو (مش اسم الجروب، شوف _wa_display_name_from_context
        # فوق). بنكاش اسمه/صورته في wa.group.participant عشان نعرضهم فوق
        # كل رسالة بدل رقمه الخام، ومنعملش الحاجة دي أصلاً لغير الرسايل
        # الواردة من جروب (شات فردي أو رسايل بعتناها إحنا مش محتاجة كده).
        # ------------------------------------------------------------
        is_group = '@g.us' in remote_jid
        sender_phone = False
        sender_name = False
        sender_avatar_url = False
        if is_group and not from_me:
            # بعض إصدارات Evolution/Baileys بتحط الـ participant في مكان
            # مختلف حسب النسخة أو نوع الرسالة (participantAlt بيظهر مع
            # حسابات LID الجديدة، وبعض الأحداث بتحطه على مستوى الرسالة
            # نفسها مش جوه key) - بندوّر في كل الاحتمالات المعروفة قبل ما
            # نستسلم ونعتبره غير معروف.
            participant_jid = (
                key.get('participant') or key.get('participantAlt')
                or incoming.get('participant') or incoming.get('author') or ''
            )
            sender_phone = self.normalize_phone(participant_jid) if participant_jid else False
            sender_name, sender_avatar_url = self._get_or_update_wa_participant(sender_phone, push_name)

        if 'protocolMessage' in message:
            return self._apply_incoming_protocol_message(message['protocolMessage'], webhook_record)

        if 'reactionMessage' in message:
            return self._apply_incoming_reaction(message['reactionMessage'], from_me, webhook_record)

        media_b64, mimetype, filename, caption = self._extract_incoming_media(message)
        text = self._extract_incoming_text(message, media_b64, filename, caption)
        if text is False:
            # senderKeyDistributionMessage / messageContextInfo / protocolMessage
            # لوحدهم من غير أي محتوى حقيقي - رسالة بروتوكول داخلية من
            # Baileys/Evolution (بتحصل كتير في الجروبات)، مش رسالة فعلية
            # من حد. تجاهلها تمامًا بدل ما تتعرض في الشات كسطر فاضي.
            _logger.info(
                "Skipping WhatsApp webhook item with no visible content (keys=%s)",
                list(message.keys()),
            )
            return False
        if not text and not media_b64:
            # وصلنا هنا (مش noise معروف) بس النص طلع فاضي برضو - نوع رسالة
            # مش متوقع أو طبقة wrapper تالتة مالحقناش نفكها. بنسجّلها زي ما
            # هي (بابل فاضي بدل ما نضيّعها) بس بنحط لوج فيه شكل الرسالة
            # الخام كامل عشان نقدر نشخّص بالظبط لو اتكررت.
            _logger.warning(
                "WhatsApp webhook item resolved to empty text (keys=%s) raw_message=%s",
                list(message.keys()), message,
            )

        # ------------------------------------------------------------
        # "الرد على رسالة" (Reply/Quote): لو الرسالة دي رد على رسالة تانية،
        # contextInfo بيحمل نسخة كاملة من الرسالة المقتبسة + مين بعتها
        # (مفيد خصوصًا في الجروبات). بنحلها هنا مرة واحدة ونخزن preview
        # جاهز على الرسالة نفسها بدل ما نعيد البحث عنها كل مرة تتعرض.
        # ------------------------------------------------------------
        context_info = incoming.get('contextInfo') or self._extract_incoming_context_info(message)
        quoted_wamid = context_info.get('stanzaId') or False
        quoted_participant_jid, quoted_preview = self._extract_quoted_preview(context_info)
        quoted_sender_name = False
        quoted_original = False
        if quoted_wamid:
            quoted_original = self.sudo().search([('dialog_message_id', '=', quoted_wamid)], limit=1)
        if quoted_participant_jid:
            # واتساب بدأ يستخدم هوية "@lid" (خصوصية) بدل رقم التليفون
            # الصريح في بعض الحقول - رقم زي ده مش رقم تليفون حقيقي خالص،
            # فمينفعش نـ normalize_phone عليه أو نستخدمه في بحث/كاش زي أي
            # رقم عادي (هيطلع رقم وهمي غلط).
            is_lid = quoted_participant_jid.endswith('@lid')
            if is_group and not is_lid:
                quoted_phone = self.normalize_phone(quoted_participant_jid)
                quoted_sender_name, _unused_avatar = self._get_or_update_wa_participant(quoted_phone, False)
            elif not is_group:
                # شات فردي: مفيش غير طرفين (أنا أو الشخص التاني) - وجود
                # participant هنا (حتى بصيغة @lid) معناه عمليًا إن
                # المقتبس مش أنا، فبنستخدم اسم جهة الاتصال بتاعة
                # المحادثة نفسها بدل ما نحاول نفك الـ lid.
                quoted_sender_name = self._fullview_contact_name(from_number)
            # جروب + @lid: مقدرش أحل هويته من غير رقم حقيقي - هيتغطى
            # تحت لو الرسالة الأصلية موجودة عندنا محليًا بالـ wamid.
        if not quoted_sender_name and quoted_original:
            # مفيش participant قابل للحل - غالبًا رد على رسالة إحنا
            # بعتناها إحنا نفسنا (Baileys غالبًا بيسيب participant فاضي
            # في الحالة دي). لو الرسالة الأصلية موجودة عندنا فعلاً،
            # بنستخدم بياناتها الحقيقية بدل ما نسيب الاسم فاضي.
            quoted_sender_name = _('أنت') if quoted_original.direction == 'outbound' else (quoted_original.wa_sender_name or False)
        if not quoted_preview and quoted_original:
            # بعض الأحداث (خصوصًا ردود متبعتة من التليفون نفسه) بتبعت
            # contextInfo من غير نسخة quotedMessage كاملة - لو الرسالة
            # الأصلية موجودة عندنا محليًا، بنستخدم نصها هي كـ preview.
            quoted_preview = (quoted_original.message_content or '').strip()[:120] or False
        if context_info:
            _logger.info("WhatsApp reply contextInfo=%s -> wamid=%s participant=%s preview=%s",
                         context_info, quoted_wamid, quoted_participant_jid, quoted_preview)

        # الإشارة لعضو في الجروب (@mention): نص الرسالة بيوصل فيه "@<رقم>"
        # حرفيًا، وcontextInfo.mentionedJid بيقول لنا مين الأرقام دي فعلاً
        # - بنبني {رقم: اسم} عشان الشاشة تقدر تستبدل الرقم الخام باسم
        # الشخص وتلوّنها، بدل ما تعرض "@201234567890" زي ما هي.
        mentioned_map = {}
        if is_group:
            for jid in (context_info.get('mentionedJid') or []):
                if not jid or jid.endswith('@lid'):
                    continue
                m_phone = self.normalize_phone(jid)
                if not m_phone:
                    continue
                m_name, _unused_avatar = self._get_or_update_wa_participant(m_phone, False)
                mentioned_map[m_phone] = m_name or m_phone

        res_model = False
        res_id = False
        lead = self.env['crm.lead'].sudo().search([('phone', '=', from_number)], limit=1, order='create_date DESC')
        if lead:
            res_model = 'crm.lead'
            res_id = str(lead.id)
        last_outbound = self.sudo().search([
            ('phone_number', '=', from_number),
            ('conversation_token', '!=', False),
        ], limit=1, order='create_date DESC')
        vals = {
            'res_id': res_id,
            'res_model': res_model,
            'message_content': text,
            'direction': direction,
            'phone_number': from_number,
            'dialog_message_id': message_id,
            'conversation_token': last_outbound.conversation_token if last_outbound else False,
            # A message typed on the phone has already left, so it starts
            # at 'sent' like any other outbound message (later upgraded to
            # delivered/read by messages.update, matched on dialog_message_id
            # exactly like Odoo-sent messages). An inbound message from the
            # customer is 'read' immediately since we're viewing it live.
            'status': 'sent' if from_me else 'read',
            'company_id': self.env.user.company_id.id,
            'webhook_message_ids': [(4, webhook_record.id)] if webhook_record else False,
            'wa_timestamp': self._incoming_wa_timestamp(incoming),
            'wa_is_group': is_group,
            'wa_sender_phone': sender_phone,
            'wa_sender_name': sender_name,
            'wa_sender_avatar_url': sender_avatar_url,
            'wa_quoted_wamid': quoted_wamid or False,
            'wa_quoted_sender_name': quoted_sender_name or False,
            'wa_quoted_preview': quoted_preview or False,
            'wa_mentioned_json': json.dumps(mentioned_map) if mentioned_map else False,
        }
        if media_b64:
            vals.update({
                'media_data': media_b64,
                'media_filename': filename,
                'media_mimetype': mimetype,
            })
        wa = self.sudo().create(vals)
        if is_history:
            # استيراد جماعي - مفيش discuss channel/popup/note، عشان
            # مانغرقش اليوزر بإشعارات لرسايل قديمة.
            return wa
        channel = self._get_or_create_wa_channel(from_number, res_model, res_id)
        wa.discuss_channel_id = channel.id
        self._post_wa_message_to_channel(channel, text, direction, from_number)
        # Re-pop the window even if the operator had folded/closed it since
        # the channel was created, so a reply always surfaces immediately.
        self._wa_pop_open_channel(channel, self._get_wa_operators(res_model))
        if lead:
            label = _("WhatsApp (sent from phone): ") if from_me else _("WhatsApp reply: ")
            lead.message_post(body=label + text)
        if from_me:
            # The phone owner just typed a real reply from the device itself -
            # a human has taken over, stop the auto-reply bot for this chat.
            if channel.wa_bot_active:
                channel.wa_bot_active = False
        else:
            self._maybe_send_auto_reply(channel, res_model, res_id, from_number)
        return wa

    def _maybe_send_auto_reply(self, channel, res_model, res_id, phone_number):
        """Sends the account's configured auto-reply template back to the
        customer, as long as no human (operator in Odoo or the phone owner)
        has taken over this conversation yet (channel.wa_bot_active)."""
        if not channel or not channel.wa_bot_active:
            return
        try:
            account = self.get_account()
        except ValidationError:
            return
        template = account.auto_reply_enabled and account.auto_reply_template_id
        if not template:
            return
        try:
            if template.model_id and res_model == template.model_id.model and res_id:
                text = template.get_sending_txt(template.get_params_values(int(res_id)))
            else:
                text = template.content or ''
        except ValidationError:
            text = template.content or ''
        if template.footer_message:
            text = (text + '\n\n' + template.footer_message) if text else template.footer_message
        if not text:
            return
        # send_message() does a real, synchronous HTTP call to Evolution API.
        # If Evolution is slow/unreachable/rate-limiting right when a
        # customer message comes in, letting that exception bubble up would
        # roll back the incoming message we already saved above (see the
        # try/except around _process_incoming_message in message_process) -
        # the auto-reply is a nice-to-have, it must never be able to cost us
        # the message that triggered it.
        try:
            self.sudo().with_context(wa_skip_forward=True).send_message(
                res_id=res_id, res_model=res_model, phone_number=phone_number, text=text)
        except Exception:
            _logger.exception(
                "Failed to send WhatsApp auto-reply to %s (channel=%s)",
                phone_number, channel.id if channel else False)

    @api.model
    def normalize_phone(self, phone, country_code=False):
        if not phone:
            return phone
        # Strip Evolution/Baileys JID suffixes (e.g. "201553513977@s.whatsapp.net")
        # before the usual cleanup.
        phone = phone.split('@')[0]
        phone = phone.replace(" ", "").replace("-", "")
        if phone.startswith('00'):
            phone = phone[2:]
        elif phone.startswith('+'):
            phone = phone[1:]
        # Best-effort guess when we don't know in advance which format the
        # number arrived in (local "01220744453", already-international
        # "201220744453", or missing both the leading 0 and the country
        # code "1220744453"). This is only a first guess - send_message()
        # additionally tries the raw/other candidate via
        # _wa_phone_candidates() and lets Evolution's own "exists" check
        # decide which one is actually correct.
        if country_code:
            if phone.startswith('0') and not phone.startswith(country_code):
                phone = country_code + phone[1:]
            elif not phone.startswith(country_code) and len(phone) <= 10:
                phone = country_code + phone
        return phone

    @api.model
    def _wa_phone_candidates(self, phone, country_code=False):
        """Ordered, deduplicated list of phone number formats worth trying
        against Evolution, since we can't reliably know in advance which
        format a given input (website visitor, imported contact, ...) is
        in. First entry is our best guess; the rest are fallbacks."""
        raw = (phone or '').split('@')[0].replace(" ", "").replace("-", "")
        if raw.startswith('00'):
            raw = raw[2:]
        elif raw.startswith('+'):
            raw = raw[1:]
        candidates = [self.normalize_phone(phone, country_code=country_code)]
        if raw and raw not in candidates:
            candidates.append(raw)
        if country_code and raw.startswith('0') and (country_code + raw) not in candidates:
            # in case the '0' wasn't actually a trunk prefix to strip
            candidates.append(country_code + raw)
        return [c for c in candidates if c]

    @api.model
    def _is_number_not_exists_error(self, response_data):
        """True when Evolution rejected the send specifically because the
        number isn't a valid/registered WhatsApp JID (as opposed to an
        auth/network/other failure), e.g.
        {"response": {"message": [{"exists": false, ...}]}}"""
        if not isinstance(response_data, dict):
            return False
        messages = (response_data.get('response') or {}).get('message')
        if isinstance(messages, list):
            return any(isinstance(m, dict) and m.get('exists') is False for m in messages)
        return False

    @api.model
    def get_conversation(self, conversation_token, after_id=0):
        if not conversation_token:
            return []
        domain = [('conversation_token', '=', conversation_token)]
        if after_id:
            domain.append(('id', '>', int(after_id)))
        messages = self.sudo().search(domain, order="create_date ASC, id ASC")
        result = []
        for m in messages:
            has_media = bool(m.media_data)
            _logger.info(
                "WhatsApp widget get_conversation: id=%s text=%r has_media=%s media_filename=%s media_mimetype=%s conversation_token=%s",
                m.id, m.message_content, has_media, m.media_filename, m.media_mimetype, m.conversation_token)
            result.append({
                'id': m.id,
                'direction': m.direction,
                'text': m.message_content,
                'status': m.status,
                'create_date': fields.Datetime.to_string(m.create_date),
                # Image/video/document attached to this message, if any -
                # served through a dedicated public+token-checked route
                # (not /web/image, which enforces normal ACLs anonymous
                # website visitors don't have).
                'media_url': ('/api/v1/whatsapp/widget/media/%s?token=%s' % (m.id, conversation_token)) if has_media else False,
                'media_type': self._wa_media_type(m.media_mimetype) if has_media else False,
                'media_filename': m.media_filename if has_media else False,
            })
        return result

    @api.model
    def start_website_conversation(self, name, phone, message):
        """يستخدمها ويدجت الموقع لعمل شات حي جوه الموقع.

        - لو الزائر حط رقم واتساب حقيقي: بتعمل Lead وتبعت أول رسالة فعليًا
          عن طريق Evolution API زي قبل كده.
        - لو الزائر مجهول (مفيش رقم، زي زوار الويدجت اللي مش حاطين رقمهم):
          المحادثة كاملة بتفضل جوه الموقع/Discuss بس - من غير ما تتبعت أي
          رسالة فعلية لواتساب - وبرضه بترجع نفس شكل الـ conversation_token
          عشان الفرونت يعمل عليه polling بنفس الطريقة.
        بترجع conversation_token عشان الفرونت يعمل عليه polling على الردود
        بعد كده (مش الرقم مباشرة، عشان محدش يقدر يشوف محادثة غيره لو خمّن
        رقم تليفون)."""
        token = uuid.uuid4().hex
        phone_number = False
        if phone:
            try:
                account = self.get_account()
                phone_number = self.normalize_phone(phone, country_code=account.default_country_code)
            except ValidationError:
                phone_number = self.normalize_phone(phone)

        if phone_number:
            lead = self.env['crm.lead'].sudo().create({
                'name': _("Website WhatsApp Widget - %s") % (name or phone_number),
                'contact_name': name or False,
                'phone': phone_number,
                'description': message,
                'medium_id': self.env.ref('utm.utm_medium_website', raise_if_not_found=False).id if self.env.ref('utm.utm_medium_website', raise_if_not_found=False) else False,
            })
            wa = self.sudo().send_message(res_id=lead.id, res_model='crm.lead', phone_number=phone, text=message)
            if wa:
                wa.conversation_token = token
        else:
            lead = self.env['crm.lead'].sudo().create({
                'name': _("Website Chat Widget - %s") % (name or token[:8]),
                'contact_name': name or False,
                'description': message,
                'medium_id': self.env.ref('utm.utm_medium_website', raise_if_not_found=False).id if self.env.ref('utm.utm_medium_website', raise_if_not_found=False) else False,
            })
            channel = self._get_or_create_website_channel(token, name, 'crm.lead', lead.id)
            wa = self.sudo().create({
                'res_id': str(lead.id),
                'res_model': 'crm.lead',
                'message_content': message,
                'direction': 'inbound',
                'conversation_token': token,
                'status': 'read',
                'company_id': self.env.user.company_id.id,
                'discuss_channel_id': channel.id,
            })
            self._post_wa_message_to_channel(channel, message, 'inbound', author_partner=channel.wa_visitor_partner_id)
        return {
            'lead_id': lead.id,
            'conversation_token': token,
            'last_message_id': wa.id if wa else 0,
        }

    @api.model
    def reply_to_conversation(self, conversation_token, message):
        """بتاخد رسالة إضافية من نفس الزائر (بعد أول رسالة) وتبعتها في نفس
        المحادثة. لو المحادثة داخلية بس (زائر مجهول) بتتخزن مباشرة من غير
        أي رسالة فعلية لواتساب، ولو محادثة برقم حقيقي بتتبعت فعليًا زي
        قبل كده - في الحالتين بنفس الـ conversation_token عشان الويدجت
        يكمل يشوف الرد."""
        if not conversation_token:
            raise ValidationError(_("Invalid conversation"))
        first = self.sudo().search([('conversation_token', '=', conversation_token)], limit=1, order='create_date ASC')
        if not first:
            raise ValidationError(_("Conversation not found"))
        channel = first.discuss_channel_id
        if channel and channel.wa_internal_only:
            wa = self.sudo().create({
                'res_id': first.res_id,
                'res_model': first.res_model,
                'message_content': message,
                'direction': 'inbound',
                'conversation_token': conversation_token,
                'status': 'read',
                'company_id': self.env.user.company_id.id,
                'discuss_channel_id': channel.id,
            })
            self._post_wa_message_to_channel(channel, message, 'inbound', author_partner=channel.wa_visitor_partner_id)
        else:
            res_id = int(first.res_id) if first.res_id and first.res_id.isdigit() else False
            wa = self.sudo().send_message(res_id=res_id, res_model=first.res_model, phone_number=first.phone_number, text=message)
            if wa:
                wa.conversation_token = conversation_token
        return {'last_message_id': wa.id if wa else 0}

    @api.model
    def reply_to_conversation_with_media(self, conversation_token, message, upload):
        """زي reply_to_conversation، لكن لملف (صورة/تسجيل صوتي/مستند) بعته
        الزائر من ويدجت الموقع. `upload` عبارة عن werkzeug FileStorage جاي
        من request.httprequest.files."""
        if not conversation_token:
            raise ValidationError(_("Invalid conversation"))
        first = self.sudo().search(
            [('conversation_token', '=', conversation_token)], limit=1, order='create_date ASC')
        if not first:
            raise ValidationError(_("Conversation not found"))

        data = upload.read()
        if not data:
            raise ValidationError(_("Empty file"))
        media_b64 = base64.b64encode(data)
        filename = upload.filename or _("attachment")
        mimetype = upload.mimetype or upload.content_type or 'application/octet-stream'

        channel = first.discuss_channel_id
        if channel and channel.wa_internal_only:
            # زائر مجهول (مفيش رقم واتساب حقيقي) - المحادثة كاملة جوه
            # الموقع/Discuss بس، من غير أي إرسال فعلي عن طريق Evolution API.
            wa = self.sudo().create({
                'res_id': first.res_id,
                'res_model': first.res_model,
                'message_content': message or '',
                'direction': 'inbound',
                'conversation_token': conversation_token,
                'status': 'read',
                'company_id': self.env.user.company_id.id,
                'discuss_channel_id': channel.id,
                'media_data': media_b64,
                'media_filename': filename,
                'media_mimetype': mimetype,
            })
            attachment = self.env['ir.attachment'].sudo().create({
                'name': filename,
                'datas': media_b64,
                'mimetype': mimetype,
                'res_model': 'discuss.channel',
                'res_id': channel.id,
            })
            channel.sudo().with_context(wa_skip_forward=True).message_post(
                body=message or '',
                author_id=channel.wa_visitor_partner_id.id,
                message_type='comment',
                subtype_xmlid='mail.mt_comment',
                attachment_ids=[attachment.id],
            )
        else:
            # محادثة برقم واتساب حقيقي - يتبعت فعليًا عن طريق Evolution API
            # (نفس مسار send_message_media)، والعملية دي كمان بتخزن نسخة
            # محلية (media_data) عشان تتعرض صح في نفس لحظتها في الويدجت.
            res_id = int(first.res_id) if first.res_id and first.res_id.isdigit() else False
            attachment = self.env['ir.attachment'].sudo().create({
                'name': filename,
                'datas': media_b64,
                'mimetype': mimetype,
                'res_model': first.res_model or False,
                'res_id': res_id or 0,
            })
            wa = self.sudo().send_message_media(
                res_id=res_id, res_model=first.res_model, phone_number=first.phone_number,
                attachment=attachment, caption=message or '')
            wa.conversation_token = conversation_token
        return {'last_message_id': wa.id if wa else 0}

    def get_account(self):
        """Returns the whatsapp.account configured for the current user's
        company. Credentials live on whatsapp.account (WhatsApp >
        Configuration > Accounts): server_url / instance_name / api_key
        for an Evolution API instance."""
        company = self.env.user.company_id
        account = self.env['whatsapp.account'].sudo().search([('company_ids', 'in', company.id)], limit=1)
        if not account:
            raise ValidationError(_(
                "No WhatsApp Business Account configured for company %s. "
                "Go to WhatsApp > Configuration > Accounts."
            ) % company.name)
        return account

    def sync_all_wa_group_names(self):
        """Backfills real names for every WhatsApp group already known to
        Odoo whose partner is still stuck on the raw group JID as its name
        - called from the 'Sync Group Names' button on whatsapp.account, so
        existing groups don't have to wait for a brand new message to come
        in before showing up correctly. Returns the number of groups
        renamed."""
        try:
            account = self.get_account()
        except ValidationError:
            return 0
        if not account.server_url or not account.instance_name or not account.api_key:
            return 0
        try:
            url = account._evo_instance_url("group/fetchAllGroups")
            response = requests.get(
                url, headers=account._evo_headers(), params={'getParticipants': 'false'}, timeout=20)
            data = response.json() if response.content else []
        except (requests.RequestException, ValueError):
            return 0
        if account.developer_mode:
            _logger.info("WhatsApp group/fetchAllGroups (sync all) status=%s response=%s",
                         response.status_code, data)
        if response.status_code != 200 or not isinstance(data, list):
            return 0
        Partner = self.env['res.partner'].sudo()
        has_mobile = 'mobile' in Partner._fields
        renamed = 0
        for group in data:
            jid = group.get('id') or group.get('remoteJid') or group.get('jid')
            name = group.get('subject') or group.get('name')
            if not jid or '@g.us' not in jid or not name:
                continue
            phone_number = self.normalize_phone(jid)
            domain = [('phone', '=', phone_number)]
            if has_mobile:
                domain = ['|', ('mobile', '=', phone_number)] + domain
            partner = Partner.search(domain, limit=1)
            if partner and partner.name == phone_number and name != phone_number:
                partner.name = name
                renamed += 1
        return renamed

    def action_sync_all_chats_and_contacts(self):
        """بيجيب كل جهات الاتصال (Evolution POST /chat/findContacts/{instance})
        وكل المحادثات (POST /chat/findChats/{instance}) من السيرفر، ويستوردهم
        لأودو مرة واحدة - مفيدة بعد ربط جهاز جديد عشان المحادثات القديمة تظهر
        فورًا من غير ما نستنى رسالة جديدة توصل لكل واحد فيهم.

        - كل contact بيتحول partner (حتى لو معهوش أي رسالة أبدًا).
        - كل chat بيتجاب التاريخ بتاعه عن طريق sync_history_from_whatsapp()
          الموجودة بالفعل.

        Best-effort بالكامل: أي contact/chat واحد يفشل معالجته (شبكة، بيانات
        ناقصة، إلخ) بيتخطى ويكمل على الباقي، ومبيوقفش العملية كلها. بيرجع
        dict فيه عدد كل حاجة اتعملها: {'contacts': n, 'chats': n, 'messages': n}."""
        self = self.sudo()
        account = self.get_account()
        if not (account.server_url and account.instance_name and account.api_key):
            raise ValidationError(_("Please fill in the Server URL, Instance Name and API Key first."))
        config = self.get_config()
        headers = self._evo_headers(config)

        def _is_skippable_jid(jid):
            return (not jid) or jid.endswith('@broadcast') or jid == 'status@broadcast'

        # ------------------------------------------------------------
        # 1) Contacts -> res.partner (even with zero messages so far)
        # ------------------------------------------------------------
        contacts_imported = 0
        try:
            contacts_url = self._evo_url(config, "chat/findContacts/%s" % config['instance_name'])
            response = requests.post(contacts_url, json={}, headers=headers, timeout=30)
            contacts_data = response.json() if response.content else []
        except (requests.RequestException, ValueError):
            contacts_data = []
        if config.get('developer_mode'):
            _logger.info("WhatsApp findContacts response=%s", contacts_data)
        if isinstance(contacts_data, list):
            for contact in contacts_data:
                jid = contact.get('id') or contact.get('remoteJid') or contact.get('jid')
                if _is_skippable_jid(jid) or '@g.us' in jid:
                    # Groups are handled through the chats loop below (and
                    # sync_all_wa_group_names for renaming) - a group isn't
                    # a "contact" with its own phone number.
                    continue
                push_name = contact.get('pushName') or contact.get('name') or contact.get('notify')
                phone_number = self.normalize_phone(jid, account.default_country_code)
                if not phone_number:
                    continue
                try:
                    self.with_context(
                        wa_push_name=push_name, wa_remote_jid=jid
                    )._get_or_create_wa_partner(phone_number)
                    contacts_imported += 1
                except Exception:
                    _logger.exception("Failed importing WhatsApp contact %s", jid)

        # ------------------------------------------------------------
        # 2) Chats -> message history via the existing per-number importer
        # ------------------------------------------------------------
        chats_imported = 0
        messages_imported = 0
        try:
            chats_url = self._evo_url(config, "chat/findChats/%s" % config['instance_name'])
            response = requests.post(chats_url, json={}, headers=headers, timeout=30)
            chats_data = response.json() if response.content else []
        except (requests.RequestException, ValueError):
            chats_data = []
        if config.get('developer_mode'):
            _logger.info("WhatsApp findChats response=%s", chats_data)
        if isinstance(chats_data, list):
            for chat in chats_data:
                jid = chat.get('id') or chat.get('remoteJid') or chat.get('jid')
                if _is_skippable_jid(jid):
                    continue
                phone_number = self.normalize_phone(jid, account.default_country_code)
                if not phone_number:
                    continue
                chats_imported += 1
                try:
                    messages_imported += self.sync_history_from_whatsapp(phone_number, limit=100)
                except Exception:
                    _logger.exception("Failed importing WhatsApp chat history for %s", jid)

        return {'contacts': contacts_imported, 'chats': chats_imported, 'messages': messages_imported}

    def get_config(self):
        """Returns a plain dict snapshot of the current whatsapp.account's
        Evolution API credentials (server_url/instance_name/api_key/...) -
        used by send_message()/send_message_media()/send_reaction()/
        sync_history_from_whatsapp() together with _evo_url()/_evo_headers()
        below. NOTE: this method was previously missing entirely (its `def`
        line had been dropped, leaving this dict-building code as dead,
        unreachable code at the end of sync_all_wa_group_names()), which
        made every one of those methods raise AttributeError: 'wa.message'
        object has no attribute 'get_config' as soon as they were called."""
        account = self.get_account()
        return {
            'account_id': account.id,
            'server_url': account.server_url,
            'instance_name': account.instance_name,
            'api_key': account.api_key,
            'webhook_url': account.callback_url,
            'developer_mode': account.developer_mode,
        }

    def _evo_headers(self, config):
        return {
            'apikey': config['api_key'] or '',
            'Content-Type': "application/json",
        }

    def _evo_url(self, config, path):
        if not config.get('server_url'):
            raise ValidationError(_("WhatsApp Account has no Server URL configured."))
        return "%s/%s" % (config['server_url'].rstrip('/'), path.lstrip('/'))

    def schedule_error_activity(self, error_message):
        active_rec = self.env[self.res_model].browse(int(self.res_id)) if self.res_model and self.res_id else self.env[self._name]
        config = self.env['wa.message.model.adaptation'].search([('model_id.model', '=', self.res_model)])
        if not config:
            raise ValidationError(_("There is no model adaptation config for ") + self._name)
        for rec in active_rec:
            user = False
            if config.activity_user_field_id:
                user = rec[config.activity_user_field_id.name]
            if not user:
                user = config.activity_default_user_id
            if not user:
                user = self.env.user
            rec.activity_schedule('odoo_whatsapp_api.message_error_activity', user_id=user.id, note=error_message, date_deadline=fields.Date.today())

    @api.model
    def _extract_wa_error(self, message):
        """Human readable error string from an Evolution API error response
        (shape varies: {'message': ...} or {'error': ...} or {'response': {'message': [...]}})."""
        if not message:
            return ''
        if isinstance(message, str):
            return message
        parts = []
        if isinstance(message.get('message'), str):
            parts.append(message['message'])
        elif isinstance(message.get('message'), list):
            parts.extend(str(m) for m in message['message'])
        if isinstance(message.get('error'), str):
            parts.append(message['error'])
        response = message.get('response')
        if isinstance(response, dict) and response.get('message'):
            rm = response['message']
            parts.extend(rm if isinstance(rm, list) else [rm])
        return '  ||  '.join(str(p) for p in parts) or 'Unknown error'

    @api.depends('status')
    def log_note(self):
        for record in self:
            if not record.json_response and not record.json_payload:
                record.trigger_log_note = False if record.trigger_log_note else True
                continue
            active_rec = self.env[record.res_model].browse(int(record.res_id)) if record.res_model and record.res_id else self.env[self._name]
            for rec in active_rec:
                if record.status:
                    message_text = f"WhatsApp Message {dict(self._fields['status']._description_selection(self.env)).get(record.status)} | "
                else:
                    message_text = ''
                response = json.loads(record.json_response) if record.json_response else {}
                payload = json.loads(record.json_payload) if record.json_payload else {}
                if payload:
                    message_text += payload.get('number', '') + "<br/>"
                if record.message_content:
                    message_text += "<br/> Content: " + record.message_content
                if record.status == 'failed':
                    error_message = self._extract_wa_error(response)
                    self.schedule_error_activity(error_message)
                if record.mail_message_id:
                    record.mail_message_id.body = message_text
                else:
                    new_message = rec.message_post(body=message_text)
                    record.mail_message_id = new_message.id
            record.trigger_log_note = False if record.trigger_log_note else True
    trigger_log_note = fields.Boolean(compute=log_note, store=True)

    def send_message(self, res_id, res_model, phone_number, text, post_to_channel=True,
                      quoted=None, quoted_sender_name=False, quoted_preview=False):
        config = self.get_config()
        account = self.env['whatsapp.account'].sudo().browse(config.get('account_id'))
        country_code = account.default_country_code if account else False
        url = self._evo_url(config, "message/sendText/%s" % config['instance_name'])
        headers = self._evo_headers(config)

        candidates = self._wa_phone_candidates(phone_number, country_code)
        response = None
        response_data = {}
        payload = {}
        payload_json = ''
        sent_number = candidates[0] if candidates else phone_number

        for index, candidate in enumerate(candidates):
            payload = {"number": candidate, "text": text}
            if quoted:
                # بتخلي واتساب يعرض الرسالة دي كرد على رسالة تانية - نفس
                # اللي بيحصل لما ترد من التليفون بالظبط.
                payload["quoted"] = quoted
            payload_json = json.dumps(payload)
            response = requests.post(url, json=payload, headers=headers, timeout=20)
            try:
                response_data = response.json()
            except ValueError:
                response_data = {}
            if config.get('developer_mode'):
                _logger.info("WhatsApp send_message payload=%s response=%s", payload_json, response.text)
            sent_number = candidate
            if response.status_code in (200, 201):
                break
            is_last_candidate = index == len(candidates) - 1
            if self._is_number_not_exists_error(response_data) and not is_last_candidate:
                # This format wasn't a valid WhatsApp JID - try the next
                # candidate instead of giving up immediately.
                continue
            break
        dialog_message = False
        if response.status_code in (200, 201):
            status = 'sent'
            dialog_message = (response_data.get('key') or {}).get('id')
        else:
            status = 'failed'
        message_vals = {
            'res_id': res_id,
            'res_model': res_model,
            'status_code': response.status_code,
            'status': status,
            'dialog_message_id': dialog_message,
            'json_payload': payload_json,
            'json_response': json.dumps(response_data),
            'company_id': self.env.user.company_id.id,
            'account_id': config.get('account_id'),
            'message_content': text,
            'direction': 'outbound',
            'phone_number': self.normalize_phone(sent_number),
            'wa_quoted_wamid': (quoted or {}).get('key', {}).get('id') or False,
            'wa_quoted_sender_name': quoted_sender_name or False,
            'wa_quoted_preview': quoted_preview or False,
        }
        wa = self.env['wa.message'].create(message_vals)
        if post_to_channel:
            channel = self._get_or_create_wa_channel(sent_number, res_model, res_id)
            wa.discuss_channel_id = channel.id
            self._post_wa_message_to_channel(channel, text, 'outbound', sent_number)
        return wa

    def send_message_template(self, res_id, res_model, phone_number, template_id, post_to_channel=True):
        """Evolution API (Baileys) has no server-side approved templates like
        Meta Cloud API - so we render the template locally (same placeholder
        substitution as before) and send it as a regular text message."""
        text = template_id.get_sending_txt(template_id.get_params_values(res_id))
        if template_id.footer_message:
            text += '\n\n' + template_id.footer_message
        wa = self.send_message(res_id=res_id, res_model=res_model, phone_number=phone_number, text=text,
                                post_to_channel=post_to_channel)
        wa.wa_message_template_id = template_id.id
        return wa


    @api.model
    def _wa_media_type(self, mimetype):
        """Evolution's sendMedia endpoint wants a coarse mediatype
        (image/video/audio/document), not the raw mimetype."""
        mimetype = (mimetype or '').lower()
        if mimetype.startswith('image/'):
            return 'image'
        if mimetype.startswith('video/'):
            return 'video'
        if mimetype.startswith('audio/'):
            return 'audio'
        return 'document'

    def send_message_media(self, res_id, res_model, phone_number, attachment, caption='', post_to_channel=True,
                            quoted=None, quoted_sender_name=False, quoted_preview=False):
        """Sends one ir.attachment (PDF, image, ...) as a real WhatsApp
        media message via Evolution API (POST message/sendMedia/{instance}),
        e.g. the invoice PDF attached from a record's chatter 'WhatsApp'
        button. Same phone-candidate retry logic as send_message()."""
        config = self.get_config()
        account = self.env['whatsapp.account'].sudo().browse(config.get('account_id'))
        country_code = account.default_country_code if account else False
        url = self._evo_url(config, "message/sendMedia/%s" % config['instance_name'])
        headers = self._evo_headers(config)

        candidates = self._wa_phone_candidates(phone_number, country_code)
        mimetype = attachment.mimetype or 'application/octet-stream'
        media_type = self._wa_media_type(mimetype)
        raw_datas = attachment.datas or b''
        media_b64 = raw_datas.decode() if isinstance(raw_datas, bytes) else raw_datas

        response = None
        response_data = {}
        payload = {}
        payload_json = ''
        sent_number = candidates[0] if candidates else phone_number

        for index, candidate in enumerate(candidates):
            payload = {
                "number": candidate,
                "mediatype": media_type,
                "mimetype": mimetype,
                "media": media_b64,
                "fileName": attachment.name or _("attachment"),
            }
            if caption:
                payload["caption"] = caption
            if quoted:
                payload["quoted"] = quoted
            response = requests.post(url, json=payload, headers=headers)
            try:
                response_data = response.json()
            except ValueError:
                response_data = {}
            # Never log the base64 payload itself - just its shape.
            payload_json = json.dumps({k: v for k, v in payload.items() if k != 'media'})
            if config.get('developer_mode'):
                _logger.info("WhatsApp send_message_media payload=%s response=%s", payload_json, response.text)
            sent_number = candidate
            if response.status_code in (200, 201):
                break
            is_last_candidate = index == len(candidates) - 1
            if self._is_number_not_exists_error(response_data) and not is_last_candidate:
                continue
            break

        dialog_message = False
        if response.status_code in (200, 201):
            status = 'sent'
            dialog_message = (response_data.get('key') or {}).get('id')
        else:
            status = 'failed'
        message_vals = {
            'res_id': res_id,
            'res_model': res_model,
            'status_code': response.status_code,
            'status': status,
            'dialog_message_id': dialog_message,
            'json_payload': payload_json,
            'json_response': json.dumps(response_data),
            'company_id': self.env.user.company_id.id,
            'account_id': config.get('account_id'),
            'message_content': caption or (_("[%s] %s") % (media_type, attachment.name or '')),
            'direction': 'outbound',
            'phone_number': self.normalize_phone(sent_number),
            # Keep a local copy so get_conversation() can build a media_url
            # for the website widget - without this the message reaches
            # WhatsApp fine but shows as an empty bubble in the widget.
            'media_data': attachment.datas,
            'media_filename': attachment.name,
            'media_mimetype': mimetype,
            'wa_quoted_wamid': (quoted or {}).get('key', {}).get('id') or False,
            'wa_quoted_sender_name': quoted_sender_name or False,
            'wa_quoted_preview': quoted_preview or False,
        }
        wa = self.env['wa.message'].create(message_vals)
        if post_to_channel:
            channel = self._get_or_create_wa_channel(sent_number, res_model, res_id)
            wa.discuss_channel_id = channel.id
            self._post_wa_message_to_channel(channel, wa.message_content, 'outbound', sent_number)
        return wa

    # ------------------------------------------------------------------
    # Reactions / Forward / History - بتخدم شاشة الشات الجديدة (شاشة
    # واتساب الداخلية) عن طريق wrappers في controllers/wa_message_fullview.py
    # ------------------------------------------------------------------
    def send_reaction(self, message_id, emoji):
        """بتبعت (أو تشيل، لو emoji فاضي) تفاعل بإيموجي على رسالة اتبعتت
        أو اتستلمت فعليًا من واتساب (POST message/sendReaction/{instance}).
        بيحتاج wamid حقيقي (dialog_message_id) - مينفعش تتفاعل على رسالة
        لسه ماتبعتتش أو فشلت."""
        target = self.browse(int(message_id))
        if not target.exists() or not target.dialog_message_id:
            raise ValidationError(_("This message hasn't been sent through WhatsApp yet."))
        config = self.get_config()
        url = self._evo_url(config, "message/sendReaction/%s" % config['instance_name'])
        headers = self._evo_headers(config)
        jid = self.normalize_phone(target.phone_number)
        if '@' not in jid:
            # target.phone_number is stored without any @-suffix
            # (normalize_phone strips it on the way in), so it has to be
            # reconstructed here. Hardcoding "@s.whatsapp.net" unconditionally
            # was wrong for a group message: WhatsApp group JIDs are 18-20
            # digits and MUST end in "@g.us" or Evolution/Baileys rejects
            # the reaction (sender not found in that "individual" chat) -
            # this is why reacting inside a group conversation was failing.
            # wa_is_group on the message itself is the source of truth
            # rather than re-guessing from digit length here.
            jid = "%s@g.us" % jid if target.wa_is_group else "%s@s.whatsapp.net" % jid
        payload = {
            "key": {
                "remoteJid": jid,
                "fromMe": target.direction == 'outbound',
                "id": target.dialog_message_id,
            },
            "reaction": emoji or "",
        }
        response = requests.post(url, json=payload, headers=headers, timeout=15)
        try:
            response_data = response.json()
        except ValueError:
            response_data = {}
        if config.get('developer_mode'):
            _logger.info("WhatsApp send_reaction payload=%s response=%s", payload, response.text)
        if response.status_code not in (200, 201):
            raise ValidationError(_("Could not send reaction: %s") % self._extract_wa_error(response_data))
        # تحديث محلي فوري - نفس منطق dialog_message_id في send_message،
        # الـ echo اللي هيجي من الويبهوك بعد كده هيحدّث بنفس القيمة
        # (idempotent) فمفيش خطر تكرار.
        try:
            data = json.loads(target.reactions_json) if target.reactions_json else {}
        except (ValueError, TypeError):
            data = {}
        if emoji:
            data['__me__'] = emoji
        else:
            data.pop('__me__', None)
        target.reactions_json = json.dumps(data)
        return target

    def forward_message(self, message_id, to_phone_number):
        """Evolution API مالهاش endpoint فورورد حقيقي زي واتساب نفسه (يفضل
        شايف إنها "Forwarded")؛ فبنعيد بعت نفس المحتوى (نص أو ميديا) كرسالة
        جديدة عادية للرقم التاني."""
        source = self.browse(int(message_id))
        if not source.exists():
            raise ValidationError(_("Message not found."))
        if not (to_phone_number or '').strip():
            raise ValidationError(_("Choose a conversation/number to forward to."))
        if source.media_data:
            attachment = self.env['ir.attachment'].sudo().create({
                'name': source.media_filename or 'file',
                'datas': source.media_data,
                'mimetype': source.media_mimetype or 'application/octet-stream',
            })
            wa = self.send_message_media(
                res_id=False, res_model=False, phone_number=to_phone_number,
                attachment=attachment, caption=(source.message_content or ' '),
                post_to_channel=False)
        else:
            text = (source.message_content or '').strip()
            if not text:
                raise ValidationError(_("Nothing to forward in this message."))
            wa = self.send_message(res_id=False, res_model=False, phone_number=to_phone_number,
                                    text=text, post_to_channel=False)
        return wa

    def sync_history_from_whatsapp(self, phone_number, limit=50):
        """Best-effort: بيجيب رسايل قديمة من واتساب نفسه (Evolution API,
        POST /chat/findMessages/{instance}) لو مش موجودة عندنا في أودو
        أصلاً - غالبًا محادثة قديمة قبل ما الـ webhook يتظبط. بيتجاهل أي
        رسالة الـ wamid بتاعها موجود عندنا بالفعل. بيرجع عدد اللي اتضافوا.

        ملحوظة: شكل الـ endpoint ده بيختلف شوية بين نسخ Evolution API
        (بعضها بيرجع {"messages": {"records": [...]}}, وبعضها {"messages":
        [...]} على طول) - الكود بيتعامل مع الشكلين. لو السيرفر عندك بيرجع
        حاجة تالتة أو الـ endpoint مش متاح أصلاً، الدالة بترجع 0 من غير ما
        تبوّظ حاجة - جرّب توثيق Evolution API بتاعتك لو الاسم مختلف."""
        try:
            account = self.get_account()
        except ValidationError:
            return 0
        config = self.get_config()
        if not (config.get('server_url') and config.get('instance_name')):
            return 0
        # A WhatsApp group JID (@g.us) must never be run through the phone
        # number candidate/country-code guessing below - it isn't a phone
        # number - and needs the @g.us suffix, not @s.whatsapp.net, or
        # Evolution is asked about a JID that doesn't exist and always
        # returns 0 results.
        is_group = self.sudo().search_count(
            [('phone_number', '=', phone_number), ('wa_is_group', '=', True)]) > 0 or len(phone_number or '') >= 16
        if is_group:
            candidates = [phone_number]
        else:
            candidates = self._wa_phone_candidates(phone_number, account.default_country_code)
        url = self._evo_url(config, "chat/findMessages/%s" % config['instance_name'])
        headers = self._evo_headers(config)
        imported = 0
        for candidate in candidates:
            if '@' in candidate:
                jid = candidate
            elif is_group:
                jid = "%s@g.us" % candidate
            else:
                jid = "%s@s.whatsapp.net" % candidate
            payload = {"where": {"key": {"remoteJid": jid}}, "limit": int(limit)}
            try:
                response = requests.post(url, json=payload, headers=headers, timeout=20)
                data = response.json() if response.content else {}
            except (requests.RequestException, ValueError):
                continue
            if config.get('developer_mode'):
                _logger.info("WhatsApp sync_history payload=%s response=%s", payload, data)
            if response.status_code not in (200, 201):
                continue
            messages_field = data.get('messages')
            if isinstance(messages_field, dict):
                records = messages_field.get('records') or []
            elif isinstance(messages_field, list):
                records = messages_field
            elif isinstance(data, list):
                records = data
            else:
                records = []
            for item in records:
                key = (item or {}).get('key') or {}
                wamid = key.get('id')
                if not wamid or self.sudo().search_count([('dialog_message_id', '=', wamid)]):
                    continue
                from_me = bool(key.get('fromMe'))
                wa = self.sudo()._process_incoming_message(item, False, from_me=from_me, is_history=True)
                if wa:
                    imported += 1
            if records:
                break
        return imported

class DiscussChannelWhatsApp(models.Model):
    """Bridges Discuss group chats back to WhatsApp: replying to a WhatsApp
    conversation from Discuss (or the chat bubble/messaging icon) sends the
    reply out through Evolution API automatically."""
    _inherit = 'discuss.channel'

    wa_phone_number = fields.Char(index=True, string="WhatsApp Number")
    wa_res_model = fields.Char(string="WhatsApp Related Model")
    wa_res_id = fields.Char(string="WhatsApp Related Record Id")
    wa_conversation_token = fields.Char(
        index=True, string="Website Conversation Token",
        help="Set only for anonymous website-widget visitors with no real WhatsApp "
             "number - links this channel to the wa.message records the widget polls on.")
    wa_internal_only = fields.Boolean(
        string="Website-Only Conversation",
        help="True for anonymous website visitors: no real WhatsApp number, so replies "
             "typed here never go out through Evolution API - they only get mirrored "
             "back into the website widget.")
    wa_visitor_partner_id = fields.Many2one(
        'res.partner', string="Website Visitor",
        help="Partner used as the message author for the visitor side of an "
             "internal-only website conversation (no phone number to dedupe on).")
    wa_bot_active = fields.Boolean(
        default=True, string="Auto-Reply Active",
        help="True while the auto-reply template is still allowed to answer for this "
             "conversation. Turned off automatically the moment a human replies - either "
             "an operator from Odoo (Discuss/chatter) or the phone owner directly from "
             "the linked device.")

    def message_post(self, *, message_type='notification', **kwargs):
        message = super().message_post(message_type=message_type, **kwargs)
        # wa_skip_forward guards against the echo we post ourselves right
        # above (inbound/outbound mirror) from being forwarded back out.
        if message_type != 'comment' or self.env.context.get('wa_skip_forward'):
            return message
        body = kwargs.get('body') or ''
        text = tools.html2plaintext(body) if body else ''
        # Any file (image, video, PDF invoice, ...) attached to this Discuss
        # message - dropped in the chat composer or via the paperclip icon.
        attachments = message.attachment_ids
        if not text and not attachments:
            return message

        if self.wa_internal_only and self.wa_conversation_token:
            # Anonymous website visitor: never call Evolution API - just
            # record the operator's reply under the same conversation_token
            # so the website widget's polling picks it up. The widget
            # renders both text and media, so when there's an attachment we
            # put the typed text as its caption (matching how a real
            # WhatsApp media message with a caption looks) instead of
            # creating a separate, orphaned text-only bubble.
            base_vals = {
                'res_id': self.wa_res_id,
                'res_model': self.wa_res_model,
                'direction': 'outbound',
                'conversation_token': self.wa_conversation_token,
                'status': 'sent',
                'company_id': self.env.user.company_id.id,
                'discuss_channel_id': self.id,
            }
            if attachments:
                # First attachment carries the typed text as its caption,
                # exactly like the real-phone-number branch below does.
                first_attachment, extra_attachments = attachments[0], attachments[1:]
                _logger.info(
                    "WhatsApp widget media forward: attachment=%s mimetype=%s has_datas=%s",
                    first_attachment.name, first_attachment.mimetype, bool(first_attachment.datas))
                wa_record = self.env['wa.message'].sudo().with_context(wa_skip_forward=True).create({
                    **base_vals,
                    'message_content': text or '',
                    'media_data': first_attachment.datas,
                    'media_filename': first_attachment.name,
                    'media_mimetype': first_attachment.mimetype,
                })
                _logger.info(
                    "WhatsApp widget media forward: created wa.message id=%s has_media_data_after_create=%s",
                    wa_record.id, bool(wa_record.media_data))
                for attachment in extra_attachments:
                    _logger.info(
                        "WhatsApp widget media forward: attachment=%s mimetype=%s has_datas=%s",
                        attachment.name, attachment.mimetype, bool(attachment.datas))
                    self.env['wa.message'].sudo().with_context(wa_skip_forward=True).create({
                        **base_vals,
                        'message_content': '',
                        'media_data': attachment.datas,
                        'media_filename': attachment.name,
                        'media_mimetype': attachment.mimetype,
                    })
            elif text:
                self.env['wa.message'].sudo().with_context(wa_skip_forward=True).create({
                    **base_vals,
                    'message_content': text,
                })
        elif self.wa_phone_number:
            # An operator is replying for real from Odoo (Discuss/chatter) -
            # a human has taken over, so the auto-reply bot must stop
            # answering for this conversation from now on.
            if self.wa_bot_active:
                self.wa_bot_active = False
            res_id = int(self.wa_res_id) if self.wa_res_id and self.wa_res_id.isdigit() else False
            # Carry over the conversation_token this phone number's widget
            # session is polling on, so the reply actually shows up there
            # instead of only being sent to the real WhatsApp number.
            token = self.env['wa.message'].sudo()._get_conversation_token(self.wa_phone_number)
            wa_records = self.env['wa.message']
            if attachments:
                # Put the typed text as the caption of the first file so it
                # doesn't go out as a separate, redundant text message.
                first_attachment, extra_attachments = attachments[0], attachments[1:]
                wa_records |= self.env['wa.message'].sudo().with_context(wa_skip_forward=True).send_message_media(
                    res_id=res_id,
                    res_model=self.wa_res_model,
                    phone_number=self.wa_phone_number,
                    attachment=first_attachment,
                    caption=text,
                    post_to_channel=False,
                )
                for attachment in extra_attachments:
                    wa_records |= self.env['wa.message'].sudo().with_context(wa_skip_forward=True).send_message_media(
                        res_id=res_id,
                        res_model=self.wa_res_model,
                        phone_number=self.wa_phone_number,
                        attachment=attachment,
                        post_to_channel=False,
                    )
            elif text:
                wa_records |= self.env['wa.message'].sudo().with_context(wa_skip_forward=True).send_message(
                    res_id=res_id,
                    res_model=self.wa_res_model,
                    phone_number=self.wa_phone_number,
                    text=text,
                    post_to_channel=False,
                )
            if token:
                for wa in wa_records:
                    wa.conversation_token = token
        return message