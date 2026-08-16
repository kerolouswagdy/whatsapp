# -*- coding: utf-8 -*-
import logging

import requests

from odoo import fields, models, api, _
from odoo.exceptions import ValidationError

_logger = logging.getLogger(__name__)


class WhatsappAccount(models.Model):
    """WhatsApp Business Account: one record per Evolution API instance
    connected to this Odoo instance (self-hosted Baileys-based gateway,
    NOT Meta's official Cloud API). Kept the same model/menu structure as
    the previous Meta-based version so views and related models don't
    need to change."""
    _name = "whatsapp.account"
    _description = "WhatsApp Business Account"
    _inherit = ['mail.thread', 'mail.activity.mixin']

    name = fields.Char(required=True, tracking=True)
    active = fields.Boolean(default=True)

    # Sending messages -------------------------------------------------
    # Evolution API credentials: a server you (self-)host + one "instance"
    # (a linked WhatsApp session) on it, identified by name, plus the
    # global API key configured on that server (AUTHENTICATION_API_KEY).
    server_url = fields.Char(
        string="Server URL", tracking=True,
        help="Base URL of your Evolution API server, e.g. http://localhost:8080 "
             "(no trailing slash).")
    instance_name = fields.Char(
        string="Instance Name", tracking=True,
        help="Name of the Evolution API instance linked to this WhatsApp number "
             "(the one you created/scanned the QR code for in the Evolution Manager).")
    api_key = fields.Char(
        string="API Key",
        help="Evolution API 'apikey' - the AUTHENTICATION_API_KEY configured on your "
             "Evolution server (Settings > API Key Global in the Manager).")
    phone_number = fields.Char(
        string="Phone Number", tracking=True,
        help="The WhatsApp number linked to this instance, digits only with country "
             "code (e.g. 201553513977). Informational - Evolution routes by instance, "
             "not by this field.")

    default_country_code = fields.Char(
        string="Default Country Code", default="20",
        help="Prepended to phone numbers typed in local format (starting with a "
             "trunk '0', e.g. 01220744453) before sending, since Evolution/Baileys "
             "needs a full international MSISDN (e.g. 201220744453) or it reports "
             "the JID as not existing. Leave numbers already in international "
             "format untouched. Digits only, no +.")

    # Receiving messages -------------------------------------------------
    callback_url = fields.Char(string="Callback URL", compute="_compute_callback_url")
    webhook_verify_token = fields.Char(
        string="Webhook Verify Token",
        help="Not used by Evolution API (that's a Meta Cloud API concept), kept for "
             "compatibility with older setups.")

    # Notifications -------------------------------------------------
    default_user_ids = fields.Many2many(
        'res.users', 'whatsapp_account_default_user_rel', 'account_id', 'user_id',
        string="Default Users",
        help="Users added to the Discuss group chat for WhatsApp conversations when no "
             "per-model operators are configured (see WhatsApp > Configuration > Model Adaptations).")
    company_ids = fields.Many2many(
        'res.company', 'whatsapp_account_company_rel', 'account_id', 'company_id',
        string="Allowed companies", default=lambda self: self.env.company)

    developer_mode = fields.Boolean(
        string="Verbose Logging",
        help="Log the full request/response payload of every Evolution API call.")

    template_ids = fields.One2many('wa.message.template', 'account_id', string="Templates")
    template_count = fields.Integer(compute="_compute_template_count")

    # Auto-reply -------------------------------------------------------
    auto_reply_enabled = fields.Boolean(
        string="Enable Auto-Reply",
        help="When a customer sends a WhatsApp message, automatically reply with the "
             "template below - until either an operator replies from Odoo (Discuss/chatter) "
             "or someone replies directly from the linked phone, at which point auto-reply "
             "stops for that conversation so a human can take over.")
    auto_reply_template_id = fields.Many2one(
        'wa.message.template', string="Auto-Reply Template",
        domain="[('account_id', '=', id)]",
        help="Template sent automatically to the customer on their first (and any "
             "subsequent) message, as long as no human has taken over the conversation yet.")

    qr_code_ids = fields.One2many('wa.qr.code', 'account_id', string="QR Codes")
    qr_code_count = fields.Integer(compute="_compute_qr_code_count")

    @api.depends('template_ids')
    def _compute_template_count(self):
        for account in self:
            account.template_count = len(account.template_ids)

    @api.depends('qr_code_ids')
    def _compute_qr_code_count(self):
        for account in self:
            account.qr_code_count = len(account.qr_code_ids)

    def _compute_callback_url(self):
        base_url = self.env['ir.config_parameter'].sudo().get_param('web.base.url') or ''
        for account in self:
            account.callback_url = (base_url.rstrip('/') + '/api/v1/whatsapp/webhook') if base_url else False

    # ------------------------------------------------------------------
    # Evolution API helpers
    # ------------------------------------------------------------------
    def _evo_headers(self):
        self.ensure_one()
        return {
            'apikey': self.api_key or '',
            'Content-Type': 'application/json',
        }

    def _evo_url(self, path):
        """path should NOT include the instance name - callers append it,
        e.g. account._evo_url('message/sendText') + '/' + account.instance_name"""
        self.ensure_one()
        if not self.server_url:
            raise ValidationError(_("Please fill in the Server URL first."))
        return "%s/%s" % (self.server_url.rstrip('/'), path.lstrip('/'))

    def _evo_instance_url(self, path):
        """Builds <server_url>/<path>/<instance_name> - the shape used by
        most Evolution API endpoints (instance name as the last URL segment)."""
        self.ensure_one()
        if not self.instance_name:
            raise ValidationError(_("Please fill in the Instance Name first."))
        return "%s/%s" % (self._evo_url(path), self.instance_name)

    # ------------------------------------------------------------------
    # Buttons / actions
    # ------------------------------------------------------------------
    def action_view_templates(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Templates'),
            'res_model': 'wa.message.template',
            'view_mode': 'list,form',
            'domain': [('account_id', '=', self.id)],
            'context': {'default_account_id': self.id},
        }

    def action_view_qr_codes(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('QR Codes'),
            'res_model': 'wa.qr.code',
            'view_mode': 'list,form',
            'domain': [('account_id', '=', self.id)],
            'context': {'default_account_id': self.id},
        }

    def action_test_credentials(self):
        """Checks the instance's connection state on the Evolution server
        (GET /instance/connectionState/{instance})."""
        self.ensure_one()
        if not self.server_url or not self.instance_name or not self.api_key:
            raise ValidationError(_("Please fill in the Server URL, Instance Name and API Key first."))
        url = self._evo_instance_url("instance/connectionState")
        response = requests.get(url, headers=self._evo_headers(), timeout=15)
        try:
            data = response.json()
        except ValueError:
            data = {}
        if self.developer_mode:
            _logger.info("Evolution API Test Credentials response: %s", data)
        if response.status_code != 200:
            raise ValidationError(_("Test failed: %s") % (data.get('message') or response.text))
        state = (data.get('instance') or {}).get('state') or data.get('state')
        if state != 'open':
            raise ValidationError(_(
                "Instance is not connected (state: %s). Open the Evolution Manager and scan the "
                "QR code again."
            ) % (state or 'unknown'))
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _("Credentials OK"),
                'message': _("Instance '%s' is connected.") % self.instance_name,
                'type': 'success',
                'sticky': False,
            }
        }

    def action_sync_group_names(self):
        """Fetches every group's real subject from Evolution API and
        renames any local contact still stuck on the raw group JID -
        so groups created before names were tracked show up correctly
        right away, instead of waiting for their next message."""
        self.ensure_one()
        if not self.server_url or not self.instance_name or not self.api_key:
            raise ValidationError(_("Please fill in the Server URL, Instance Name and API Key first."))
        renamed = self.env['wa.message'].sudo().sync_all_wa_group_names()
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _("Group Names Synced"),
                'message': _("%s group(s) renamed.") % renamed,
                'type': 'success',
                'sticky': False,
            }
        }

    def action_open_connect_wizard(self):
        """Opens the 'Scan to link' popup (see wa_account_connect_wizard.py)."""
        self.ensure_one()
        if not self.server_url or not self.instance_name or not self.api_key:
            raise ValidationError(_("Please fill in the Server URL, Instance Name and API Key first."))
        return {
            'type': 'ir.actions.act_window',
            'name': _('Link WhatsApp Device'),
            'res_model': 'whatsapp.account.connect.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {'default_account_id': self.id},
        }

    def action_subscribe_webhook(self):
        """Registers this module's Callback URL on the Evolution instance
        (POST /webhook/set/{instance}) so incoming messages/status updates
        get pushed here automatically."""
        self.ensure_one()
        if not self.server_url or not self.instance_name or not self.api_key:
            raise ValidationError(_("Please fill in the Server URL, Instance Name and API Key first."))
        if not self.callback_url:
            raise ValidationError(_("Set web.base.url (General Settings) so a Callback URL can be computed."))
        url = self._evo_instance_url("webhook/set")
        payload = {
            "webhook": {
                "url": self.callback_url,
                "enabled": True,
                "events": ["MESSAGES_UPSERT", "MESSAGES_UPDATE", "CONNECTION_UPDATE"],
                # Without this, incoming images/audio/video/documents only
                # arrive with an encrypted mediaKey (Baileys) - Evolution
                # needs to decrypt it itself and inline the result as
                # base64 in the payload before we can store it.
                "webhookBase64": True,
            }
        }
        response = requests.post(url, json=payload, headers=self._evo_headers(), timeout=15)
        try:
            data = response.json()
        except ValueError:
            data = {}
        if self.developer_mode:
            _logger.info("Evolution API webhook/set payload=%s response=%s", payload, data)
        if response.status_code not in (200, 201):
            raise ValidationError(_("Webhook registration failed: %s") % (data.get('message') or response.text))
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _("Webhook Registered"),
                'message': _("Evolution will now push events to %s") % self.callback_url,
                'type': 'success',
                'sticky': False,
            }
        }

    def action_synchronize_templates(self):
        """Evolution API (Baileys) has no concept of pre-approved Meta
        templates - there is no server-side list to pull. Instead of a bare
        "not applicable" notice, this gives the user something actually
        useful: how many local templates exist, and whether the instance is
        currently reachable (since that's the only thing that can genuinely
        be "checked" against the Evolution server here)."""
        self.ensure_one()
        template_count = len(self.template_ids)

        connection_note = _("Connection status: unknown (fill in Server URL, "
                             "Instance Name and API Key, then use Test Credentials).")
        if self.server_url and self.instance_name and self.api_key:
            try:
                url = self._evo_instance_url("instance/connectionState")
                response = requests.get(url, headers=self._evo_headers(), timeout=15)
                data = response.json() if response.content else {}
                state = (data.get('instance') or {}).get('state') or data.get('state')
                if response.status_code == 200 and state == 'open':
                    connection_note = _("Connection status: instance '%s' is connected.") % self.instance_name
                else:
                    connection_note = _(
                        "Connection status: instance not connected (state: %s). Open the "
                        "Evolution Manager and scan the QR code again."
                    ) % (state or 'unknown')
            except requests.RequestException:
                connection_note = _("Connection status: could not reach the Evolution server.")

        message = _(
            "Evolution API doesn't have server-side templates to pull like Meta Cloud API does - "
            "there's nothing to download. Your %(count)s local template(s) under WhatsApp > "
            "Templates are already what gets sent (as regular text messages once rendered).\n\n"
            "%(connection_note)s"
        ) % {'count': template_count, 'connection_note': connection_note}

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _("Local Templates"),
                'message': message,
                'type': 'info',
                'sticky': True,
            }
        }