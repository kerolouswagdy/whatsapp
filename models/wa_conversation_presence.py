# -*- coding: utf-8 -*-
from odoo import fields, models


class WaConversationPresence(models.Model):
    """كاش خفيف لحالة "بيكتب دلوقتي..." / "متاح" / "آخر ظهور" لكل رقم -
    بتتحدث من webhook حدث presence.update (شوف controllers/webhook.py)،
    وشاشة الشات بتسألها كل شوية عن طريق get_conversation_presence()."""
    _name = 'wa.conversation.presence'
    _description = 'WhatsApp Conversation Presence Cache'
    _rec_name = 'phone_number'

    phone_number = fields.Char(required=True, index=True)
    company_id = fields.Many2one('res.company')
    state = fields.Selection([
        ('composing', 'Typing'),
        ('recording', 'Recording Audio'),
        ('available', 'Online'),
        ('unavailable', 'Offline'),
        ('paused', 'Paused'),
    ])
    last_seen = fields.Datetime(help="آخر مرة كانت حالته 'متاح' (available).")
    updated_at = fields.Datetime(help="آخر مرة استلمنا فيها presence.update لصاحب الرقم ده.")

    _sql_constraints = [
        ('phone_company_unique', 'unique(phone_number, company_id)',
         'Presence already cached for this phone number in this company.'),
    ]
