from odoo import fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    # ------------------------------------------------------------------
    # WhatsApp Cloud API credentials now live on whatsapp.account
    # (WhatsApp > Configuration > Accounts), matching Odoo's native
    # WhatsApp app. Only the website click-to-chat widget settings and
    # the fallback Discuss operators stay here.
    # ------------------------------------------------------------------
    wa_default_operator_ids = fields.Many2many(
        'res.users', string="Default WhatsApp Operators",
        help="Users added to the Discuss group chat for WhatsApp conversations when no operators are "
             "configured for the specific model (e.g. website widget conversations) and the WhatsApp "
             "Account has no Default Users either.",
        related='company_id.wa_default_operator_ids',
        readonly=False,
    )
    whatsapp_number = fields.Char(
        string="Website WhatsApp Number",
        help="Public number shown on the website WhatsApp widget (digits only, with country code, no + or spaces). Example: 966568406006",
        related='company_id.whatsapp_number',
        readonly=False,
    )
    whatsapp_message = fields.Char(
        string="Website WhatsApp Welcome Message",
        help="Default pre-filled message sent when a website visitor starts a chat from the WhatsApp widget",
        related='company_id.whatsapp_message',
        readonly=False,
    )


class Company(models.Model):
    _inherit = "res.company"

    whatsapp_number = fields.Char(
        string="Website WhatsApp Number",
        help="Public number shown on the website WhatsApp widget (digits only, with country code, no + or spaces). Example: 966568406006",
    )
    whatsapp_message = fields.Char(
        string="Website WhatsApp Welcome Message",
        help="Default pre-filled message sent when a website visitor starts a chat from the WhatsApp widget",
    )
    wa_default_operator_ids = fields.Many2many(
        'res.users', string="Default WhatsApp Operators",
        help="Users added to the Discuss group chat for WhatsApp conversations when no "
             "per-model or per-account operators are configured.",
    )