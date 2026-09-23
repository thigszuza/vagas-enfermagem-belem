
from datetime import datetime
from typing import Optional
from sqlmodel import Field, SQLModel


class JobBase(SQLModel):
  title: str
  hospital_or_company: str
  location: str = "Belém, PA"
  neighborhood: Optional[str] = None
  shift_type: Optional[str] = None
  specialty: Optional[str] = None
  salary: Optional[float] = None
  description: str
  url_apply: str
  source: str
  created_at: datetime = Field(default_factory=datetime.utcnow)


class Job(JobBase, table=True):
  id: Optional[int] = Field(default=None, primary_key=True)


class JobCreate(JobBase):
  pass
