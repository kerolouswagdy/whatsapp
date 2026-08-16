# -*- coding: utf-8 -*-
"""
امتداد على wa.message الحقيقي (class WaMessageQueue في controllers/webhook.py).
مفيش _name هنا خالص - _inherit بس - عشان محصلش أي تعارض تاني زي اللي
حصل قبل كده. بيستخدم نفس الحقول الموجودة فعليًا:
message_content, direction, phone_number, status, media_data,
media_filename, media_mimetype, company_id, create_date.
وبينده send_message()/get_account() الأصليين زي ما هم.
"""
import json

from odoo import fields, models, api, _
from odoo.exceptions import ValidationError
import base64


class WaMessageFullView(models.Model):
    _inherit = 'wa.message'

    # الحقل الوحيد اللي كان موجود قبل كده: هل عملية العرض في شاشة الشات
    # الجديدة "شافت" الرسالة الواردة دي ولا لسه (للبادچ الأخضر بتاع غير
    # المقروء). True لأي حاجة قديمة/صادرة، False بس لرسايل inbound الجديدة.
    is_seen_internal = fields.Boolean(default=True, index=True)

    # تخزين تفاعلات الإيموجي على الرسالة دي: JSON بسيط {"__me__": "👍",
    # "__them__": "❤️"} - شات فردي (رقم واحد) فمفيش غير طرفين ممكن يتفاعلوا.
    reactions_json = fields.Char()

    # التاريخ/الوقت الحقيقي بتاع الرسالة على واتساب (messageTimestamp) -
    # مختلف عن create_date لما بنستورد رسايل قديمة (sync_history_from_
    # whatsapp) لأن create_date بيتسجل وقت الاستيراد نفسه مش وقت الرسالة.
    wa_timestamp = fields.Datetime(index=True, default=lambda self: fields.Datetime.now())

    # "حذف عندي بس" - بتخفي الرسالة من شاشة الشات الجديدة بس من غير ما
    # تتمسح فعليًا (زي "Delete for me" في واتساب).
    is_deleted_locally = fields.Boolean(default=False, index=True)

    # هل الشات ده جروب واتساب (JID بينتهي بـ @g.us) ولا فرد عادي - بيتحدد
    # وقت استلام الرسالة الواردة نفسها (remoteJid الخام قبل ما normalize_phone
    # يشيل الـ suffix)، عشان تبويب "Groups" في شاشة الشات يقدر يفلتر عليه.
    wa_is_group = fields.Boolean(default=False, index=True)

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if 'is_seen_internal' not in vals:
                vals['is_seen_internal'] = vals.get('direction') != 'inbound'
        return super().create(vals_list)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _fullview_contact_name(self, phone_number):
        Partner = self.env['res.partner']
        # 'mobile' مش موجود دايمًا على res.partner (بيتشال أحيانًا حسب
        # الإعداد/الموديولات المثبتة) - بنبني الـ domain ديناميكيًا عشان
        # ميبوظش لو الحقل مش موجود.
        if 'mobile' in Partner._fields:
            domain = ['|', ('phone', '=', phone_number), ('mobile', '=', phone_number)]
        else:
            domain = [('phone', '=', phone_number)]
        partner = Partner.sudo().search(domain, limit=1)
        return partner.name if partner else phone_number

    def _reactions_for_widget(self, m):
        """بترجع لستة {emoji, count, from_me} جاهزة للعرض تحت البابل، من
        غير ما تفرق بين تفاعل وارد وصادر غير عرض عدد التفاعلات."""
        if not m.reactions_json:
            return []
        try:
            data = json.loads(m.reactions_json)
        except (ValueError, TypeError):
            return []
        grouped = {}
        for sender, emoji in data.items():
            if not emoji:
                continue
            g = grouped.setdefault(emoji, {'emoji': emoji, 'count': 0, 'from_me': False})
            g['count'] += 1
            if sender == '__me__':
                g['from_me'] = True
        return list(grouped.values())

    def _fullview_message_dict(self, m):
        body = (m.message_content or '').strip()
        media_type = False
        if m.media_data:
            media_type = self._wa_media_type(m.media_mimetype)
            # send_message_media() الأصلي بيحط نص تلقائي زي "[document]
            # Lecture_2.pdf" لما محدش يكتب caption - بما إن اسم/نوع الملف
            # هيتعرض أصلاً كـ chip منفصل في الشاشة، بنشيل النص التلقائي
            # ده عشان مايتكررش أو يبان غريب. أي caption حقيقي كتبه اليوزر
            # بيفضل زي ما هو.
            auto_label = "[%s] %s" % (media_type, m.media_filename or '')
            if body == auto_label:
                body = ''
        return {
            'id': m.id,
            'body': body,
            'from_me': m.direction == 'outbound',
            'date': fields.Datetime.to_string(m.wa_timestamp or m.create_date),
            'state': m.status,
            'has_media': bool(m.media_data),
            'media_type': media_type,
            'media_filename': m.media_filename if m.media_data else False,
            'reactions': self._reactions_for_widget(m),
            'can_forward': bool(body or m.media_data),
        }

    # ------------------------------------------------------------------
    # API used by controllers/whatsapp_fullview_controller.py
    # ------------------------------------------------------------------
    @api.model
    def get_conversations(self):
        """لستة المحادثات (شات واحد لكل رقم تليفون) لشركة اليوزر الحالي."""
        company_id = self.env.company.id
        messages = self.sudo().search([
            ('company_id', '=', company_id), ('phone_number', '!=', False),
            ('is_deleted_locally', '=', False),
        ], order='wa_timestamp desc, id desc')
        favorite_numbers = set(self.env['wa.conversation.favorite'].sudo().search(
            [('company_id', '=', company_id)]).mapped('phone_number'))
        conversations = {}
        order = []
        for m in messages:
            key = m.phone_number
            if key not in conversations:
                conversations[key] = {
                    'phone_number': m.phone_number,
                    'contact_name': self._fullview_contact_name(m.phone_number),
                    'last_message': m.message_content or (_('[Media]') if m.media_data else ''),
                    'last_date': fields.Datetime.to_string(m.wa_timestamp or m.create_date),
                    'unread_count': 0,
                    # Real phone numbers top out at 15 digits (E.164) -
                    # WhatsApp group JIDs are 18-20 digits, so this also
                    # correctly flags groups created before wa_is_group
                    # existed (it always defaults to False on old rows).
                    'is_group': bool(key) and len(key) >= 16,
                    'is_favorite': key in favorite_numbers,
                }
                order.append(key)
            if m.wa_is_group:
                # Any message in the thread flagged as a group is enough -
                # a group JID never mixes with an individual one.
                conversations[key]['is_group'] = True
        # _read_group الجديد بتاع أودوو 19 (read_group القديم deprecated)
        unread_groups = self.sudo()._read_group(
            [('company_id', '=', company_id), ('direction', '=', 'inbound'), ('is_seen_internal', '=', False),
             ('is_deleted_locally', '=', False)],
            groupby=['phone_number'], aggregates=['__count'])
        for phone, count in unread_groups:
            if phone in conversations:
                conversations[phone]['unread_count'] = count
        return [conversations[k] for k in order]

    @api.model
    def toggle_favorite_conversation(self, phone_number):
        """Stars/unstars a conversation for the 'Favourites' tab. Returns
        the new state (True = now favourite)."""
        Favorite = self.env['wa.conversation.favorite'].sudo()
        company_id = self.env.company.id
        existing = Favorite.search([('company_id', '=', company_id), ('phone_number', '=', phone_number)], limit=1)
        if existing:
            existing.unlink()
            return False
        Favorite.create({'company_id': company_id, 'phone_number': phone_number})
        return True

    @api.model
    def get_conversation_messages(self, phone_number, after_id=0, before_id=0, limit=50):
        """- after_id: polling عادي، كل رسالة جديدة بعد آخر واحدة عندي (زي
          ما كان قبل كده، من غير حد أقصى - نادرًا ما تيجي أكتر من كام
          رسالة كل poll).
        - before_id: "تحميل رسائل أقدم" - آخر `limit` رسالة قبل أول واحدة
          ظاهرة عندي دلوقتي.
        - من غير الاتنين (فتح المحادثة أول مرة): آخر `limit` رسالة
          (الأحدث)، مش أول `limit` زي ما كان قبل كده - كان بيجيب أقدم 100
          رسالة بدل أحدث 100 لو المحادثة طويلة."""
        # Ordered by wa_timestamp (the real WhatsApp message time), not
        # create_date (when Odoo happened to insert the row) - those two
        # diverge whenever a message is imported after the fact (history
        # sync) or a webhook is processed slightly out of order, which is
        # exactly what was making the conversation display in the wrong
        # order. id stays as the tiebreaker/uniqueness key so before_id/
        # after_id paging (based on id, not time) keeps working.
        domain = [('company_id', '=', self.env.company.id), ('phone_number', '=', phone_number),
                  ('is_deleted_locally', '=', False)]
        if after_id:
            messages = self.sudo().search(domain + [('id', '>', int(after_id))],
                                           order='wa_timestamp asc, id asc')
        elif before_id:
            messages = self.sudo().search(domain + [('id', '<', int(before_id))],
                                           order='wa_timestamp desc, id desc', limit=int(limit))
            messages = messages[::-1]
        else:
            messages = self.sudo().search(domain, order='wa_timestamp desc, id desc', limit=int(limit))
            messages = messages[::-1]
        return [self._fullview_message_dict(m) for m in messages]

    @api.model
    def mark_conversation_read(self, phone_number):
        unread = self.sudo().search([
            ('company_id', '=', self.env.company.id), ('phone_number', '=', phone_number),
            ('direction', '=', 'inbound'), ('is_seen_internal', '=', False),
        ])
        unread.write({'is_seen_internal': True})
        return True

    @api.model
    def send_from_fullview(self, phone_number, body):
        """بتستخدم send_message() الأصلي بتاعك - post_to_channel=False عشان
        الرسالة تتبعت فعليًا من غير ما يتعمل discuss.channel."""
        wa = self.sudo().send_message(res_id=False, res_model=False, phone_number=phone_number,
                                       text=body, post_to_channel=False)
        return self._fullview_message_dict(wa)

    @api.model
    def send_media_from_fullview(self, phone_number, upload, caption=''):
        """بتستخدم send_message_media() الأصلي بتاعك (نفس اللي زرار
        'WhatsApp' في الـ chatter بيستخدمه) عشان تبعت صورة/ملف (PDF،
        صور، مستند فيه لينك تسجيل...) فعليًا عن طريق Evolution API."""
        upload.stream.seek(0, 2)
        size = upload.stream.tell()
        upload.stream.seek(0)
        if size > 25 * 1024 * 1024:
            raise ValidationError(_("File is too large (max 25 MB)"))
        raw = upload.stream.read()
        attachment = self.env['ir.attachment'].sudo().create({
            'name': upload.filename,
            'datas': base64.b64encode(raw),
            'mimetype': upload.mimetype or 'application/octet-stream',
        })
        wa = self.sudo().send_message_media(res_id=False, res_model=False, phone_number=phone_number,
                                             attachment=attachment,
                                             # مسافة بدل فاضي عشان نمنع
                                             # send_message_media من حط
                                             # النص التلقائي "[type] name"
                                             # (بيحصل بس لو caption falsy).
                                             caption=(caption.strip() if caption and caption.strip() else ' '),
                                             post_to_channel=False)
        return self._fullview_message_dict(wa)

    # ------------------------------------------------------------------
    # React / Forward / Delete-locally / History - القائمة اللي بتظهر لما
    # تدوس على رسالة في شاشة الشات
    # ------------------------------------------------------------------
    @api.model
    def react_from_fullview(self, message_id, emoji):
        """emoji فاضي = إلغاء تفاعلي الحالي (زي ما بتعمل لو دوست تاني على
        نفس الإيموجي في واتساب)."""
        wa = self.sudo().send_reaction(int(message_id), emoji or '')
        return self._fullview_message_dict(wa)

    @api.model
    def forward_from_fullview(self, message_id, to_phone_number):
        wa = self.sudo().forward_message(int(message_id), to_phone_number)
        return self._fullview_message_dict(wa)

    @api.model
    def delete_locally_from_fullview(self, message_id):
        """حذف عندي بس - الرسالة تفضل موجودة في قاعدة البيانات (وعند
        الطرف التاني على واتساب) بس تختفي من شاشة الشات دي بس."""
        msg = self.sudo().browse(int(message_id))
        if msg.exists() and msg.company_id.id == self.env.company.id:
            msg.is_deleted_locally = True
        return True

    @api.model
    def sync_history_from_fullview(self, phone_number, limit=50):
        count = self.sudo().sync_history_from_whatsapp(phone_number, limit=limit)
        return {'imported': count}
