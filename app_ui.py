import io
import random
import re
import unicodedata
import urllib.parse
from datetime import datetime, timedelta

import streamlit as st
import streamlit.components.v1 as components
from pypdf import PdfReader
from sqlalchemy import text
from sqlmodel import Session, SQLModel, create_engine, select

from models import Job, UserProfile, UserSubscription
from scrapers_belem import ScraperHospitaisBelem

# --- INICIALIZAÇÃO DO BANCO E MIGRAÇÃO ---
sqlite_url = "sqlite:///vagas_enfermagem.db"
engine = create_engine(sqlite_url, echo=False)
SQLModel.metadata.create_all(engine)

with engine.connect() as conn:
    colunas_job = [
        ("state", "VARCHAR DEFAULT 'PA'"),
        ("category", "VARCHAR DEFAULT 'Enfermagem'"),
        ("requires_graduation", "BOOLEAN DEFAULT 0"),
        ("status", "VARCHAR DEFAULT 'Disponível'"),
    ]
    for col_nome, col_tipo in colunas_job:
        try:
            conn.execute(text(f"ALTER TABLE job ADD COLUMN {col_nome} {col_tipo}"))
            conn.commit()
        except Exception:
            pass

    try:
        conn.execute(
            text("ALTER TABLE userprofile ADD COLUMN is_biomed_graduated BOOLEAN DEFAULT 0")
        )
        conn.commit()
    except Exception:
        pass

# --- MOTOR DE VAGAS 24 HORAS (TODAS AS ÁREAS - PA & SP) ---
PORTAIS = ["Gupy Saúde", "Vagas.com", "LinkedIn", "Catho Hospitalar", "InfoJobs", "Portal Direto RH"]

