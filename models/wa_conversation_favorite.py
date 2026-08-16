# -*- coding: utf-8 -*-
from odoo import fields, models


class WaConversationFavorite(models.Model):
    """Marks a WhatsApp conversation (by phone_number/group id) as
    'favourite' for a given company, purely for the 'Favourites' tab in the
    fullview chat screen (static/src/js/whatsapp_full_view.js) - no other
    behaviour depends on it."""
    _name = "wa.conversation.favorite"
    _description = "WhatsApp Favourite Conversation"

    company_id = fields.Many2one('res.company', required=True, index=True,
                                  default=lambda self: self.env.company)
    phone_number = fields.Char(required=True, index=True)

    _company_phone_uniq = models.Constraint(
        'unique(company_id, phone_number)',
        'This conversation is already marked as favourite.',
    )
