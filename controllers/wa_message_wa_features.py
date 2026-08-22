# -*- coding: utf-8 -*-
"""
امتداد تاني على wa.message (زي wa_message_fullview.py بالظبط - _inherit
بس، مفيش _name) - بيضيف أنواع رسايل واتساب اللي كانت بس بتتستقبل قبل
كده (location/contact/poll/sticker...) كأول مرة نقدر نبعتها، بالإضافة
لـ:
  - تعليم الرسايل "مقروءة" فعليًا عند العميل (التيك الأزرق) مش جوه
    أودو بس.
  - "بيكتب الآن..." (typing presence).
  - تعديل/حذف "لدى الجميع" لرسالة صادرة بعتناها احنا.

كل دول بيستخدموا نفس الحساب/الـ instance المتظبط أصلاً في
whatsapp.account (get_config() الأصلية) - مفيش إعدادات جديدة تتحط.

ملحوظة مهمة عن sendList/sendButtons: واتساب وقف دعم الرسايل التفاعلية
دي (قوايم/أزرار) على اتصال Baileys (اللي Evolution شغال بيه هنا) في
نسخ واتساب الحديثة - غالبًا هترجع من Evolution برضو بس مش هتوصل
للعميل فعليًا كأزرار. سيبناها موجودة في الكود (بعض نسخ Evolution/بعض
الأرقام لسه بيقبلوها) بس استخدم send_poll كبديل أضمن لو الهدف "خلي
العميل يختار من كذا اختيار".
"""
import json
import logging

import requests

from odoo import models, api, _
from odoo.exceptions import ValidationError

_logger = logging.getLogger(__name__)


