import streamlit as st
from sqlmodel import Session, create_engine, select
from models import Job, JobCreate

# Conexão direta à base de dados para leitura rápida no Streamlit
sqlite_url = "sqlite:///vagas_enfermagem.db"
engine = create_engine(sqlite_url, echo=False)

st.set_page_config(page_title="Vagas Enfermagem Belém", layout="wide")
st.title("🩺 Vagas de Enfermagem em Belém - PA")

# Barra lateral com filtros e cadastro rápido de teste
st.sidebar.header("🔍 Filtros de Pesquisa")
filtro_esp = st.sidebar.selectbox(
    "Especialidade",
    ["Todas", "Geral", "UTI", "Centro Cirúrgico", "Urgência/Emergência", "Pediatria"]
)
filtro_turno = st.sidebar.selectbox(
    "Turno / Escala", ["Todos", "12x36", "Diurno", "Noturno"]
)

# Consulta com filtros
with Session(engine) as session:
  query = select(Job)
  if filtro_esp != "Todas":
    query = query.where(Job.specialty == filtro_esp)
  if filtro_turno != "Todos":
    query = query.where(Job.shift_type == filtro_turno)

  vagas = session.exec(query.order_by(Job.created_at.desc())).all()

st.subheader(f"Vagas Disponíveis ({len(vagas)})")

if not vagas:
  st.info("Nenhuma vaga registada no momento. Utilize o formulário abaixo para adicionar uma vaga de teste.")
else:
  for v in vagas:
    with st.container():
      st.markdown(f"### {v.title}")
      st.caption(f"🏥 **{v.hospital_or_company}** | 📍 {v.location} | ⏰ {v.shift_type or 'A combinar'} | 🏷️ {v.specialty}")
      st.write(v.description)
      st.markdown(f"[🔗 Aceder à Candidatura]({v.url_apply})")
      st.divider()

# Secção para adicionar uma vaga manualmente (para testes imediatos)
with st.expander("➕ Adicionar Vaga Manualmente (Recrutador / Teste)"):
  with st.form("form_vaga"):
    titulo = st.text_input("Título da Vaga", value="Enfermeiro(a) Plantonista UTI")
    empresa = st.text_input("Hospital / Clínica", value="Hospital Porto Dias")
    local = st.text_input("Localização", value="Belém - PA")
    especialidade = st.selectbox("Especialidade", ["Geral", "UTI", "Centro Cirúrgico", "Urgência/Emergência", "Pediatria"])
    turno = st.selectbox("Turno", ["12x36", "Diurno", "Noturno"])
    url = st.text_input("Link da Vaga", value="https://exemplo.com/vaga")
    descricao = st.text_area("Descrição / Requisitos", value="Experiência prévia em UTI e COREN ativo.")
    
    submetido = st.form_submit_button("Guardar Vaga")
    if submetido:
      nova_vaga = Job(
          title=titulo,
          hospital_or_company=empresa,
          location=local,
          specialty=especialidade,
          shift_type=turno,
          url_apply=url,
          description=descricao,
          source="Cadastro Manual"
      )
      with Session(engine) as session:
        session.add(nova_vaga)
        session.commit()
      st.success("Vaga registada com sucesso!")
      st.rerun()
      