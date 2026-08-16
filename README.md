# Odoo WhatsApp API (Evolution API Edition)

Send and receive WhatsApp messages directly from Odoo — on any model (leads, sales orders, invoices, tickets...) — through a **self‑hosted [Evolution API](https://doc.evolution-api.com/) instance** (a Baileys‑based WhatsApp gateway), with a full built‑in chat inbox, website live‑chat widget, and Discuss integration.

> **Note:** this module was originally built around the 360Dialog / Meta Cloud API. It has since been rewritten to work with **Evolution API**, a self‑hosted gateway that talks to WhatsApp through a linked phone number (QR‑code login), so no Meta Business/WABA account or per‑message API costs are required. Model names, menus and views were kept the same so upgrades from the old version are seamless.

## Description

This module lets any Odoo record subscribed to `mail.thread` send and receive WhatsApp messages, and adds a full chat experience on top:

- **Send messages** from any model using the native *Mail Compose* wizard (a WhatsApp checkbox switches the wizard from email to WhatsApp) or from configured message templates.
- **Receive messages** via a webhook, automatically logged on the related record's chatter/Discuss channel, with an activity created for the configured user if a send fails.
- **Full WhatsApp inbox inside Odoo** — a dedicated full‑page chat screen (conversations list, message history, media, emoji reactions, forwarding, favourites) talking live to your Evolution API instance.
- **Website live‑chat widget** — visitors can start a real WhatsApp conversation from your website; replies (from WhatsApp or from Discuss) appear back in the same widget.
- **Auto‑reply** — automatically answer a customer's first message with a template until a human takes over the conversation.
- **QR "click to chat" codes** — generate `wa.me` deep‑link QR codes (e.g. for flyers/storefronts) with a pre‑filled message.
- **Group name sync** — pulls real WhatsApp group subjects so group conversations don't stay stuck on a raw JID.

This module was tested on Odoo 19 (kept compatible with the model/view structure used since v15/v16/v17).

## Requirements

- A running **Evolution API** server (self‑hosted, e.g. via Docker) with a WhatsApp instance linked by scanning a QR code. See the [Evolution API documentation](https://doc.evolution-api.com/) to set one up.
- The instance's **Server URL**, **Instance Name** and **API Key** (`AUTHENTICATION_API_KEY` configured on your Evolution server).
- Your Odoo server's public **`web.base.url`** must be reachable from the Evolution server so it can push the webhook (incoming messages, delivery status, connection updates).
- Python package **`qrcode[pil]`** on the Odoo server if you want to generate "click to chat" QR codes (`pip install qrcode[pil]`).

Unlike the Meta Cloud API / 360Dialog approach, there are **no pre‑approved message templates or per‑message provider costs** — any text is sent as a normal WhatsApp message from the linked phone number, and templates configured in Odoo are just reusable text with variable substitution.

## Installation

1. Download or clone the repository into a folder on your Odoo addons path.
2. Configure module dependencies. By default WhatsApp messaging is enabled only for the `crm.lead` model — you can add more via **WhatsApp > Configuration > Model Adaptation** after installing (no manifest edit needed).
3. Install the module: **Apps** menu → clear default filters → search `odoo-whatsapp-api` (or `WhatsApp`). If it doesn't appear, click **Update Apps List** (requires [developer mode](https://www.odoo.com/documentation/17.0/applications/general/developer_mode.html)) or check the addons path in `odoo.conf`.

## Configuration

### 1. Connect your Evolution API instance

Go to **WhatsApp > Configuration > Accounts** and create/edit an account:

| Field | Description |
|---|---|
| **Server URL** | Base URL of your Evolution API server, e.g. `http://localhost:8080` (no trailing slash). |
| **Instance Name** | The Evolution instance name you created/scanned the QR code for in the Evolution Manager. |
| **API Key** | The `apikey` / `AUTHENTICATION_API_KEY` configured on your Evolution server. |
| **Phone Number** | Informational — Evolution routes by instance, not by this field. |
| **Default Country Code** | Prepended to locally‑formatted numbers (starting with a trunk `0`) before sending. |

Then, on the account:

- **Test Credentials** — checks the instance's connection state on the Evolution server.
- **Link WhatsApp Device** — opens a "Scan to log in" popup showing the pairing QR code (scanned with the *business* phone, like WhatsApp Web).
- **Set Webhook** — registers this module's callback URL (`<your-odoo-url>/api/v1/whatsapp/webhook`) on the Evolution instance so incoming messages, status updates and connection changes get pushed to Odoo automatically.
- **Sync Group Names** — backfills real WhatsApp group names for groups already known to Odoo.

For local development, you can point the webhook at a tunneling tool (ngrok, etc.) or test it directly with an HTTP client against `http://localhost:8069/api/v1/whatsapp/webhook`.

### 2. Model Adaptation

For each Odoo model you want WhatsApp messaging enabled on, add a configuration under **WhatsApp > Configuration > Model Adaptation**:

- **Message Error Activity Configuration** — who receives the activity notification when a message fails (a specific user and/or a user field on the model; the specific user is used only if the field isn't set).
- **Phone Number Fields** — which field(s) hold the phone number. If a `res.partner` field is set, its `mobile` is used first, falling back to `phone`.

A default configuration for `crm.lead` is preloaded.

### 3. Message Templates

Manage reusable message templates under **WhatsApp > Configuration > Message Templates**. Templates can include `[]` placeholder variables, each mapped either to free text or to a field on the model (char or many2one fields). Since Evolution API has no server‑side approved‑template concept, these are rendered locally and sent as regular text messages.

### 4. Auto‑Reply

On a WhatsApp account, enable **Auto‑Reply** and pick a template. It's sent automatically on a customer's first (and subsequent) message until an operator replies from Odoo or from the linked phone directly — at which point auto‑reply stops for that conversation.

### 5. Website Live‑Chat Widget

Under **Settings > WhatsApp**, set the public **Website WhatsApp Number**, a **Welcome Message**, and default **Discuss operators** to notify when no per‑model operators are configured. The widget lets visitors chat over real WhatsApp without leaving your site; replies are polled by a private `conversation_token` so visitors can't see each other's conversations.

## Usage

- **Mail Compose Wizard** — any model subscribed to `mail` gets a WhatsApp checkbox in the compose wizard to send a WhatsApp message instead of an email.
- **Message Post With Template** — mass mailing (e.g. Mail Automation) using a `mail.template` linked to a WhatsApp template automatically sends a WhatsApp message instead of an email.
- **WhatsApp Inbox** — the full‑page chat screen (menu **WhatsApp**) shows all conversations, lets you send text and media, react with emoji, forward messages, mark favourites, and sync older history from WhatsApp.
- **Discuss** — inbound WhatsApp conversations open a Discuss channel per contact; replying from that channel sends the reply back out over WhatsApp.
- **QR Codes** — generate click‑to‑chat QR codes per account under **WhatsApp > Configuration > QR Codes**.

## Technical Notes

- Webhook endpoints: `POST /api/v1/whatsapp/webhook` (raw Evolution events) and `POST /api/v1/whatsapp/webhook/<event_name>` (used when Evolution's "Webhook by Events" mode is enabled).
- Website widget endpoints live under `/api/v1/whatsapp/widget/*`.
- Internal full‑view chat screen endpoints live under `/whatsapp/fullview/*` (`auth='user'`, scoped to the current user's company account).
- Incoming media (images/audio/video/documents) requires `webhookBase64: true` on the Evolution webhook config (set automatically by **Set Webhook**) so files arrive inlined instead of as an encrypted `mediaKey`.

## Technical Support

