from datetime import datetime
from typing import Optional
from sqlmodel import Field, SQLModel


class JobBase(SQLModel):
  title: str
  hospital_or_company: str
  location: str
  state: Optional[str] = "PA"  # PA ou SP
  category: Optional[str] = "Enfermagem"  # Enfermagem ou Biomedicina
  shift_type: Optional[str] = "12x36"
  specialty: Optional[str] = "Geral"
  salary: Optional[float] = None
  description: Optional[str] = None
  url_apply: str
  source: Optional[str] = "Web"
  status: Optional[str] = Field(default="Disponível")
  requires_graduation: Optional[bool] = False


class Job(JobBase, table=True):
  __table_args__ = {"extend_existing": True}
  id: Optional[int] = Field(default=None, primary_key=True)
  created_at: datetime = Field(default_factory=datetime.utcnow)


class JobCreate(JobBase):
  pass


class UserSubscription(SQLModel, table=True):
  __table_args__ = {"extend_existing": True}
  id: Optional[int] = Field(default=None, primary_key=True)
  email: str = Field(unique=True)
  name: Optional[str] = "Meu Amor"
  active: bool = True
  created_at: datetime = Field(default_factory=datetime.utcnow)


class UserProfile(SQLModel, table=True):
  __table_args__ = {"extend_existing": True}
  id: Optional[int] = Field(default=None, primary_key=True)
  resume_text: str = ""
  skills_keywords: str = ""
  is_biomed_graduated: bool = False
  updated_at: datetime = Field(default_factory=datetime.utcnow)
  