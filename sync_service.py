
from models import Job
from scrapers_belem import ScraperHospitaisBelem
from sqlmodel import Session, SQLModel, create_engine, select

sqlite_url = "sqlite:///vagas_enfermagem.db"
engine = create_engine(sqlite_url, echo=False)

# Garante a criação da tabela na base de dados SQLite
SQLModel.metadata.create_all(engine)


def persistir_vagas():
  coletor = ScraperHospitaisBelem()
  vagas = coletor.coletar_todas()

  salvas = 0
  with Session(engine) as session:
    for dados in vagas:
      existe = session.exec(
          select(Job).where(Job.url_apply == dados["url_apply"])
      ).first()
      if not existe:
        job = Job(**dados)
        session.add(job)
        salvas += 1
    session.commit()

  print(f"[SUCESSO] {salvas} novas vagas inseridas na base de dados.")


if __name__ == "__main__":
  persistir_vagas()