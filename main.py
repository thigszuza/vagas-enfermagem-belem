from typing import List, Optional
from fastapi import Depends, FastAPI, Query
from sqlmodel import Session, SQLModel, create_engine, select
from models import Job, JobCreate

sqlite_url = "sqlite:///vagas_enfermagem.db"
engine = create_engine(sqlite_url, echo=False)

app = FastAPI(title="API Vagas Enfermagem Belém")


@app.on_event("startup")
def on_startup():
  SQLModel.metadata.create_all(engine)


def get_session():
  with Session(engine) as session:
    yield session


@app.post("/vagas/", response_model=Job)
def create_job(job: JobCreate, session: Session = Depends(get_session)):
  db_job = Job.from_orm(job)
  session.add(db_job)
  session.commit()
  session.refresh(db_job)
  return db_job


@app.get("/vagas/", response_model=List[Job])
def list_jobs(
    specialty: Optional[str] = Query(None),
    shift_type: Optional[str] = Query(None),
    min_salary: Optional[float] = Query(None),
    session: Session = Depends(get_session),
):
  query = select(Job)
  if specialty:
    query = query.where(Job.specialty == specialty)
  if shift_type:
    query = query.where(Job.shift_type == shift_type)
  if min_salary:
    query = query.where(Job.salary >= min_salary)

  return session.exec(query.order_by(Job.created_at.desc())).all()
