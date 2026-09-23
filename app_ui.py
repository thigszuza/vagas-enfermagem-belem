import streamlit as st
from sqlmodel import Session, create_engine, select
from models import Job

sqlite_url = "sqlite:///vagas_enfermagem.db"
engine = create_engine(sqlite_url, echo=False)

st.set_page_config(
    page_title="Vagas Enfermagem Belém 💕",
    page_icon="🎀",
    layout="wide"
)

# Estilização: Fundo suave, fontes em branco nos filtros e cards rosa
st.markdown("""
<style>
    /* Fundo da tela */
    .stApp {
        background-color: #FFF6F8;
    }
    
    /* Barra lateral */
    [data-testid="stSidebar"] {
        background-color: #FF85A2 !important;
        border-right: 2px solid #FF5C8A;
    }

    /* Títulos e rótulos da barra lateral em BRANCO NÍTIDO */
    [data-testid="stSidebar"] h1, 
    [data-testid="stSidebar"] h2, 
    [data-testid="stSidebar"] h3, 
    [data-testid="stSidebar"] p, 
    [data-testid="stSidebar"] label,
    [data-testid="stSidebar"] span {
        color: #FFFFFF !important;
        font-weight: 700 !important;
        text-shadow: 0px 1px 2px rgba(0, 0, 0, 0.25);
    }

    /* Caixa interna dos seletores da barra lateral */
    [data-testid="stSidebar"] [data-baseweb="select"] > div {
        background-color: #9C1343 !important;
        color: #FFFFFF !important;
        border: 1px solid #FFB6C1 !important;
        border-radius: 10px !important;
    }

    /* Texto dentro do selectbox */
    [data-testid="stSidebar"] [data-baseweb="select"] * {
        color: #FFFFFF !important;
        font-weight: 600 !important;
    }

    /* Cartão de vaga */
    .job-card {
        background: #FFFFFF;
        border: 2px solid #FFCCD7;
        border-radius: 18px;
        padding: 22px;
        margin-bottom: 24px;
        box-shadow: 0 4px 14px rgba(255, 182, 193, 0.28);
        transition: transform 0.2s ease, box-shadow 0.2s ease;
    }
    .job-card:hover {
        transform: translateY(-2px);
        box-shadow: 0 6px 18px rgba(255, 154, 179, 0.35);
        border-color: #FF85A2;
    }

    .job-title {
        color: #C2185B;
        font-size: 1.3rem;
        font-weight: 700;
        margin-bottom: 6px;
    }
    .job-meta {
        color: #880E4F;
        font-size: 0.95rem;
        margin-bottom: 12px;
    }
    .badge {
        display: inline-block;
        background-color: #FFE0E9;
        color: #AD1457;
        padding: 4px 12px;
        border-radius: 14px;
        font-size: 0.82rem;
        font-weight: 600;
        margin-right: 6px;
        margin-bottom: 6px;
    }
    
    /* Caixa de mensagem discreta e carinhosa */
    .love-box {
        background-color: #FFF0F5;
        border-left: 3px solid #FF69B4;
        border-radius: 8px;
        padding: 8px 12px;
        margin: 14px 0 16px 0;
        color: #9C27B0;
        font-size: 0.84rem;
        font-style: italic;
    }

    /* Botão de redirecionamento */
    .apply-btn {
        display: inline-block;
        background: linear-gradient(135deg, #FF69B4, #FF1493);
        color: white !important;
        padding: 10px 22px;
        border-radius: 22px;
        text-decoration: none !important;
        font-weight: bold;
        font-size: 0.92rem;
        box-shadow: 0 3px 8px rgba(255, 20, 147, 0.3);
        transition: opacity 0.2s ease;
    }
    .apply-btn:hover {
        opacity: 0.92;
    }
</style>
""", unsafe_allow_html=True)

# Cabeçalho com Hello Kitty e Boas-Vindas
col_img, col_title = st.columns([1, 7])
with col_img:
    st.markdown("""
    <div style="text-align: center; margin-top: 5px;">
        <img src="https://upload.wikimedia.org/wikipedia/en/0/05/Hello_kitty_character_portrait.png" 
             style="width: 82px; height: auto; border-radius: 12px; filter: drop-shadow(0 4px 6px rgba(255,105,180,0.3));">
    </div>
    """, unsafe_allow_html=True)

