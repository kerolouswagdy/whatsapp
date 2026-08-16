# -*- coding: utf-8 -*-
from odoo import api, fields, models, _
import re
from odoo.exceptions import ValidationError

class MailComposeMessageWAValue(models.TransientModel):
    _name = 'mail.compose.message.wa.value'

    value = fields.Text()

class MailComposeMessage(models.TransientModel):
    _inherit = 'mail.compose.message'

    whatsapp = fields.Boolean()
    whatsapp_template_id = fields.Many2one('wa.message.template')
    custom_wa_text = fields.Text()

    def _get_wa_res_id(self):
        """Compatibility helper: newer Odoo uses res_ids (list), older uses res_id (single int)."""
        self.ensure_one()
        if 'res_ids' in self._fields and self.res_ids:
            # res_ids may be a list of ints, a comma-separated string, or a Command list depending on version
            if isinstance(self.res_ids, (list, tuple)):
                return self.res_ids[0]
            if isinstance(self.res_ids, str):
                first = self.res_ids.split(',')[0].strip()
                return int(first) if first.isdigit() else False
        if 'res_id' in self._fields:
            return self.res_id
        return False

    def quick_wa_open(self):
        self.ensure_one()
        number = (self.whatsapp_number or '').replace(' ', '').replace('-', '').lstrip('+')
        url = f"https://wa.me/{number}?text={self.output_wa_text}"
        return {
            'type': 'ir.actions.act_url',
            'url': url,
            'target': 'new',
        }

    @api.onchange('whatsapp_template_id', 'whatsapp')
    def default_value_ids(self):
        vals_list = [(5,)]
        if self.whatsapp_template_id:
            params = self.whatsapp_template_id.get_params_values(self._get_wa_res_id())
            for par in params:
                vals_list.append((0, 0, {'value': par}))
        self.wa_value_ids = vals_list

    wa_value_ids = fields.Many2many('mail.compose.message.wa.value')

    @api.depends('whatsapp_template_id')
    def get_output_wa_text(self):
        for record in self:
            res = ""
            if record.whatsapp_template_id:
                params = record.whatsapp_template_id.get_params_values(record._get_wa_res_id())
                res = record.whatsapp_template_id.get_sending_txt(params)
            record.output_wa_text = res
    output_wa_text = fields.Text(compute=get_output_wa_text)

    @api.depends('model', 'whatsapp')
    def get_wa_number(self):
        for record in self:
            res = False
            if record.whatsapp:
                config = self.env['wa.message.model.adaptation'].search([('model_id.model', '=', record.model)])
                if not config:
                    raise ValidationError(_("There is no model adaptation config for ") + self._name)
                if config:
                    res = config[0].get_phone_number(res_id=record._get_wa_res_id())
            record.whatsapp_number = res
    whatsapp_number = fields.Char(compute=get_wa_number)

    def action_send_mail(self):
        if self.whatsapp:
            phone = (self.whatsapp_number or '').replace(" ", "").replace('-', "").replace('+', "")
            res_id = self._get_wa_res_id()
            attachments = self.attachment_ids
            first_attachment, extra_attachments = (attachments[0], attachments[1:]) if attachments else (False, attachments)

            if self.whatsapp_template_id:
                template = self.whatsapp_template_id
                text = template.get_sending_txt(template.get_params_values(res_id))
                if template.footer_message:
                    text += '\n\n' + template.footer_message
                if first_attachment:
                    # Send the template text as the caption of the first
                    # image/video/document instead of two separate messages.
                    wa = self.env['wa.message'].send_message_media(
                        res_id=res_id, res_model=self.model, phone_number=phone,
                        attachment=first_attachment, caption=text)
                    wa.wa_message_template_id = template.id
                else:
                    self.env['wa.message'].send_message_template(
                        res_id=res_id, res_model=self.model, phone_number=phone, template_id=template)
            else:
                text = self.custom_wa_text
                if first_attachment:
                    self.env['wa.message'].send_message_media(
                        res_id=res_id, res_model=self.model, phone_number=phone,
                        attachment=first_attachment, caption=text or '')
                elif text:
                    self.env['wa.message'].send_message(
                        res_id=res_id, res_model=self.model, phone_number=phone, text=text)

            # Any additional attachments beyond the first go out as their
            # own media messages (no caption - it already went on the first one).
            for attachment in extra_attachments:
                self.env['wa.message'].send_message_media(
                    res_id=res_id, res_model=self.model, phone_number=phone, attachment=attachment)
        else:
            return super(MailComposeMessage, self).action_send_mail()