CATALOGO_24H = [
    # ENFERMAGEM - BELÉM / ANANINDEUA
    {
        "title": "Enfermeira Assistencial - UTI Adulto",
        "hospital_or_company": "Hospital Porto Dias",
        "location": "Marco, Belém - PA",
        "state": "PA", "category": "Enfermagem", "shift_type": "12x36", "specialty": "UTI",
        "description": "Assistência intensiva a pacientes críticos, cálculo e infusão de drogas vasoativas, passagem de sondas e supervisão da equipe técnica.",
        "url_apply": "https://portodias.gupy.io", "source": "Gupy Saúde", "requires_graduation": True
    },
    {
        "title": "Técnico de Enfermagem - Centro Cirúrgico & CME",
        "hospital_or_company": "Hospital Ophir Loyola",
        "location": "São Brás, Belém - PA",
        "state": "PA", "category": "Enfermagem", "shift_type": "12x36", "specialty": "Centro Cirúrgico",
        "description": "Instrumentação cirúrgica, paramentação estéril, controle de materiais em CME e recuperação pós-anestésica.",
        "url_apply": "http://www.ophirloyola.pa.gov.br", "source": "Portal Direto RH", "requires_graduation": False
    },
    {
        "title": "Enfermeiro(a) - Urgência e Emergência (Pronto Atendimento)",
        "hospital_or_company": "Hospital Metropolitano (HMUE)",
        "location": "BR-316, Ananindeua - PA",
        "state": "PA", "category": "Enfermagem", "shift_type": "12x36", "specialty": "Urgência/Emergência",
        "description": "Acolhimento com Classificação de Risco (Manchester), estabilização de politraumatizados e apoio em sala vermelha.",
        "url_apply": "https://institutoabadiania.org.br/trabalhe-conosco", "source": "Vagas.com", "requires_graduation": True
    },
    {
        "title": "Enfermeira Pediátrica & Neonatal",
        "hospital_or_company": "Hospital Santa Casa de Misericórdia do Pará",
        "location": "Umarizal, Belém - PA",
        "state": "PA", "category": "Enfermagem", "shift_type": "12x36", "specialty": "Pediatria",
        "description": "Cuidados assistenciais humanizados na UCI e UTI Neonatal, punção de acesso venoso periférico pediátrico e apoio ao aleitamento materno.",
        "url_apply": "http://santacasa.pa.gov.br/trabalhe-conosco", "source": "Portal Direto RH", "requires_graduation": True
    },
    {
        "title": "Técnico(a) de Enfermagem - Hemodiálise & Nefrologia",
        "hospital_or_company": "Clínica de Doenças Renais de Belém",
        "location": "Nazaré, Belém - PA",
        "state": "PA", "category": "Enfermagem", "shift_type": "Diurno", "specialty": "Nefrologia",
        "description": "Montagem e priming de linhas de diálise, monitorização de sinais vitais e fístula arteriovenosa durante sessão hemodialítica.",
        "url_apply": "https://www.linkedin.com/jobs", "source": "LinkedIn", "requires_graduation": False
    },
    # BIOMEDICINA - BELÉM
    {
        "title": "Biomédica Analista - Hematologia e Bioquímica Clínica",
        "hospital_or_company": "Laboratório Beneficente de Belém",
        "location": "Nazaré, Belém - PA",
        "state": "PA", "category": "Biomedicina", "shift_type": "Diurno", "specialty": "Análises Clínicas",
        "description": "Rotina de bancada automatizada, microscopia para contagem diferencial de leucócitos, controle de qualidade (CQI/CQE) e liberação de laudos. CRBM ativo.",
        "url_apply": "https://trabalheconosco.vagas.com.br", "source": "Vagas.com", "requires_graduation": True
    },
    {
        "title": "Auxiliar Técnico de Coleta e Triagem Laboratorial",
        "hospital_or_company": "Laboratório Ruth Brazão",
        "location": "Batista Campos, Belém - PA",
        "state": "PA", "category": "Biomedicina", "shift_type": "Diurno", "specialty": "Coleta e Triagem",
        "description": "Punção venosa à vácuo, coleta pediátrica, centrifugação e envio de amostras biológicas. Aberto a graduandos ou recém-formados.",
        "url_apply": "https://ruthbrazao.com.br/trabalhe-conosco", "source": "InfoJobs", "requires_graduation": False
    },
    {
        "title": "Biomédico(a) - Biologia Molecular & Microbiologia",
        "hospital_or_company": "Laboratório Paulo C. Azevedo",
        "location": "Umarizal, Belém - PA",
        "state": "PA", "category": "Biomedicina", "shift_type": "Diurno", "specialty": "Biologia Molecular",
        "description": "Extração de material genético, PCR em tempo real, cultura de patógenos e antibiograma automatizado.",
        "url_apply": "https://labpauloazevedo.com.br/trabalhe-conosco", "source": "Portal Direto RH", "requires_graduation": True
    },
    # SÃO PAULO - ENFERMAGEM
    {
        "title": "Enfermeira de Cuidados Avançados - Clínica Médica",
        "hospital_or_company": "Hospital Israelita Albert Einstein",
        "location": "Morumbi, São Paulo - SP",
        "state": "SP", "category": "Enfermagem", "shift_type": "12x36", "specialty": "Enfermagem Geral",
        "description": "Gestão de casos clínicos complexos, aplicação de práticas baseadas em evidências e garantia dos mais altos padrões de segurança do paciente.",
        "url_apply": "https://einstein.gupy.io", "source": "Gupy Saúde", "requires_graduation": True
    },
    {
        "title": "Técnico de Enfermagem - UTI Cardíaca e Hemodinâmica",
        "hospital_or_company": "Hospital Sírio-Libanês",
        "location": "Bela Vista, São Paulo - SP",
        "state": "SP", "category": "Enfermagem", "shift_type": "12x36", "specialty": "UTI",
        "description": "Assistência em recuperação pós-cateterismo e cirurgia cardiovascular, balão intra-aórtico e suporte multiprofissional.",
        "url_apply": "https://hospitalsiriolibanes.gupy.io", "source": "Gupy Saúde", "requires_graduation": False
    },
    {
        "title": "Enfermeira - Oncologia Clínica e Quimioterapia",
        "hospital_or_company": "A.C.Camargo Cancer Center",
        "location": "Liberdade, São Paulo - SP",
        "state": "SP", "category": "Enfermagem", "shift_type": "Diurno", "specialty": "Oncologia",
        "description": "Administração segura de quimioterápicos, manejo de efeitos adversos, curativos de cateteres venosos centrais (Port-a-Cath / PICC).",
        "url_apply": "https://accamargo.gupy.io", "source": "Gupy Saúde", "requires_graduation": True
    },
    {
        "title": "Enfermeiro(a) Navegador - Pronto-Socorro Infantil",
        "hospital_or_company": "Hospital Infantil Sabará",
        "location": "Higienópolis, São Paulo - SP",
        "state": "SP", "category": "Enfermagem", "shift_type": "12x36", "specialty": "Pediatria",
        "description": "Atendimento pediátrico humanizado, apoio à família em emergências infantis e aplicação de protocolos pediátricos internacionais.",
        "url_apply": "https://hospitalinfantilsabara.gupy.io", "source": "Catho Hospitalar", "requires_graduation": True
    },
    # SÃO PAULO - BIOMEDICINA
    {
        "title": "Biomédica Especialista - Genética & Biologia Molecular",
        "hospital_or_company": "Grupo Fleury Diagnósticos",
        "location": "Jabaquara, São Paulo - SP",
        "state": "SP", "category": "Biomedicina", "shift_type": "Diurno", "specialty": "Biologia Molecular",
        "description": "Sequenciamento de Nova Geração (NGS), RT-PCR para painéis infecciosos e oncológicos e validação clínica de relatórios moleculares.",
        "url_apply": "https://fleury.gupy.io", "source": "Gupy Saúde", "requires_graduation": True
    },
    {
        "title": "Analista de Imunologia e Sorologia Clínica",
        "hospital_or_company": "Dasa Diagnósticos da América",
        "location": "Barueri / São Paulo - SP",
        "state": "SP", "category": "Biomedicina", "shift_type": "12x36", "specialty": "Imunologia",
        "description": "Controle operacional de plataformas de quimioluminescência e imunofluorimetria, calibragem de ensaios e controle estatístico de bancada.",
        "url_apply": "https://dasa.gupy.io", "source": "Gupy Saúde", "requires_graduation": True
    },
    {
        "title": "Biomédico(a) de Plantão - Análises Clínicas Hospitalares",
        "hospital_or_company": "Hospital das Clínicas da FMUSP",
        "location": "Cerqueira César, São Paulo - SP",
        "state": "SP", "category": "Biomedicina", "shift_type": "12x36", "specialty": "Análises Clínicas",
        "description": "Rotina analítica de urgência hospitalar (gases sanguíneos, coagulação, enzimas cardíacas e líquor). Assinatura de laudos emergenciais.",
        "url_apply": "https://www.vagas.com.br/hc-fmusp", "source": "Vagas.com", "requires_graduation": True
    },
    # MULTIPROFISSIONAL / GERAL EM SAÚDE
    {
        "title": "Farmacêutica Hospitalar - Dispensação e Dose Unitária",
        "hospital_or_company": "Hospital Guadalupe",
        "location": "São Brás, Belém - PA",
        "state": "PA", "category": "Saúde Geral", "shift_type": "Diurno", "specialty": "Farmácia",
        "description": "Fracionamento de medicamentos, validação de prescrições hospitalares, farmacovigilância e controle de psicotrópicos.",
        "url_apply": "https://hospitalguadalupe.com.br", "source": "Portal Direto RH", "requires_graduation": True
    },
    {
        "title": "Recepcionista Hospitalar - Atendimento e Triagem",
        "hospital_or_company": "Hospital São Camilo",
        "location": "Pompeia, São Paulo - SP",
        "state": "SP", "category": "Saúde Geral", "shift_type": "12x36", "specialty": "Atendimento Hospitalar",
        "description": "Abertura de fichas de atendimento emergencial, autorização junto a convênios médicos e acolhimento presencial na recepção central.",
        "url_apply": "https://saocamilo.gupy.io", "source": "InfoJobs", "requires_graduation": False
    }
]

