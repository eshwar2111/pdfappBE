"""Email bodies.

Deliberately plain HTML with inline styles — email clients strip <style>
blocks and support no modern CSS. Every message also carries a text
alternative, and the URL appears as visible text so it survives a client that
mangles links.
"""

from __future__ import annotations

from html import escape

from app.email.base import EmailMessage

_WRAPPER = """\
<div style="font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;
            background:#f5f7fa;padding:32px 16px;">
  <div style="max-width:520px;margin:0 auto;background:#ffffff;border:1px solid #e3e8ef;
              border-radius:12px;padding:28px;">
    <p style="margin:0 0 20px;font-size:15px;font-weight:600;color:#1c2430;">
      PDF Intelligence
    </p>
    {body}
    <hr style="border:none;border-top:1px solid #e3e8ef;margin:24px 0 16px;" />
    <p style="margin:0;font-size:12px;color:#8b96a5;">
      {footer}
    </p>
  </div>
</div>"""

_BUTTON = """\
<a href="{url}" style="display:inline-block;background:#557fb4;color:#ffffff;
   text-decoration:none;padding:10px 18px;border-radius:8px;font-size:14px;
   font-weight:500;">{label}</a>"""


def share_invitation(
    *, to: str, document_name: str, owner_name: str, share_url: str, can_comment: bool
) -> EmailMessage:
    action = "view and comment on" if can_comment else "view"
    safe_name = escape(document_name)
    safe_owner = escape(owner_name)

    body = f"""
    <p style="margin:0 0 16px;font-size:15px;color:#1c2430;line-height:1.6;">
      <strong>{safe_owner}</strong> shared a document with you:
      <strong>{safe_name}</strong>
    </p>
    <p style="margin:0 0 20px;font-size:14px;color:#5b6777;line-height:1.6;">
      You can {action} it, and ask questions about its contents. No account is
      needed — just open the link.
    </p>
    <p style="margin:0 0 16px;">{_BUTTON.format(url=escape(share_url, quote=True), label="Open document")}</p>
    <p style="margin:0;font-size:12px;color:#8b96a5;word-break:break-all;">{escape(share_url)}</p>
    """

    text = (
        f"{owner_name} shared a document with you: {document_name}\n\n"
        f"You can {action} it and ask questions about its contents. "
        "No account is needed.\n\n"
        f"{share_url}\n"
    )

    return EmailMessage(
        to=to,
        subject=f"{owner_name} shared “{document_name}” with you",
        html=_WRAPPER.format(
            body=body,
            footer="Anyone with this link can open the document. Do not forward it.",
        ),
        text=text,
    )


def password_reset(*, to: str, name: str, reset_url: str, ttl_minutes: int) -> EmailMessage:
    safe_name = escape(name)

    body = f"""
    <p style="margin:0 0 16px;font-size:15px;color:#1c2430;line-height:1.6;">
      Hi {safe_name}, we received a request to reset your password.
    </p>
    <p style="margin:0 0 20px;font-size:14px;color:#5b6777;line-height:1.6;">
      This link expires in {ttl_minutes} minutes and can be used once.
    </p>
    <p style="margin:0 0 16px;">{_BUTTON.format(url=escape(reset_url, quote=True), label="Reset password")}</p>
    <p style="margin:0;font-size:12px;color:#8b96a5;word-break:break-all;">{escape(reset_url)}</p>
    """

    text = (
        f"Hi {name}, we received a request to reset your password.\n\n"
        f"This link expires in {ttl_minutes} minutes and can be used once:\n"
        f"{reset_url}\n\n"
        "If you did not request this, you can ignore this email — "
        "your password will not change.\n"
    )

    return EmailMessage(
        to=to,
        subject="Reset your PDF Intelligence password",
        html=_WRAPPER.format(
            body=body,
            footer=(
                "If you did not request this, ignore this email — "
                "your password will not change."
            ),
        ),
        text=text,
    )