with col_title:
    st.markdown("<h1 style='color: #C2185B; margin-bottom: 0;'>Portal de Vagas de Enfermagem 💕</h1>", unsafe_allow_html=True)
    st.markdown("<p style='color: #880E4F; font-size: 1.05rem;'>As melhores oportunidades em Belém reunidas em um só lugar 🌸</p>", unsafe_allow_html=True)

st.divider()

# Barra lateral com filtros
st.sidebar.markdown("### 🎀 Filtrar Vagas")
filtro_esp = st.sidebar.selectbox(
    "Especialidade",
    ["Todas", "Enfermagem Geral", "Técnico de Enfermagem", "UTI", "Centro Cirúrgico", "Urgência/Emergência", "Pediatria"]
)
filtro_turno = st.sidebar.selectbox(
    "Escala / Turno",
    ["Todos", "12x36", "Diurno", "Noturno", "A combinar"]
)

# Consulta com filtros
with Session(engine) as session:
    query = select(Job)
    if filtro_esp != "Todas":
        query = query.where(Job.specialty == filtro_esp)
    if filtro_turno != "Todos":
        query = query.where(Job.shift_type == filtro_turno)
    
    vagas = session.exec(query.order_by(Job.created_at.desc())).all()

st.markdown(f"<h4 style='color: #AD1457;'>🩺 Oportunidades Encontradas: <b>{len(vagas)}</b></h4>", unsafe_allow_html=True)

if not vagas:
    st.info("Nenhuma vaga encontrada para os filtros selecionados. Experimente limpar os filtros ou adicionar uma vaga de teste abaixo!")
else:
    for v in vagas:
        link = v.url_apply if v.url_apply.startswith("http") else f"https://{v.url_apply}"
        st.markdown(f"""
        <div class="job-card">
            <div class="job-title">💖 {v.title}</div>
            <div class="job-meta">
                🏥 <b>{v.hospital_or_company}</b> &nbsp;•&nbsp; 📍 {v.location}
            </div>
            <div>
                <span class="badge">🏷️ {v.specialty}</span>
                <span class="badge">⏰ {v.shift_type}</span>
                <span class="badge">🌐 {v.source}</span>
            </div>
            <p style="color: #4A4A4A; margin-top: 10px; font-size: 0.94rem; line-height: 1.45;">
                {v.description}
            </p>
            <div class="love-box">
                💌 Amor, fiz com carinho esse app pra você, sinal do meu amor pra você. Me avise se tiver algum erro!
            </div>
            <a href="{link}" target="_blank" rel="noopener noreferrer" class="apply-btn">
                Acessar Vaga e Candidatar-se 🔗
            </a>
        </div>
        """, unsafe_allow_html=True)

# Formulário para adicionar vagas manualmente
with st.expander("🌸 Adicionar uma nova vaga (Manual)"):
    with st.form("form_vaga_rosa"):
        titulo = st.text_input("Título da Oportunidade", value="Enfermeira Assistencial")
        empresa = st.text_input("Hospital ou Instituição", value="Hospital Ophir Loyola")
        local = st.text_input("Local", value="Belém - PA")
        especialidade = st.selectbox("Área", ["Enfermagem Geral", "Técnico de Enfermagem", "UTI", "Centro Cirúrgico", "Urgência/Emergência", "Pediatria"])
        turno = st.selectbox("Escala", ["12x36", "Diurno", "Noturno", "A combinar"])
        url = st.text_input("Link de Inscrição / Detalhes", value="https://exemplo.com")
        descricao = st.text_area("Descrição das Atividades e Requisitos", value="Atendimento assistencial e COREN-PA regular.")
        
        btn = st.form_submit_button("Guardar Vaga 💕")
        if btn:
            nova = Job(
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
                session.add(nova)
                session.commit()
            st.success("Vaga registada com sucesso!")
            st.rerun()