def auto_alimentar_banco_24h():
    with Session(engine) as session:
        existentes = session.exec(select(Job)).all()
        urls_cadastradas = {j.url_apply for j in existentes}
        
        novos = 0
        for item in CATALOGO_24H:
            if item["url_apply"] not in urls_cadastradas:
                tempo_min = random.randint(5, 180)
                job = Job(
                    title=item["title"],
                    hospital_or_company=item["hospital_or_company"],
                    location=item["location"],
                    state=item["state"],
                    category=item["category"],
                    shift_type=item["shift_type"],
                    specialty=item["specialty"],
                    description=item["description"],
                    url_apply=item["url_apply"],
                    source=item["source"],
                    status="Disponível",
                    requires_graduation=item["requires_graduation"],
                    created_at=datetime.utcnow() - timedelta(minutes=tempo_min)
                )
                session.add(job)
                novos += 1
        if novos > 0:
            session.commit()

auto_alimentar_banco_24h()

st.set_page_config(
    page_title="Portal de Carreiras em Saúde & Biomedicina 💕",
    page_icon="🎀",
    layout="wide",
    initial_sidebar_state="expanded",
)

# Toast surpresa
frases_toasts = [
    "eu te amo ou eu te lobo <3",
    "Você vai longe, meu bem! Orgulho imenso do seu esforço 💕",
    "Belém ou São Paulo: seu talento cabe no mundo inteiro! ✨",
    "eu te lobo infinito <3 🐾",
]
st.toast(f"💌 {random.choice(frases_toasts)}", icon="🎀")

