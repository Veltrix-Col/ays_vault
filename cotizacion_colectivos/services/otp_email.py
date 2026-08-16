from __future__ import annotations

from dataclasses import dataclass
from django.utils import formats, timezone
from django.utils.html import escape


@dataclass(frozen=True)
class OTPEmail:
    subject: str
    text_body: str
    html_body: str


def build_otp_email(code: str, *, expires_at) -> OTPEmail:
    """Build the short-lived OTP message without persisting or logging its value."""

    safe_code = escape(str(code))
    local_expiry = timezone.localtime(expires_at)
    expiry_label = formats.date_format(local_expiry, "DATETIME_FORMAT")
    subject = "Código de verificación · A&S Seguros"
    text_body = (
        "A&S Seguros\n\n"
        "Código de verificación\n\n"
        "Se solicitó acceso a un formulario seguro de A&S Seguros.\n\n"
        f"Tu código es: {code}\n\n"
        f"Este código será válido hasta el {expiry_label}, mientras el enlace permanezca vigente.\n"
        "No compartas este código con otras personas.\n\n"
        "Si no solicitaste este acceso, puedes ignorar este mensaje.\n\n"
        "A&S Seguros"
    )
    html_body = f"""<!doctype html>
<html lang="es">
<body style="margin:0;padding:0;background:#f3f6f9;font-family:Arial,Helvetica,sans-serif;color:#152036;">
  <table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0" style="background:#f3f6f9;">
    <tr><td align="center" style="padding:28px 12px;">
      <table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0" style="max-width:560px;background:#ffffff;border:1px solid #dce3ea;border-radius:12px;">
        <tr><td style="padding:24px 28px;background:#152036;color:#ffffff;border-radius:12px 12px 0 0;font-size:20px;font-weight:700;">A&amp;S Seguros</td></tr>
        <tr><td style="padding:30px 28px;">
          <h1 style="margin:0 0 14px;font-size:24px;line-height:1.25;color:#152036;">Código de verificación</h1>
          <p style="margin:0 0 22px;font-size:16px;line-height:1.55;color:#43536a;">Se solicitó acceso a un formulario seguro de A&amp;S Seguros.</p>
          <p style="margin:0 0 8px;font-size:14px;color:#607086;">Tu código es:</p>
          <div style="margin:0 0 24px;padding:18px;text-align:center;background:#eef6fb;border:1px solid #b9d8ea;border-radius:10px;color:#0b5f8a;font-size:34px;line-height:1;letter-spacing:8px;font-weight:700;">{safe_code}</div>
          <p style="margin:0 0 10px;font-size:15px;line-height:1.55;color:#43536a;">Este código será válido hasta el <strong>{escape(expiry_label)}</strong>, mientras el enlace permanezca vigente.</p>
          <p style="margin:0 0 18px;font-size:15px;line-height:1.55;color:#43536a;">No compartas este código con otras personas.</p>
          <p style="margin:0;font-size:13px;line-height:1.55;color:#738095;">Si no solicitaste este acceso, puedes ignorar este mensaje.</p>
        </td></tr>
        <tr><td style="padding:18px 28px;border-top:1px solid #e5eaf0;color:#607086;font-size:13px;">A&amp;S Seguros</td></tr>
      </table>
    </td></tr>
  </table>
</body>
</html>"""
    return OTPEmail(subject=subject, text_body=text_body, html_body=html_body)