class WaMessageWaFeatures(models.Model):
    _inherit = 'wa.message'

    # ------------------------------------------------------------------
    # Helper مشترك - نفس منطق send_message() بالظبط (تجربة أكتر من صيغة
    # للرقم، تسجيل الرسالة في wa.message، صدى في Discuss) بس من غير ما
    # نكرر اللوب ده في كل دالة إرسال جديدة تحت.
    # ------------------------------------------------------------------
    def _send_evo(self, path, payload_builder, res_id, res_model, phone_number,
                   message_content, extra_vals=None, post_to_channel=True):
        config = self.get_config()
        account = self.env['whatsapp.account'].sudo().browse(config.get('account_id'))
        country_code = account.default_country_code if account else False
        url = self._evo_url(config, "%s/%s" % (path, config['instance_name']))
        headers = self._evo_headers(config)

        candidates = self._wa_phone_candidates(phone_number, country_code)
        response = None
        response_data = {}
        payload = {}
        payload_json = ''
        sent_number = candidates[0] if candidates else phone_number

        for index, candidate in enumerate(candidates):
            payload = payload_builder(candidate)
            payload_json = json.dumps(payload)
            response = requests.post(url, json=payload, headers=headers, timeout=20)
            try:
                response_data = response.json()
            except ValueError:
                response_data = {}
            if config.get('developer_mode'):
                _logger.info("WhatsApp %s payload=%s response=%s", path, payload_json, response.text)
            sent_number = candidate
            if response.status_code in (200, 201):
                break
            is_last_candidate = index == len(candidates) - 1
            if self._is_number_not_exists_error(response_data) and not is_last_candidate:
                continue
            break

        if response is not None and response.status_code in (200, 201):
            status = 'sent'
            dialog_message = (response_data.get('key') or {}).get('id')
        else:
            status = 'failed'
            dialog_message = False

        message_vals = {
            'res_id': res_id,
            'res_model': res_model,
            'status_code': response.status_code if response is not None else False,
            'status': status,
            'dialog_message_id': dialog_message,
            'json_payload': payload_json,
            'json_response': json.dumps(response_data),
            'company_id': self.env.user.company_id.id,
            'account_id': config.get('account_id'),
            'message_content': message_content,
            'direction': 'outbound',
            'phone_number': self.normalize_phone(sent_number),
        }
        if extra_vals:
            message_vals.update(extra_vals)
        wa = self.env['wa.message'].create(message_vals)
        if post_to_channel:
            channel = self._get_or_create_wa_channel(sent_number, res_model, res_id)
            wa.discuss_channel_id = channel.id
            self._post_wa_message_to_channel(channel, message_content, 'outbound', sent_number)
        return wa

    # ------------------------------------------------------------------
    # أنواع رسايل جديدة (كانت بس بتتستقبل - دلوقتي بقى نقدر نبعتها)
    # ------------------------------------------------------------------
    def send_location(self, res_id, res_model, phone_number, latitude, longitude,
                        name='', address='', post_to_channel=True):
        def build(candidate):
            payload = {"number": candidate, "latitude": float(latitude), "longitude": float(longitude)}
            if name:
                payload["name"] = name
            if address:
                payload["address"] = address
            return payload
        label = ' - '.join(p for p in (_('📍 موقع'), name, address) if p)
        return self._send_evo("message/sendLocation", build, res_id, res_model, phone_number,
                                label or _('📍 موقع'), post_to_channel=post_to_channel)

    def send_contact_card(self, res_id, res_model, phone_number, contact_name, contact_phone,
                            organization='', post_to_channel=True):
        wuid = self.normalize_phone(contact_phone)

        def build(candidate):
            contact = {"fullName": contact_name, "wuid": wuid, "phoneNumber": contact_phone}
            if organization:
                contact["organization"] = organization
            return {"number": candidate, "contact": [contact]}
        label = _('👤 جهة اتصال: %s') % contact_name
        return self._send_evo("message/sendContact", build, res_id, res_model, phone_number,
                                label, post_to_channel=post_to_channel)

    def send_poll(self, res_id, res_model, phone_number, question, options,
                   selectable_count=1, post_to_channel=True):
        options = [o for o in (options or []) if o]
        if len(options) < 2:
            raise ValidationError(_("Poll needs at least 2 options."))

        def build(candidate):
            return {"number": candidate, "name": question,
                     "selectableCount": int(selectable_count), "values": options}
        lines = [_('📊 استطلاع: %s') % question] + ['• %s' % o for o in options]
        return self._send_evo("message/sendPoll", build, res_id, res_model, phone_number,
                                '\n'.join(lines), post_to_channel=post_to_channel)

    def send_list_message(self, res_id, res_model, phone_number, title, description,
                            button_text, sections, footer_text='', post_to_channel=True):
        """sections: [{'title': str, 'rows': [{'title': str, 'description': str,
        'rowId': str}, ...]}, ...] - شوف ملحوظة sendList/sendButtons فوق."""
        def build(candidate):
            payload = {"number": candidate, "title": title, "description": description,
                        "buttonText": button_text, "sections": sections}
            if footer_text:
                payload["footerText"] = footer_text
            return payload
        label = _('📋 قائمة: %s') % title
        return self._send_evo("message/sendList", build, res_id, res_model, phone_number,
                                label, post_to_channel=post_to_channel)

    def send_buttons_message(self, res_id, res_model, phone_number, title, description,
                               buttons, footer='', post_to_channel=True):
        """buttons: [{'type': 'reply', 'displayText': str, 'id': str}, ...] - شوف
        ملحوظة sendList/sendButtons فوق."""
        def build(candidate):
            payload = {"number": candidate, "title": title, "description": description, "buttons": buttons}
            if footer:
                payload["footer"] = footer
            return payload
        label = _('🔘 %s') % title
        return self._send_evo("message/sendButtons", build, res_id, res_model, phone_number,
                                label, post_to_channel=post_to_channel)

    def send_sticker(self, res_id, res_model, phone_number, attachment, post_to_channel=True):
        b64 = attachment.datas.decode() if isinstance(attachment.datas, bytes) else attachment.datas

        def build(candidate):
            return {"number": candidate,
                     "sticker": "data:%s;base64,%s" % (attachment.mimetype or 'image/webp', b64)}
        return self._send_evo(
            "message/sendSticker", build, res_id, res_model, phone_number, _('🩹 ستيكر'),
            extra_vals={'media_data': b64, 'media_filename': attachment.name,
                         'media_mimetype': attachment.mimetype},
            post_to_channel=post_to_channel)

    def send_voice_note(self, res_id, res_model, phone_number, attachment, post_to_channel=True):
        """PTT (push-to-talk) - بتوصل عند العميل كرسالة صوتية زي ما لو
        سجلها بنفسه، مش كملف صوت عادي مرفق (ده الفرق بينها وبين
        send_message_media بميديا-تايب audio)."""
        b64 = attachment.datas.decode() if isinstance(attachment.datas, bytes) else attachment.datas

        def build(candidate):
            return {"number": candidate,
                     "audio": "data:%s;base64,%s" % (attachment.mimetype or 'audio/ogg', b64)}
        return self._send_evo(
            "message/sendWhatsAppAudio", build, res_id, res_model, phone_number, _('🎤 رسالة صوتية'),
            extra_vals={'media_data': b64, 'media_filename': attachment.name,
                         'media_mimetype': attachment.mimetype},
            post_to_channel=post_to_channel)

    # ------------------------------------------------------------------
    # Typing presence / Read receipts فعليين على واتساب
    # ------------------------------------------------------------------
    def send_typing_presence(self, phone_number, state='composing', delay=1200):
        """state: 'composing' (بيكتب...) أو 'recording' (بيسجل رسالة
        صوتية...) أو 'paused'. واتساب بيلغيها تلقائي بعد delay ملي ثانية
        برضو، فمش لازم تبعت 'paused' يدوي في العادي - نداها وانت بس بتبدأ
        تكتب الرد."""
        config = self.get_config()
        account = self.env['whatsapp.account'].sudo().browse(config.get('account_id'))
        country_code = account.default_country_code if account else False
        url = self._evo_url(config, "chat/sendPresence/%s" % config['instance_name'])
        headers = self._evo_headers(config)
        candidates = self._wa_phone_candidates(phone_number, country_code)
        if not candidates:
            return False
        payload = {"number": candidates[0], "delay": int(delay), "presence": state}
        try:
            requests.post(url, json=payload, headers=headers, timeout=10)
        except Exception:
            _logger.exception("Failed sending WhatsApp presence for %s", phone_number)
        return True

    def mark_read_on_whatsapp(self, phone_number, wamids=None):
        """بتعلّم الرسايل كـ 'مقروءة' فعليًا عند العميل (التيك الأزرق) -
        مش بس جوه أودو زي mark_conversation_read الأصلية (اللي بتتحكم في
        البادچ الأخضر بتاعنا احنا بس). من غير wamids، بتاخد آخر ٥ رسايل
        واردة من الرقم ده وعندها dialog_message_id."""
        config = self.get_config()
        if not wamids:
            unread = self.sudo().search([
                ('company_id', '=', self.env.company.id), ('phone_number', '=', phone_number),
                ('direction', '=', 'inbound'), ('dialog_message_id', '!=', False),
            ], order='id desc', limit=5)
            wamids = [m.dialog_message_id for m in unread]
        if not wamids:
            return True
        read_messages = [{"remoteJid": "%s@s.whatsapp.net" % phone_number, "fromMe": False, "id": wamid}
                          for wamid in wamids]
        url = self._evo_url(config, "chat/markMessageAsRead/%s" % config['instance_name'])
        headers = self._evo_headers(config)
        try:
            requests.post(url, json={"readMessages": read_messages}, headers=headers, timeout=10)
        except Exception:
            _logger.exception("Failed marking WhatsApp messages as read for %s", phone_number)
        return True

    # ------------------------------------------------------------------
    # تعديل / حذف "لدى الجميع" لرسالة صادرة بعتناها احنا (مش رسالة
    # الطرف التاني - دي بتتحكم فيها فقط لو is_revoked/protocolMessage جاله
    # هو، شوف _apply_incoming_protocol_message في webhook.py)
    # ------------------------------------------------------------------
    def edit_message(self, message_id, new_text):
        msg = self.sudo().browse(int(message_id))
        if not msg.exists() or msg.direction != 'outbound' or not msg.dialog_message_id:
            raise ValidationError(_("Only your own already-sent messages can be edited."))
        config = self.get_config()
        domain = 'g.us' if msg.wa_is_group else 's.whatsapp.net'
        remote_jid = "%s@%s" % (msg.phone_number, domain)
        account = self.env['whatsapp.account'].sudo().browse(config.get('account_id'))
        country_code = account.default_country_code if account else False
        candidates = self._wa_phone_candidates(msg.phone_number, country_code)
        url = self._evo_url(config, "chat/updateMessage/%s" % config['instance_name'])
        headers = self._evo_headers(config)
        payload = {
            "number": candidates[0] if candidates else msg.phone_number,
            "key": {"remoteJid": remote_jid, "fromMe": True, "id": msg.dialog_message_id},
            "text": new_text,
        }
        response = requests.post(url, json=payload, headers=headers, timeout=20)
        try:
            response_data = response.json() if response.content else {}
        except ValueError:
            response_data = {}
        if response.status_code in (200, 201):
            msg.write({'message_content': new_text, 'is_edited': True})
        else:
            raise ValidationError(_("WhatsApp rejected the edit: %s") % self._extract_wa_error(response_data))
        return msg

    def delete_message_for_everyone(self, message_id):
        msg = self.sudo().browse(int(message_id))
        if not msg.exists() or msg.direction != 'outbound' or not msg.dialog_message_id:
            raise ValidationError(_("Only your own already-sent messages can be deleted for everyone."))
        config = self.get_config()
        domain = 'g.us' if msg.wa_is_group else 's.whatsapp.net'
        remote_jid = "%s@%s" % (msg.phone_number, domain)
        url = self._evo_url(config, "chat/deleteMessageForEveryone/%s" % config['instance_name'])
        headers = self._evo_headers(config)
        payload = {"id": msg.dialog_message_id, "remoteJid": remote_jid, "fromMe": True}
        response = requests.delete(url, json=payload, headers=headers, timeout=20)
        try:
            response_data = response.json() if response.content else {}
        except ValueError:
            response_data = {}
        if response.status_code in (200, 201):
            msg.write({'is_revoked': True})
        else:
            raise ValidationError(_("WhatsApp rejected the delete: %s") % self._extract_wa_error(response_data))
        return msg

    # ------------------------------------------------------------------
    # Wrappers لشاشة الشات المباشر (fullview) - نفس نمط send_from_fullview/
    # send_media_from_fullview بتوع wa_message_fullview.py بالظبط.
    # ------------------------------------------------------------------
    @api.model
    def send_location_from_fullview(self, phone_number, latitude, longitude, name='', address=''):
        wa = self.sudo().send_location(False, False, phone_number, latitude, longitude,
                                          name, address, post_to_channel=False)
        return self._fullview_message_dict(wa)

    @api.model
    def send_contact_from_fullview(self, phone_number, contact_name, contact_phone, organization=''):
        wa = self.sudo().send_contact_card(False, False, phone_number, contact_name, contact_phone,
                                              organization, post_to_channel=False)
        return self._fullview_message_dict(wa)

    @api.model
    def send_poll_from_fullview(self, phone_number, question, options, selectable_count=1):
        wa = self.sudo().send_poll(False, False, phone_number, question, options,
                                      selectable_count, post_to_channel=False)
        return self._fullview_message_dict(wa)

    @api.model
    def _attachment_from_upload(self, upload, max_mb=16):
        upload.stream.seek(0, 2)
        size = upload.stream.tell()
        upload.stream.seek(0)
        if size > max_mb * 1024 * 1024:
            raise ValidationError(_("File is too large (max %s MB)") % max_mb)
        raw = upload.stream.read()
        import base64 as b64mod
        return self.env['ir.attachment'].sudo().create({
            'name': upload.filename,
            'datas': b64mod.b64encode(raw),
            'mimetype': upload.mimetype or 'application/octet-stream',
        })

    @api.model
    def send_sticker_from_fullview(self, phone_number, upload):
        attachment = self._attachment_from_upload(upload)
        wa = self.sudo().send_sticker(False, False, phone_number, attachment, post_to_channel=False)
        return self._fullview_message_dict(wa)

    @api.model
    def send_voice_note_from_fullview(self, phone_number, upload):
        attachment = self._attachment_from_upload(upload, max_mb=25)
        wa = self.sudo().send_voice_note(False, False, phone_number, attachment, post_to_channel=False)
        return self._fullview_message_dict(wa)

    @api.model
    def edit_message_from_fullview(self, message_id, new_text):
        wa = self.sudo().edit_message(message_id, new_text)
        return self._fullview_message_dict(wa)

    @api.model
    def delete_everyone_from_fullview(self, message_id):
        wa = self.sudo().delete_message_for_everyone(message_id)
        return self._fullview_message_dict(wa)

    @api.model
    def send_typing_from_fullview(self, phone_number, state='composing'):
        return self.sudo().send_typing_presence(phone_number, state)

    @api.model
    def mark_read_on_whatsapp_from_fullview(self, phone_number):
        return self.sudo().mark_read_on_whatsapp(phone_number)
