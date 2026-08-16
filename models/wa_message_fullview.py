# -*- coding: utf-8 -*-
"""
امتداد على wa.message الحقيقي (class WaMessageQueue في controllers/webhook.py).
مفيش _name هنا خالص - _inherit بس - عشان محصلش أي تعارض تاني زي اللي
حصل قبل كده. بيستخدم نفس الحقول الموجودة فعليًا:
message_content, direction, phone_number, status, media_data,
media_filename, media_mimetype, company_id, create_date.
وبينده send_message()/get_account() الأصليين زي ما هم.
"""
from odoo import fields, models, api, _


class WaMessageFullView(models.Model):
    _inherit = 'wa.message'

    # الحقل الوحيد الجديد: هل عملية العرض في شاشة الشات الجديدة "شافت"
    # الرسالة الواردة دي ولا لسه (للبادچ الأخضر بتاع غير المقروء).
    # True لأي حاجة قديمة/صادرة، False بس لرسايل inbound الجديدة.
    is_seen_internal = fields.Boolean(default=True, index=True)

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
        partner = self.env['res.partner'].sudo().search([
            '|', ('phone', '=', phone_number), ('mobile', '=', phone_number),
        ], limit=1)
        return partner.name if partner else phone_number

    def _fullview_message_dict(self, m):
        return {
            'id': m.id,
            'body': m.message_content,
            'from_me': m.direction == 'outbound',
            'date': fields.Datetime.to_string(m.create_date),
            'state': m.status,
            'has_media': bool(m.media_data),
            'media_type': self._wa_media_type(m.media_mimetype) if m.media_data else False,
            'media_filename': m.media_filename if m.media_data else False,
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
        ], order='create_date desc, id desc')
        conversations = {}
        order = []
        for m in messages:
            key = m.phone_number
            if key not in conversations:
                conversations[key] = {
                    'phone_number': m.phone_number,
                    'contact_name': self._fullview_contact_name(m.phone_number),
                    'last_message': m.message_content or (_('[Media]') if m.media_data else ''),
                    'last_date': fields.Datetime.to_string(m.create_date),
                    'unread_count': 0,
                }
                order.append(key)
        unread_groups = self.sudo().read_group(
            [('company_id', '=', company_id), ('direction', '=', 'inbound'), ('is_seen_internal', '=', False)],
            ['phone_number'], ['phone_number'])
        for g in unread_groups:
            phone = g['phone_number']
            if phone in conversations:
                conversations[phone]['unread_count'] = g['phone_number_count']
        return [conversations[k] for k in order]

    @api.model
    def get_conversation_messages(self, phone_number, after_id=0, limit=100):
        domain = [('company_id', '=', self.env.company.id), ('phone_number', '=', phone_number)]
        if after_id:
            domain.append(('id', '>', int(after_id)))
        messages = self.sudo().search(domain, order='create_date asc, id asc',
                                       limit=(None if after_id else limit))
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