# --- FUNÇÕES DE MATCH E IA GEMINI ---
def normalizar_texto(txt: str) -> str:
    if not txt:
        return ""
    nfkd = unicodedata.normalize("NFKD", txt)
    return "".join([c for c in nfkd if not unicodedata.combining(c)]).lower()

def extrair_texto_pdf(arquivo_bytes) -> str:
    try:
        reader = PdfReader(io.BytesIO(arquivo_bytes))
        texto = ""
        for pagina in reader.pages:
            ext = pagina.extract_text()
            if ext:
                texto += ext + " "
        return texto
    except Exception:
        return ""

PALAVRAS_CHAVE = [
    "uti", "centro cirurgico", "urgencia", "emergencia", "pediatria",
    "neonatal", "hemodialise", "oncologia", "pronto socorro", "coren",
    "crbm", "tecnico de enfermagem", "enfermeiro", "enfermeira",
    "biomedico", "biomedica", "analises clinicas", "bancada", "coleta",
    "hematologia", "bioquimica", "microbiologia", "imunologia",
    "biologia molecular", "sorologia", "laudos", "auditoria", "farmacia"
]

def extrair_keywords(texto: str) -> list:
    t_norm = normalizar_texto(texto)
    encontradas = set()
    for kw in PALAVRAS_CHAVE:
        if re.search(r"\b" + re.escape(kw) + r"\b", t_norm):
            encontradas.add(kw)
    return list(encontradas)

def calcular_match(vaga: Job, perfil_keywords: list) -> int:
    if not perfil_keywords:
        return 0
    texto_vaga = normalizar_texto(
        f"{vaga.title} {vaga.description} {vaga.specialty} {vaga.hospital_or_company}"
    )
    acertos = sum(1 for kw in perfil_keywords if kw in texto_vaga)
    score = int((acertos / max(len(perfil_keywords), 1)) * 100)
    return min(score * 2, 100)

def simular_analise_ia_thiago(vaga: Job, perfil_kws: list) -> str:
    match_perc = calcular_match(vaga, perfil_kws)
    pontos_fortes = [kw.upper() for kw in perfil_kws if kw in normalizar_texto(f"{vaga.description} {vaga.title}")]
    
    msg = "🐾 **Oi meu amor! Aqui é a Hello Kitty falando em nome do Thiago!** 💕\n\n"
    msg += f"Analisei os detalhes da vaga de **{vaga.title}** no **{vaga.hospital_or_company}**:\n\n"
    
    if match_perc >= 50:
        msg += f"✨ **Afinidade Alta ({match_perc}%):** Essa oportunidade combina muito com seu perfil! "
        if pontos_fortes:
            msg += f"Eles valorizam competências em **{', '.join(pontos_fortes)}**. "
        msg += "Destaque suas experiências práticas e seu rigor técnico.\n\n"
    else:
        msg += f"🌱 **Oportunidade Promissora ({match_perc}%):** Ótima opção para ampliar sua experiência! "
        msg += "No processo seletivo, evidencie sua atenção aos detalhes, compromisso e rápida curva de aprendizado.\n\n"
        
    msg += f"📍 **Dica de Deslocamento:** Localizada em {vaga.location}. Planeje com antecedência a rota para não ter preocupações de trânsito no dia da entrevista!\n\n"
    msg += "💌 *'Você é extremamente competente, cuidadosa e dedicada. Confie no seu potencial, estou na torcida sempre!'* — Com amor, Thiago Zuza."
    return msg

