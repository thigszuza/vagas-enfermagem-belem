from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
import os
import smtplib
from typing import List
from models import Job

# Configurações do SMTP (Exemplo usando Gmail)
# Dica: No Gmail, utilize uma "Senha de App" (App Password) gerada em https://myaccount.google.com/apppasswords
SMTP_HOST = os.getenv("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv(
    "SMTP_USER", ""
)  # Seu e-mail de envio (ex: seuemail@gmail.com)
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")  # Senha de app do Gmail de 16 letras


def formatar_html_vagas(vagas: List[Job], nome_destinatario="Meu Amor"):
  itens_html = ""
  for v in vagas:
    link = (
        v.url_apply
        if v.url_apply.startswith("http")
        else f"https://{v.url_apply}"
    )
    itens_html += f"""
        <div style="background-color: #ffffff; border: 2px solid #ffccd7; border-radius: 12px; padding: 18px; margin-bottom: 16px; box-shadow: 0 3px 6px rgba(255, 182, 193, 0.2);">
            <h3 style="color: #c2185b; margin-top: 0; margin-bottom: 6px;">💖 {v.title}</h3>
            <p style="color: #880e4f; margin: 4px 0; font-size: 14px;">
                <b>🏥 Hospital/Clínica:</b> {v.hospital_or_company} | <b>📍 Local:</b> {v.location}
            </p>
            <p style="color: #666; font-size: 13px; margin: 8px 0;">{v.description}</p>
            <div style="margin: 12px 0;">
                <span style="background: #ffe0e9; color: #ad1457; padding: 4px 10px; border-radius: 10px; font-size: 12px; font-weight: bold; margin-right: 6px;">🏷️ {v.specialty or 'Geral'}</span>
                <span style="background: #ffe0e9; color: #ad1457; padding: 4px 10px; border-radius: 10px; font-size: 12px; font-weight: bold;">⏰ {v.shift_type or 'A combinar'}</span>
            </div>
            <a href="{link}" target="_blank" style="display: inline-block; background-color: #ff69b4; color: #ffffff !important; padding: 10px 20px; border-radius: 20px; text-decoration: none; font-weight: bold; font-size: 13px; margin-top: 8px;">
                Candidatar-se à Vaga 🔗
            </a>
        </div>
        """

  html = f"""
    <!DOCTYPE html>
    <html>
    <head><meta charset="utf-8"></head>
    <body style="font-family: Arial, sans-serif; background-color: #fff6f8; padding: 20px; margin: 0;">
        <div style="max-width: 620px; margin: 0 auto; background-color: #ffffff; border: 2px solid #ff85a2; border-radius: 18px; overflow: hidden;">
            <div style="background-color: #ff85a2; padding: 20px; text-align: center; color: white;">
                <h1 style="margin: 0; font-size: 22px;">🎀 Novas Vagas de Enfermagem em Belém 💕</h1>
                <p style="margin: 6px 0 0 0; font-size: 14px;">Encontrei novas oportunidades especialmente para você!</p>
            </div>
            
            <div style="padding: 20px; background-color: #fff6f8;">
                <p style="font-size: 15px; color: #880e4f;">
                    Oi {nome_destinatario}! 🌸 Fiz essa seleção com muito carinho para que você não perca nenhuma oportunidade em Belém.
                </p>
                
                {itens_html}
                
                <div style="text-align: center; margin-top: 25px; padding-top: 15px; border-top: 1px dashed #ff85a2; color: #ad1457; font-size: 13px;">
                    💌 <i>"Fiz com muito amor esse portal pra você! Se precisar ajustar algo, me avise!"</i>
                </div>
            </div>
        </div>
    </body>
    </html>
    """
  return html


def enviar_boletim_email(destinatario: str, vagas: List[Job]):
  if not SMTP_USER or not SMTP_PASSWORD:
    print(
        "⚠️ Credenciais SMTP não configuradas. Defina SMTP_USER e SMTP_PASSWORD."
    )
    return False

  if not vagas:
    return True

  msg = MIMEMultipart("alternative")
  msg["Subject"] = (
      f"🌸 {len(vagas)} Novas Vagas de Enfermagem em Belém para Você! 💕"
  )
  msg["From"] = f"Vagas Enfermagem Belém <{SMTP_USER}>"
  msg["To"] = destinatario

  corpo_html = formatar_html_vagas(vagas)
  msg.attach(MIMEText(corpo_html, "html"))

  try:
    with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
      server.starttls()
      server.login(SMTP_USER, SMTP_PASSWORD)
      server.sendmail(SMTP_USER, destinatario, msg.as_string())
    print(f"✅ E-mail enviado com sucesso para {destinatario}!")
    return True
  except Exception as e:
    print(f"❌ Erro ao enviar e-mail: {e}")
    return False