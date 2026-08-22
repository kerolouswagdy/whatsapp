# -*- coding: utf-8 -*-
"""
امتداد على whatsapp.account (_inherit بس زي whatsapp_account_fullview.py) -
بيضيف إدارة الجروبات اللي كانت Read-only بس قبل كده (بنقرا اسم/أعضاء/
وصف الجروب في webhook.py عن طريق group/fetchAllGroups) - دلوقتي نقدر
نعمل عليها فعل (ننشئ جروب، نضيف/نشيل عضو، نغيّر اسم/وصف/صورة، نجيب
رابط الدعوة).

كل الدوال دي بترجع الـ dict الخام اللي راجع من Evolution API نفسه (مفيش
حاجة بتتخزن في أودو - الجروب نفسه مش موديل عندنا، بس بيانات محلية زي
wa_group_participant بتتحدّث لاحقًا لما يوصل حدث group.update على
الـ webhook زي أي جروب تاني)."""
import logging

import requests

from odoo import models, api, _
from odoo.exceptions import ValidationError

_logger = logging.getLogger(__name__)


class WhatsappAccountGroups(models.Model):
    _inherit = "whatsapp.account"

    def _evo_group_call(self, method, path, group_jid=False, payload=None, extra_params=None):
        self.ensure_one()
        url = self._evo_instance_url(path)
        params = dict(extra_params or {})
        if group_jid:
            params['groupJid'] = group_jid
        headers = self._evo_headers()
        response = requests.request(method, url, headers=headers, params=params,
                                      json=payload, timeout=20)
        try:
            data = response.json() if response.content else {}
        except ValueError:
            data = {}
        if response.status_code not in (200, 201):
            message = data.get('message') or data.get('error') or response.text or _('Unknown error')
            raise ValidationError(_("Evolution API rejected the request: %s") % message)
        return data

    def create_group(self, subject, participants):
        """participants: لستة أرقام (زي ['201220744453', '201553513977']) -
        من غير كود دولة زيادة عن اللازم، بترجع الرد الخام (فيه الـ id
        الجديد بتاع الجروب - group/id@g.us)."""
        self.ensure_one()
        payload = {"subject": subject, "participants": [self.normalize_phone(p) for p in participants]}
        return self._evo_group_call('POST', 'group/create', payload=payload)

    def update_group_subject(self, group_jid, subject):
        self.ensure_one()
        return self._evo_group_call('POST', 'group/updateGroupSubject', group_jid=group_jid,
                                       payload={"subject": subject})

    def update_group_description(self, group_jid, description):
        self.ensure_one()
        return self._evo_group_call('POST', 'group/updateGroupDescription', group_jid=group_jid,
                                       payload={"description": description})

    def update_group_picture(self, group_jid, image_url_or_b64):
        self.ensure_one()
        return self._evo_group_call('POST', 'group/updateGroupPicture', group_jid=group_jid,
                                       payload={"image": image_url_or_b64})

    def update_group_participants(self, group_jid, action, participants):
        """action: 'add' / 'remove' / 'promote' (خليه أدمن) / 'demote'
        (شيله من أدمن). participants: لستة أرقام."""
        self.ensure_one()
        if action not in ('add', 'remove', 'promote', 'demote'):
            raise ValidationError(_("Invalid group participant action: %s") % action)
        payload = {"action": action, "participants": [self.normalize_phone(p) for p in participants]}
        return self._evo_group_call('POST', 'group/updateParticipant', group_jid=group_jid, payload=payload)

    def get_group_invite_code(self, group_jid):
        """بترجع رابط دعوة الجروب الحالي - GET بيستخدم query param مش body."""
        self.ensure_one()
        return self._evo_group_call('GET', 'group/inviteCode', group_jid=group_jid)

    def revoke_group_invite_code(self, group_jid):
        """بتلغي رابط الدعوة القديم وتطلع واحد جديد - أي حد كان معاه
        اللينك القديم ملهوش يدخل بيه تاني."""
        self.ensure_one()
        return self._evo_group_call('POST', 'group/revokeInviteCode', group_jid=group_jid)

    def leave_group(self, group_jid):
        self.ensure_one()
        return self._evo_group_call('DELETE', 'group/leaveGroup', group_jid=group_jid)

    def normalize_phone(self, phone, country_code=False):
        """نفس منطق wa.message.normalize_phone بالظبط - محتاجينها هنا
        كمان عشان مستخدمين create_group/update_group_participants
        مباشرة على الحساب من غير ما يعدّوا برقم اتنضف قبل كده."""
        if not phone:
            return phone
        phone = phone.split('@')[0].replace(" ", "").replace("-", "")
        if phone.startswith('00'):
            phone = phone[2:]
        elif phone.startswith('+'):
            phone = phone[1:]
        return phone