# --- ESTILIZAÇÃO CSS COM CONTRASTE TOTAL NAS ABAS E INPUTS ---
st.markdown("""
<style>
    .stApp {
        background-color: #FFF6F8;
    }
    [data-testid="stSidebar"] {
        background-color: #FF85A2 !important;
        border-right: 2px solid #FF5C8A;
    }
    [data-testid="stSidebar"] h1, [data-testid="stSidebar"] h2, 
    [data-testid="stSidebar"] h3, [data-testid="stSidebar"] p, 
    [data-testid="stSidebar"] label, [data-testid="stSidebar"] span {
        color: #FFFFFF !important;
        font-weight: 700 !important;
        text-shadow: 0px 1px 2px rgba(0, 0, 0, 0.25);
    }
    [data-testid="stSidebar"] [data-baseweb="select"] > div {
        background-color: #9C1343 !important;
        color: #FFFFFF !important;
        border: 1px solid #FFB6C1 !important;
        border-radius: 10px !important;
    }
    [data-testid="stSidebar"] [data-baseweb="select"] * {
        color: #FFFFFF !important;
        font-weight: 600 !important;
    }
    div[data-baseweb="input"] > div,
    div[data-baseweb="textarea"] > div {
        background-color: #FFFFFF !important;
        border: 2px solid #FFCCD7 !important;
        border-radius: 12px !important;
    }
    div[data-baseweb="input"] input,
    div[data-baseweb="textarea"] textarea {
        color: #4A1525 !important;
        font-weight: 600 !important;
        background-color: transparent !important;
    }
    .stButton > button,
    div[data-testid="stFormSubmitButton"] > button {
        background: linear-gradient(135deg, #FF7597, #E91E63) !important;
        color: #FFFFFF !important;
        border: none !important;
        border-radius: 20px !important;
        font-weight: 700 !important;
        padding: 8px 18px !important;
        box-shadow: 0 3px 8px rgba(233, 30, 99, 0.25) !important;
    }

    /* Regras de Contraste Absoluto para as Abas */
    .stTabs [data-baseweb="tab-list"] {
        gap: 8px;
        background-color: transparent !important;
    }
    .stTabs [data-baseweb="tab"] {
        background-color: #FFE6EE !important;
        border-radius: 12px 12px 0px 0px !important;
        padding: 10px 18px !important;
        border: 1px solid #FFCCD7 !important;
        border-bottom: none !important;
    }
    .stTabs [data-baseweb="tab"] * {
        color: #880E4F !important;
        font-weight: 700 !important;
        font-size: 0.95rem !important;
    }
    .stTabs [aria-selected="true"] {
        background: linear-gradient(135deg, #FF69B4, #E91E63) !important;
        border-color: #E91E63 !important;
    }
    .stTabs [aria-selected="true"] * {
        color: #FFFFFF !important;
        font-weight: 800 !important;
        text-shadow: 0px 1px 2px rgba(0, 0, 0, 0.25) !important;
    }

    .job-card {
        background: #FFFFFF;
        border: 2px solid #FFCCD7;
        border-radius: 18px;
        padding: 22px;
        margin-bottom: 20px;
        box-shadow: 0 4px 14px rgba(255, 182, 193, 0.28);
    }
    .job-title {
        color: #C2185B;
        font-size: 1.25rem;
        font-weight: 700;
    }
    .badge {
        display: inline-block;
        background-color: #FFE0E9;
        color: #AD1457;
        padding: 4px 10px;
        border-radius: 14px;
        font-size: 0.8rem;
        font-weight: 600;
        margin-right: 6px;
    }
    .badge-sp {
        background-color: #E1F5FE;
        color: #0277BD;
        padding: 4px 10px;
        border-radius: 14px;
        font-size: 0.8rem;
        font-weight: 700;
        margin-right: 6px;
    }
    .badge-bio {
        background-color: #E0F2F1;
        color: #00695C;
        padding: 4px 10px;
        border-radius: 14px;
        font-size: 0.8rem;
        font-weight: 700;
        margin-right: 6px;
    }
    .badge-24h {
        background: #FFF3E0;
        color: #E65100;
        border: 1px solid #FFE0B2;
        padding: 4px 10px;
        border-radius: 14px;
        font-size: 0.8rem;
        font-weight: 700;
        margin-right: 6px;
    }
    .action-link {
        display: inline-block;
        background: #FCE4EC;
        color: #C2185B !important;
        padding: 6px 14px;
        border-radius: 16px;
        font-size: 0.85rem;
        font-weight: 600;
        text-decoration: none !important;
        margin-right: 6px;
        border: 1px solid #F8BBD0;
    }
    .emergency-card {
        background: #FFEBEE;
        border-left: 5px solid #D32F2F;
        padding: 14px;
        border-radius: 10px;
        margin-bottom: 15px;
    }
</style>
""", unsafe_allow_html=True)

# --- CABEÇALHO ---
col_img, col_title = st.columns([1, 7])
with col_img:
    st.markdown("""
    <div style="text-align: center; margin-top: 5px;">
        <img src="https://upload.wikimedia.org/wikipedia/en/0/05/Hello_kitty_character_portrait.png" 
             style="width: 82px; height: auto; border-radius: 12px; filter: drop-shadow(0 4px 6px rgba(255,105,180,0.3));">
    </div>
    """, unsafe_allow_html=True)

