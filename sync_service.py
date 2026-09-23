import os
from email_service import enviar_boletim_email
from models import Job, UserSubscription
from scrapers_belem import ScraperHospitaisBelem
from sqlmodel import Session, create_engine, select

sqlite_url = "sqlite:///vagas_enfermagem.db"
engine = create_engine(sqlite_url, echo=False)


def sincronizar_e_notificar():
  print("Iniciando varredura de vagas em Belém...")
  scraper = ScraperHospitaisBelem()
  vagas_encontradas = scraper.coletar_todas()

  vagas_novas = []

  with Session(engine) as session:
    for v_data in vagas_encontradas:
      existente = session.exec(
          select(Job).where(Job.url_apply == v_data["url_apply"])
      ).first()

      if not existente:
        nova = Job(**v_data)
        session.add(nova)
        session.commit()
        session.refresh(nova)
        vagas_novas.append(nova)

    # Dispara e-mail com todas as vagas novas reunidas para as pessoas inscritas
    if vagas_novas:
      assinantes = session.exec(
          select(UserSubscription).where(UserSubscription.active == True)
      ).all()
      for sub in assinantes:
        enviar_boletim_email(sub.email, vagas_novas)

  print(
      f"Sincronização concluída: {len(vagas_novas)} vagas inéditas processadas."
  )


if __name__ == "__main__":
  sincronizar_e_notificar()
  