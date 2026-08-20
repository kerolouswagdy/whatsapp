# -*- coding: utf-8 -*-
import logging
from datetime import timedelta

import requests

from odoo import api, fields, models

_logger = logging.getLogger(__name__)

# لو Odoo فضل مقفول (أو الاتصال بواتساب مقطوع) أكتر من كده، مفيش داعي
# نستنى إشارة reconnect - الـ cron بيعمل مرور دوري على كل الحسابات
# النشطة كشبكة أمان حتى لو محصلش reconnect event خالص (زي حالة فشل فك
# التشفير silently اللي اتكلمنا عليها - مش مرتبطة بانقطاع أصلاً).
SAFETY_NET_MINUTES = 30

# أقصى عدد محادثات نمر عليها في كل مرور - عشان مرور واحد كبير مايستهلكش
# وقت طويل أو يضرب الـ Evolution API بطلبات كتير مرة واحدة على حساب معين
# فيه مئات المحادثات.
DEFAULT_CHATS_LIMIT = 40
DEFAULT_MESSAGES_LIMIT = 50


class WaMessageReconciliation(models.Model):
    _inherit = 'wa.message'

    def _reconcile_account(self, account, chats_limit=DEFAULT_CHATS_LIMIT,
                            messages_limit=DEFAULT_MESSAGES_LIMIT):
        """بيعمل incremental re-sync لآخر محادثات account معين، عن طريق
        sync_history_from_whatsapp() الموجودة بالفعل (بتتجاهل أي رسالة
        الـ wamid بتاعها موجود عندنا من قبل، فمينفعش تعمل duplicate).

        بيرجع {'chats': n, 'messages': n} - عدد المحادثات اللي اتفحصت
        وعدد الرسايل اللي فعليًا اتضافت (يعني كانت فايتة)."""
        self = self.sudo()
        config = self.get_config(account=account)
        headers = self._evo_headers(config)
        chats_checked = 0
        messages_recovered = 0
        try:
            chats_url = self._evo_url(config, "chat/findChats/%s" % config['instance_name'])
            response = requests.post(chats_url, json={}, headers=headers, timeout=20)
            chats_data = response.json() if response.content else []
        except (requests.RequestException, ValueError):
            _logger.exception(
                "Reconciliation: failed to fetch chat list for account %s (%s)",
                account.name, account.instance_name)
            chats_data = []

        if isinstance(chats_data, list):
            # آخر محادثات نشطة الأول (لو الـ API بترجعهم مرتبين كده أصلاً) -
            # مش أولوية قصوى، بس بيقلل احتمال إن limit يقطع قبل ما يوصل
            # للمحادثات اللي فعلاً كان فيها رسايل فايتة.
            for chat in chats_data[:chats_limit]:
                jid = chat.get('id') or chat.get('remoteJid') or chat.get('jid')
                if not jid or jid.endswith('@broadcast') or jid == 'status@broadcast':
                    continue
                phone_number = self.normalize_phone(jid, account.default_country_code)
                if not phone_number:
                    continue
                chats_checked += 1
                try:
                    messages_recovered += self.sync_history_from_whatsapp(
                        phone_number, limit=messages_limit, account=account)
                except Exception:
                    _logger.exception(
                        "Reconciliation: failed syncing chat %s for account %s", jid, account.name)

        account.write({
            'last_reconciled_at': fields.Datetime.now(),
            'pending_reconciliation': False,
        })

        if messages_recovered:
            _logger.info(
                "Reconciliation: recovered %s missed message(s) across %s chat(s) for account %s (%s)",
                messages_recovered, chats_checked, account.name, account.instance_name)
        elif account.developer_mode:
            _logger.info(
                "Reconciliation: checked %s chat(s) for account %s (%s), nothing missing",
                chats_checked, account.name, account.instance_name)

        return {'chats': chats_checked, 'messages': messages_recovered}

    @api.model
    def cron_reconcile_missed_messages(self):
        """بينادي عن طريق ir.cron كل بضع دقايق (راجع data/wa_reconciliation_cron.xml).

        بيعالج نوعين من الحسابات:
        1) اللي عليها pending_reconciliation=True - يعني رجعت اتصلت
           (close -> open) واتحطلها العلم ده من whatsapp_webhook_controller.
        2) أي حساب معملوش reconciliation من أكتر من SAFETY_NET_MINUTES -
           شبكة أمان لالتقاط الرسايل اللي فُقدت من غير أي إشارة انقطاع
           واضحة (زي فشل فك التشفير الصامت)، مش بس حالة الـ reconnect.
        """
        threshold = fields.Datetime.now() - timedelta(minutes=SAFETY_NET_MINUTES)
        accounts = self.env['whatsapp.account'].sudo().search([
            ('active', '=', True),
            ('server_url', '!=', False),
            ('instance_name', '!=', False),
            ('api_key', '!=', False),
            '|',
                ('pending_reconciliation', '=', True),
                '|',
                    ('last_reconciled_at', '=', False),
                    ('last_reconciled_at', '<', threshold),
        ])
        for account in accounts:
            try:
                self._reconcile_account(account)
            except Exception:
                # Best-effort زي باقي الموديول: حساب واحد يفشل مايوقفش
                # الباقي، والـ cron نفسه لازم يخلص من غير ما يفضل معلّق.
                _logger.exception(
                    "Reconciliation: unhandled error reconciling account %s (%s)",
                    account.name, account.instance_name)