with col_title:
    st.markdown("<h1 style='color: #C2185B; margin-bottom: 0;'>Portal de Carreiras em Saúde & Biomedicina 💕</h1>", unsafe_allow_html=True)
    st.markdown("<p style='color: #880E4F; font-size: 1.05rem;'>Monitoramento contínuo de oportunidades em Belém e São Paulo com carinho para você 🌸</p>", unsafe_allow_html=True)

st.divider()

# Mensagem Afetiva
st.markdown("""
<div style="background: linear-gradient(90deg, #FFE4EC, #FFF0F5); border: 1px dashed #FF69B4; border-radius: 12px; padding: 10px 16px; text-align: center; color: #C2185B; font-weight: 600; margin-bottom: 16px;">
    🐾 <i>eu te amo ou eu te lobo &lt;3</i> ✨
</div>
""", unsafe_allow_html=True)

# --- BARRA LATERAL ---
st.sidebar.markdown("### 🎀 Localização & Carreira")
filtro_estado = st.sidebar.radio(
    "Estado / Região:",
    ["Todos", "PA (Belém e Região)", "SP (São Paulo Capital)"]
)
filtro_categoria = st.sidebar.radio(
    "Área de Atuação:",
    ["Todas", "Enfermagem", "Biomedicina", "Saúde Geral"]
)
busca_termo = st.sidebar.text_input("🔍 Busca por palavra", placeholder="Ex: Sírio, Einstein, UTI, Bioquímica, Coleta...")

st.sidebar.markdown("---")
st.sidebar.markdown("##### 🥠 Biscoito do Dia")
st.sidebar.info("A dedicação que você coloca em cuidar das pessoas faz a diferença em qualquer equipe hospitalar ou laboratorial! 💕")

if st.sidebar.button("🔄 Sincronizar Portais 24h Agora"):
    with st.spinner("Atualizando feed dos portais hospitalares..."):
        scraper = ScraperHospitaisBelem()
        novas = scraper.coletar_todas()
        qtd = 0
        with Session(engine) as session:
            for v in novas:
                if not session.exec(select(Job).where(Job.url_apply == v["url_apply"])).first():
                    session.add(Job(**v))
                    qtd += 1
            session.commit()
        auto_alimentar_banco_24h()
        st.sidebar.success("Base 24h atualizada!")
        st.rerun()

# --- CONSULTA DAS VAGAS ---
with Session(engine) as session:
    q = select(Job)
    if filtro_estado == "PA (Belém e Região)":
        q = q.where(Job.state == "PA")
    elif filtro_estado == "SP (São Paulo Capital)":
        q = q.where(Job.state == "SP")
        
    if filtro_categoria != "Todas":
        q = q.where(Job.category == filtro_categoria)
        
    if busca_termo:
        t = f"%{busca_termo.strip()}%"
        q = q.where(
            (Job.title.ilike(t)) | 
            (Job.hospital_or_company.ilike(t)) | 
            (Job.description.ilike(t)) |
            (Job.specialty.ilike(t))
        )
        
    vagas_lista = session.exec(q.order_by(Job.created_at.desc())).all()

# Recupera perfil salvo
with Session(engine) as session:
    perfil_user = session.exec(select(UserProfile)).first()
    user_kws = [k.strip() for k in perfil_user.skills_keywords.split(",") if k.strip()] if perfil_user and perfil_user.skills_keywords else []
    is_biomed = perfil_user.is_biomed_graduated if perfil_user else False

# --- ABAS PRINCIPAIS ---
tab_vagas, tab_biomed, tab_rotas_emerg, tab_candidaturas = st.tabs([
    "🌸 Mural Geral de Vagas",
    "🔬 Especial Biomedicina",
    "🗺️ Rotas & Contatos de Emergência",
    "📋 Minhas Candidaturas"
])

# ================= TAB 1: MURAL DE VAGAS =================
with tab_vagas:
    st.markdown(f"<h4 style='color: #AD1457;'>🩺 Oportunidades no Feed 24h: <b>{len(vagas_lista)}</b></h4>", unsafe_allow_html=True)
    
    if not vagas_lista:
        st.info("Nenhuma oportunidade localizada para estes filtros. Tente selecionar 'Todos' na barra lateral!")
    else:
        for v in vagas_lista:
            score = calcular_match(v, user_kws)
            badge_estado = '<span class="badge-sp">🏙️ São Paulo</span>' if getattr(v, "state", "PA") == "SP" else '<span class="badge">🌴 Belém - PA</span>'
            badge_cat = '<span class="badge-bio">🔬 Biomedicina</span>' if getattr(v, "category", "Enfermagem") == "Biomedicina" else '<span class="badge">🩺 Enfermagem</span>'
            
            link_vaga = v.url_apply if v.url_apply.startswith("http") else f"https://{v.url_apply}"
            rota_maps = f"https://www.google.com/maps/dir/?api=1&destination={urllib.parse.quote(f'{v.hospital_or_company} {v.location}')}&travelmode=transit"
            txt_zap = urllib.parse.quote(f"Olha essa oportunidade de {v.title} no {v.hospital_or_company} ({v.location}): {link_vaga}")
            link_zap = f"https://api.whatsapp.com/send?text={txt_zap}"

            st.markdown(f"""
            <div class="job-card">
                <div class="job-title">💖 {v.title}</div>
                <div style="color: #880E4F; font-size: 0.95rem; margin-bottom: 8px;">
                    🏥 <b>{v.hospital_or_company}</b> &nbsp;•&nbsp; 📍 {v.location}
                </div>
                <div style="margin-bottom: 10px;">
                    {badge_estado} {badge_cat} 
                    <span class="badge-24h">🌐 {v.source}</span>
                    <span class="badge">⏰ {v.shift_type}</span>
                    <span class="badge">✨ Afinidade: {score}%</span>
                </div>
                <p style="color: #444; font-size: 0.92rem; line-height: 1.4;">{v.description}</p>
                <div style="margin-top: 10px;">
                    <a href="{link_vaga}" target="_blank" class="action-link" style="background:#FF69B4; color:white !important; font-weight:bold;">Acessar no Portal 🔗</a>
                    <a href="{rota_maps}" target="_blank" class="action-link">🗺️ Simular Rota Maps</a>
                    <a href="{link_zap}" target="_blank" class="action-link">💬 Compartilhar Zap</a>
                </div>
            </div>
            """, unsafe_allow_html=True)

            col_ia, col_fav, _ = st.columns([3, 2, 4])
            with col_ia:
                with st.popover("🎀 Análise do Gemini da Hello Kitty"):
                    st.markdown(simular_analise_ia_thiago(v, user_kws))
            with col_fav:
                if st.button("❤️ Salvar Candidatura", key=f"btn_fav_{v.id}"):
                    with Session(engine) as s:
                        obj = s.get(Job, v.id)
                        obj.status = "Candidatada"
                        s.add(obj)
                        s.commit()
                    st.success("Salva em 'Minhas Candidaturas'!")
                    st.rerun()

# ================= TAB 2: ESPECIAL BIOMEDICINA =================
with tab_biomed:
    st.markdown("### 🔬 Painel Exclusivo de Biomedicina")
    st.info("Espaço dedicado a Análises Clínicas, Biologia Molecular, Imunologia e Diagnósticos Laboratoriais.")

    st.markdown("#### 🎓 Verificação Profissional")
    tem_formacao = st.radio(
        "Em Biomedicina, é necessária a sua formação completa, você já concluiu a graduação?",
        ["Sim, possuo graduação completa e registro no CRBM", "Não, estou cursando / formação em andamento"],
        index=0 if is_biomed else 1
    )

    if st.button("Confirmar Status de Formação"):
        with Session(engine) as s:
            p = s.exec(select(UserProfile)).first()
            grad_status = "Sim" in tem_formacao
            if not p:
                p = UserProfile(is_biomed_graduated=grad_status)
            else:
                p.is_biomed_graduated = grad_status
            s.add(p)
            s.commit()
        st.success("Status de formação atualizado com sucesso!")
        st.rerun()

    if "Não" in tem_formacao:
        st.warning("⚠️ **Atenção:** Vagas para *Biomédica Responsável Técnica*, emissão e assinatura de laudos exigem diploma e registro ativo no CRBM. Enquanto não concluir, priorize oportunidades como **Técnica de Laboratório**, **Auxiliar de Coleta** ou **Estágio em Análises Clínicas**!")
    else:
        st.success("✨ **Elegível:** Você está apta a assumir bancadas analíticas, liberação de laudos e responsabilidade técnica laboratorial.")

    st.divider()

    col_mod1, col_mod2 = st.columns(2)
    with col_mod1:
        st.markdown("#### 📄 Currículo Sugestivo (Biomedicina)")
        st.text_area(
            "Estrutura Pronta:",
            """OBJETIVO: Biomédica - Análises Clínicas / Diagnóstico Laboratorial

RESUMO DE QUALIFICAÇÕES:
• Experiência e proficiência em rotinas de bancada (Hematologia, Bioquímica, Imunologia e Microbiologia).
• Conhecimento na calibração e operação de analisadores automatizados e controle de qualidade (CQI/CQE).
• Registro ativo e regular junto ao CRBM.
• Atenção rigorosa aos procedimentos operacionais padrão (POPs) e biossegurança.

FORMAÇÃO:
• Bacharelado em Biomedicina.""",
            height=220
        )

    with col_mod2:
        st.markdown("#### ✉️ Carta de Apresentação (Biomedicina)")
        st.text_area(
            "Modelo para Envio:",
            """Prezada Coordenação de Laboratório e RH,

Apresento minha candidatura à oportunidade na área de Análises Clínicas.

Possuo sólido domínio dos fluxos laboratoriais pré-analíticos, analíticos e pós-analíticos, atuando com precisão em exames hematológicos, imunológicos e bioquímicos. Prezo pelo rigor metodológico e controle de qualidade para garantir a total confiabilidade dos laudos diagnósticos.

Estou à inteira disposição para entrevista técnica e demonstração de competências de bancada.

Atenciosamente,
Biomédica | Contato WhatsApp""",
            height=220
        )

# ================= TAB 3: ROTAS & EMERGÊNCIA =================
with tab_rotas_emerg:
    st.markdown("### 🗺️ Simulação de Trajeto & Apoio Rápido de Segurança")
    
    col_em1, col_em2 = st.columns(2)
    with col_em1:
        st.markdown("""
        <div class="emergency-card">
            <h4 style="color:#C62828; margin:0 0 10px 0;">🚨 Botão de Segurança p/ Voltar de Plantão</h4>
            <p style="color:#333; font-size:0.9rem;">Saindo de noite ou de madrugada? Clique para mandar mensagem instantânea com aviso de trajeto direto para o Thiago:</p>
            <a href="https://api.whatsapp.com/send?text=Oi%20amor,%20estou%20saindo%20do%20plant%C3%A3o%20agora%20e%20a%20caminho%20de%20casa!%20Te%20aviso%20assim%20que%20chegar%20%E2%9D%A4%EF%B8%8F" 
               target="_blank" class="action-link" style="background:#D32F2F; color:white !important; font-weight:bold; padding:10px 16px;">
                📲 Mandar Aviso de Saída de Plantão p/ Thiago
            </a>
        </div>
        """, unsafe_allow_html=True)
    
    with col_em2:
        st.markdown("""
        #### 📞 Contatos Úteis de Emergência & Saúde:
        * 🚑 **SAMU:** 192
        * 🚓 **Polícia Militar:** 190
        * 🩺 **COREN-PA (Belém):** (91) 3262-6052
        * 🩺 **COREN-SP (Capital):** (11) 3225-6300
        * 🔬 **CRBM-4 (Norte):** (91) 3212-3850
        * 🔬 **CRBM-1 (São Paulo):** (11) 3347-5555
        """)

# ================= TAB 4: CANDIDATURAS =================
with tab_candidaturas:
    st.markdown("### 📋 Painel de Acompanhamento")
    with Session(engine) as session:
        salvas = session.exec(select(Job).where(Job.status == "Candidatada")).all()

    if not salvas:
        st.info("Você ainda não salvou nenhuma candidatura. Marque no mural de vagas!")
    else:
        for c in salvas:
            col_a, col_b = st.columns([5, 2])
            with col_a:
                st.markdown(f"**💖 {c.title}** — {c.hospital_or_company} ({c.location})")
            with col_b:
                if st.button("Remover", key=f"rm_c_{c.id}"):
                    with Session(engine) as s:
                        item = s.get(Job, c.id)
                        item.status = "Disponível"
                        s.add(item)
                        s.commit()
                    st.rerun()
            st.divider()

# --- ASSINATURA E CRÉDITOS ---
st.divider()
st.markdown("""
<div style="text-align: center; color: #AD1457; font-size: 0.95rem; font-weight: 700; padding: 10px;">
    Desenvolvido com todo amor por Thiago Zuza 💕 🐾
</div>
""", unsafe_allow_html=True)
