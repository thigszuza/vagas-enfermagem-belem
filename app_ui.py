import io
import os
import random
import re
import unicodedata
import urllib.parse
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta

import pandas as pd
import requests
import streamlit as st
import streamlit.components.v1 as components
from pypdf import PdfReader
from sqlalchemy import text
from sqlmodel import Field, Session, SQLModel, create_engine, select

from models import Job, UserProfile, UserSubscription
from scrapers_belem import ScraperHospitaisBelem

# Tenta carregar biblioteca oficial do Google Gemini
try:
    from google import genai
    HAS_GENAI = True
except ImportError:
    HAS_GENAI = False

# --- CONFIGURAÇÃO DA PÁGINA ---
st.set_page_config(
    page_title="Portal de Carreiras em Saúde & Biomedicina 💕",
    page_icon="🎀",
    layout="wide",
    initial_sidebar_state="expanded",
)

# --- METATAGS OPEN GRAPH NO STREAMLIT ---
st.markdown("""
<head>
    <meta property="og:type" content="website" />
    <meta property="og:title" content="Portal de Carreiras em Saúde & Biomedicina 💕" />
    <meta property="og:description" content="Vagas atualizadas 24h em Belém, SP e RJ preparadas com todo carinho para você! 🌸✨" />
    <meta property="og:image" content="https://upload.wikimedia.org/wikipedia/en/0/05/Hello_kitty_character_portrait.png" />
    <meta property="og:image:width" content="300" />
    <meta property="og:image:height" content="300" />
    <meta name="description" content="Oportunidades em Enfermagem e Biomedicina selecionadas com carinho 💕" />
</head>
""", unsafe_allow_html=True)

# --- MODELOS ADICIONAIS DE BANCO ---
class MedicalAppointment(SQLModel, table=True):
    __table_args__ = {"extend_existing": True}
    id: int | None = Field(default=None, primary_key=True)
    title: str
    appointment_type: str
    location: str
    network_provider: str = "Particular"
    estimated_price: float = 0.0
    scheduled_date: str
    scheduled_time: str
    notes: str = ""
    is_completed: bool = False
    created_at: datetime = Field(default_factory=datetime.utcnow)

class MonthlyNeed(SQLModel, table=True):
    __table_args__ = {"extend_existing": True}
    id: int | None = Field(default=None, primary_key=True)
    item_name: str
    category: str
    quantity: int = 1
    estimated_cost: float = 0.0
    month_reference: str
    is_purchased: bool = False
    created_at: datetime = Field(default_factory=datetime.utcnow)

# --- INICIALIZAÇÃO DO BANCO E AUTO-MIGRAÇÃO ---
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
        conn.execute(text("ALTER TABLE medicalappointment ADD COLUMN network_provider VARCHAR DEFAULT 'Particular'"))
        conn.commit()
    except Exception:
        pass

    try:
        conn.execute(text("ALTER TABLE medicalappointment ADD COLUMN estimated_price FLOAT DEFAULT 0.0"))
        conn.commit()
    except Exception:
        pass

    try:
        conn.execute(text("ALTER TABLE userprofile ADD COLUMN is_biomed_graduated BOOLEAN DEFAULT 0"))
        conn.commit()
    except Exception:
        pass

    try:
        conn.execute(text("ALTER TABLE userprofile ADD COLUMN resume_raw_text TEXT DEFAULT ''"))
        conn.commit()
    except Exception:
        pass

# --- CATÁLOGO DE VAGAS 24H (PA, SP, RJ) ---
CATALOGO_24H = [
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
        "title": "Biomédica Especialista - Genética & Biologia Molecular",
        "hospital_or_company": "Grupo Fleury Diagnósticos",
        "location": "Jabaquara, São Paulo - SP",
        "state": "SP", "category": "Biomedicina", "shift_type": "Diurno", "specialty": "Biologia Molecular",
        "description": "Sequenciamento de Nova Geração (NGS), RT-PCR para painéis infecciosos e oncológicos e validação clínica de relatórios moleculares.",
        "url_apply": "https://fleury.gupy.io", "source": "Gupy Saúde", "requires_graduation": True
    },
    {
        "title": "Enfermeira de Terapia Intensiva (CTI Adulto)",
        "hospital_or_company": "Hospital Copa D'Or (Rede D'Or)",
        "location": "Copacabana, Rio de Janeiro - RJ",
        "state": "RJ", "category": "Enfermagem", "shift_type": "12x36", "specialty": "UTI",
        "description": "Assistência de enfermagem a pacientes de alta complexidade em CTI, monitorização hemodinâmica invasiva e protocolos internacionais de segurança.",
        "url_apply": "https://rededor.gupy.io", "source": "Gupy Saúde", "requires_graduation": True
    },
    {
        "title": "Biomédica Analista - Análises Clínicas & Automação",
        "hospital_or_company": "Laboratório Sérgio Franco (Dasa)",
        "location": "Tijuca, Rio de Janeiro - RJ",
        "state": "RJ", "category": "Biomedicina", "shift_type": "Diurno", "specialty": "Análises Clínicas",
        "description": "Rotina técnica de hematologia, bioquímica e imunologia de bancada automatizada. Liberação, checagem e emissão de laudos. CRBM ativo.",
        "url_apply": "https://dasa.gupy.io", "source": "Gupy Saúde", "requires_graduation": True
    }
]

def popular_catalogo_base():
    with Session(engine) as session:
        try:
            urls = {j.url_apply for j in session.exec(select(Job)).all() if getattr(j, "url_apply", None)}
        except Exception:
            urls = set()

        inseridos = 0
        for item in CATALOGO_24H:
            url = item.get("url_apply")
            if url and url not in urls:
                try:
                    tempo_min = random.randint(5, 180)
                    job = Job(
                        title=str(item.get("title", "")),
                        hospital_or_company=str(item.get("hospital_or_company", "")),
                        location=str(item.get("location", "")),
                        state=str(item.get("state", "PA")),
                        category=str(item.get("category", "Enfermagem")),
                        shift_type=str(item.get("shift_type", "12x36")),
                        specialty=str(item.get("specialty", "Geral")),
                        description=str(item.get("description", "")),
                        url_apply=str(url),
                        source=str(item.get("source", "Web")),
                        status="Disponível",
                        requires_graduation=bool(item.get("requires_graduation", False)),
                        created_at=datetime.utcnow() - timedelta(minutes=tempo_min)
                    )
                    session.add(job)
                    session.commit()
                    urls.add(url)
                    inseridos += 1
                except Exception:
                    session.rollback()
        return inseridos

popular_catalogo_base()

# --- CSS COM ALTO CONTRASTE E CORREÇÃO TOTAL DE CAIXAS ESCURAS ---
st.markdown("""
<style>
    .stApp {
        background-color: #FFF6F8 !important;
        color: #33101E !important;
    }

    h1, h2, h3, h4, h5, h6, p, label, span {
        color: #33101E !important;
    }
    
    [data-testid="stSidebar"] {
        background-color: #FF85A2 !important;
        border-right: 2px solid #FF5C8A;
    }
    [data-testid="stSidebar"] h1, [data-testid="stSidebar"] h2, 
    [data-testid="stSidebar"] h3, [data-testid="stSidebar"] h4, 
    [data-testid="stSidebar"] p, [data-testid="stSidebar"] label, 
    [data-testid="stSidebar"] span {
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

    /* ========================================================================= */
    /* BLINDAGEM COMPLETA CONTRA CAIXAS PRETAS EM INPUTS, SELECTS E DATE/TIME     */
    /* ========================================================================= */
    div[data-baseweb="input"],
    div[data-baseweb="input"] > div,
    div[data-baseweb="base-input"],
    div[data-baseweb="select"] > div,
    .stTextInput > div,
    .stTextInput > div > div,
    .stSelectbox > div > div,
    .stDateInput > div > div,
    .stTimeInput > div > div,
    .stNumberInput > div > div {
        background-color: #FFFFFF !important;
        background: #FFFFFF !important;
        border: 2px solid #FFCCD7 !important;
        border-radius: 12px !important;
        color: #1A1A1A !important;
    }

    .stTextInput input,
    div[data-baseweb="input"] input,
    .stDateInput input,
    .stTimeInput input,
    .stNumberInput input {
        background-color: #FFFFFF !important;
        background: #FFFFFF !important;
        color: #1A1A1A !important;
        -webkit-text-fill-color: #1A1A1A !important;
        font-weight: 600 !important;
        font-size: 0.95rem !important;
    }

    /* Textos selecionados em Selectbox e Menus suspensos */
    div[data-baseweb="select"] * {
        color: #1A1A1A !important;
        font-weight: 600 !important;
    }

    ul[data-baseweb="menu"] {
        background-color: #FFFFFF !important;
        border: 1px solid #FFCCD7 !important;
    }
    ul[data-baseweb="menu"] li {
        color: #1A1A1A !important;
        background-color: #FFFFFF !important;
    }
    ul[data-baseweb="menu"] li:hover {
        background-color: #FFE6EE !important;
        color: #C2185B !important;
    }

    /* ÁREA DE UPLOAD DE ARQUIVOS */
    [data-testid="stFileUploader"],
    [data-testid="stFileUploader"] > div,
    [data-testid="stFileUploader"] section,
    [data-testid="stFileUploaderDropzone"] {
        background-color: #FFFFFF !important;
        background: #FFFFFF !important;
        border: 2px dashed #FF85A2 !important;
        border-radius: 14px !important;
    }
    [data-testid="stFileUploader"] span,
    [data-testid="stFileUploader"] small,
    [data-testid="stFileUploader"] p,
    [data-testid="stFileUploaderDropzoneInstructions"] * {
        color: #4A1525 !important;
        font-weight: 600 !important;
    }
    [data-testid="stFileUploader"] button {
        background: #FFF0F5 !important;
        color: #C2185B !important;
        border: 1px solid #FFCCD7 !important;
        border-radius: 16px !important;
        font-weight: 700 !important;
    }

    .doc-display-box {
        background-color: #FFFFFF !important;
        border: 2px solid #FFCCD7 !important;
        border-radius: 14px !important;
        padding: 18px 22px !important;
        height: 250px !important;
        overflow-y: auto !important;
        font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif !important;
        font-size: 0.95rem !important;
        line-height: 1.6 !important;
        color: #111111 !important;
        font-weight: 500 !important;
        white-space: pre-wrap !important;
        box-shadow: 0 2px 8px rgba(0, 0, 0, 0.04) !important;
        margin-bottom: 12px !important;
    }

    .news-card, .appointment-card, .network-card {
        background-color: #FFFFFF !important;
        border: 2px solid #FFCCD7 !important;
        border-radius: 14px !important;
        padding: 16px 20px !important;
        margin-bottom: 14px !important;
        box-shadow: 0 3px 10px rgba(255, 105, 180, 0.08) !important;
    }

    .wellness-card {
        background: #FFFFFF !important;
        border: 2px solid #FFCCD7 !important;
        border-left: 6px solid #FF69B4 !important;
        border-radius: 16px !important;
        padding: 20px !important;
        margin-bottom: 20px !important;
        box-shadow: 0 4px 14px rgba(255, 105, 180, 0.1) !important;
    }

    .reminder-hk-card {
        background: linear-gradient(135deg, #FFFFFF, #FFF0F5) !important;
        border: 2px dashed #FF69B4 !important;
        border-radius: 18px !important;
        padding: 18px !important;
        margin-bottom: 20px !important;
        display: flex;
        align-items: center;
        gap: 16px;
        box-shadow: 0 4px 12px rgba(255, 105, 180, 0.12) !important;
    }

    [data-testid="stAlert"] {
        border-radius: 12px !important;
        border: 1px solid #FFB6C1 !important;
        background-color: #FFFFFF !important;
    }
    [data-testid="stAlert"] * {
        color: #5D1A2F !important;
        font-weight: 600 !important;
    }

    [data-testid="stRadio"] label,
    [data-testid="stRadio"] p,
    [data-testid="stRadio"] span,
    [data-testid="stCheckbox"] label,
    [data-testid="stCheckbox"] span {
        color: #4A1525 !important;
        font-weight: 700 !important;
    }

    .stButton > button,
    .stDownloadButton > button,
    div[data-testid="stFormSubmitButton"] > button,
    div[data-testid="stPopover"] > button {
        background: linear-gradient(135deg, #FF69B4, #E91E63) !important;
        color: #FFFFFF !important;
        border: none !important;
        border-radius: 20px !important;
        font-weight: 700 !important;
        padding: 10px 22px !important;
        box-shadow: 0 4px 10px rgba(233, 30, 99, 0.28) !important;
        transition: all 0.3s ease;
    }
    .stButton > button:hover,
    .stDownloadButton > button:hover,
    div[data-testid="stFormSubmitButton"] > button:hover {
        background: linear-gradient(135deg, #E91E63, #C2185B) !important;
        box-shadow: 0 6px 14px rgba(233, 30, 99, 0.4) !important;
        color: #FFFFFF !important;
    }
    .stButton > button *,
    .stDownloadButton > button *,
    div[data-testid="stFormSubmitButton"] > button *,
    div[data-testid="stPopover"] > button * {
        color: #FFFFFF !important;
        font-weight: 700 !important;
    }

    .stTabs [data-baseweb="tab-list"] {
        gap: 8px !important;
        background-color: transparent !important;
    }
    .stTabs [data-baseweb="tab"] {
        background-color: #FFE6EE !important;
        border: 2px solid #FFCCD7 !important;
        border-radius: 12px 12px 0px 0px !important;
        padding: 10px 18px !important;
        border-bottom: none !important;
    }
    .stTabs [data-baseweb="tab"] * {
        color: #880E4F !important;
        font-weight: 800 !important;
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
        background: #FFFFFF !important;
        border: 2px solid #FFCCD7;
        border-radius: 18px;
        padding: 22px;
        margin-bottom: 20px;
        box-shadow: 0 4px 14px rgba(255, 182, 193, 0.28);
    }
    .job-title {
        color: #C2185B !important;
        font-size: 1.25rem;
        font-weight: 700;
    }
    .badge {
        display: inline-block;
        background-color: #FFE0E9;
        color: #AD1457 !important;
        padding: 4px 10px;
        border-radius: 14px;
        font-size: 0.8rem;
        font-weight: 600;
        margin-right: 6px;
    }
    .badge-sp {
        background-color: #E1F5FE;
        color: #0277BD !important;
        padding: 4px 10px;
        border-radius: 14px;
        font-size: 0.8rem;
        font-weight: 700;
        margin-right: 6px;
    }
    .badge-rj {
        background-color: #FFF9C4;
        color: #E65100 !important;
        padding: 4px 10px;
        border-radius: 14px;
        font-size: 0.8rem;
        font-weight: 700;
        margin-right: 6px;
    }
    .badge-bio {
        background-color: #E0F2F1;
        color: #00695C !important;
        padding: 4px 10px;
        border-radius: 14px;
        font-size: 0.8rem;
        font-weight: 700;
        margin-right: 6px;
    }
    .badge-24h {
        background: #FFF3E0;
        color: #E65100 !important;
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
        background: #FFFFFF;
        border: 2px solid #FFD1DC;
        border-left: 6px solid #FF6584;
        padding: 18px;
        border-radius: 14px;
        margin-bottom: 15px;
        box-shadow: 0 4px 12px rgba(255, 101, 132, 0.08);
    }
    .linkedin-card {
        background: #FFFFFF;
        border: 2px solid #D6E4FF;
        border-left: 6px solid #0077B5;
        padding: 18px;
        border-radius: 14px;
        margin-bottom: 15px;
        box-shadow: 0 4px 12px rgba(0, 119, 181, 0.08);
    }
    .btn-safety-alert {
        display: inline-block;
        background: linear-gradient(135deg, #FF6584, #FF476F);
        color: #FFFFFF !important;
        padding: 12px 24px;
        border-radius: 25px;
        text-decoration: none !important;
        font-weight: 700;
        box-shadow: 0 4px 12px rgba(255, 71, 111, 0.32);
        transition: all 0.3s ease;
    }
    .btn-safety-alert:hover {
        background: linear-gradient(135deg, #FF476F, #E02856);
        box-shadow: 0 6px 16px rgba(255, 71, 111, 0.45);
        color: #FFFFFF !important;
    }
    .btn-contact-direct {
        display: inline-block;
        background: #E8F5E9;
        color: #2E7D32 !important;
        border: 1px solid #C8E6C9;
        padding: 8px 16px;
        border-radius: 18px;
        text-decoration: none !important;
        font-weight: 700;
        font-size: 0.85rem;
    }
</style>
""", unsafe_allow_html=True)

# --- FUNÇÃO DE PREVISÃO DO TEMPO ---
@st.cache_data(ttl=1800)
def obter_previsao_tempo(cidade_nome: str):
    coords = {
        "Belém - PA": {"lat": -1.4558, "lon": -48.4902},
        "São Paulo - SP": {"lat": -23.5505, "lon": -46.6333},
        "Rio de Janeiro - RJ": {"lat": -22.9068, "lon": -43.1729},
    }
    chave = "Belém - PA" if "Belém" in cidade_nome else ("São Paulo - SP" if "São Paulo" in cidade_nome else "Rio de Janeiro - RJ")
    c = coords[chave]
    try:
        url = f"https://api.open-meteo.com/v1/forecast?latitude={c['lat']}&longitude={c['lon']}&current_weather=true&hourly=precipitation_probability,precipitation&timezone=America%2FSao_Paulo"
        res = requests.get(url, timeout=5)
        if res.status_code == 200:
            dados = res.json()
            curr = dados.get("current_weather", {})
            hourly = dados.get("hourly", {})
            probs = hourly.get("precipitation_probability", [0])[:6]
            precips = hourly.get("precipitation", [0.0])[:6]
            max_prob = max(probs) if probs else 0
            max_precip = max(precips) if precips else 0.0
            return {
                "temp": curr.get("temperature", 26),
                "prob_chuva": max_prob,
                "precip": max_precip,
                "status": "OK"
            }
    except Exception:
        pass
    return {"temp": 26.0, "prob_chuva": 30, "precip": 0.0, "status": "Simulado"}

# --- LEITOR DINÂMICO DE NOTÍCIAS 24H (COFEN & CFBM) ---
@st.cache_data(ttl=3600)
def obter_noticias_reais_saude():
    noticias_enf = []
    noticias_bio = []
    headers = {"User-Agent": "Mozilla/5.0"}

    try:
        r_enf = requests.get("https://www.cofen.gov.br/feed/", headers=headers, timeout=6)
        if r_enf.status_code == 200:
            root = ET.fromstring(r_enf.content)
            for item in root.findall(".//item")[:3]:
                t = item.find("title").text if item.find("title") is not None else "Atualização Cofen"
                l = item.find("link").text if item.find("link") is not None else "https://www.cofen.gov.br"
                d = item.find("pubDate").text[:16] if item.find("pubDate") is not None else ""
                noticias_enf.append({"titulo": t, "link": l, "data": d})
    except Exception:
        pass

    try:
        r_bio = requests.get("https://cfbm.gov.br/feed/", headers=headers, timeout=6)
        if r_bio.status_code == 200:
            root = ET.fromstring(r_bio.content)
            for item in root.findall(".//item")[:3]:
                t = item.find("title").text if item.find("title") is not None else "Atualização CFBM"
                l = item.find("link").text if item.find("link") is not None else "https://cfbm.gov.br"
                d = item.find("pubDate").text[:16] if item.find("pubDate") is not None else ""
                noticias_bio.append({"titulo": t, "link": l, "data": d})
    except Exception:
        pass

    return noticias_enf, noticias_bio

# --- BASE DE CONHECIMENTO CRÍTICA SOBRE HOSPITAIS / LABORATÓRIOS ---
INFO_EMPRESAS_SAUDE = {
    "porto dias": {
        "resumo": "Maior complexo hospitalar privado de Belém (Rede D'Or), no bairro do Marco. Referência em urgência e UTI.",
        "cultura": "Ritmo acelerado e exigente. Excelente vitrine profissional e remuneração rigorosamente em dia.",
        "pontos_atencao": "Plantão intenso, rotatividade moderada em enfermagem assistencial.",
        "dica_entrevista": "Foque em raciocínio rápido para drogas vasoativas, biossegurança e protocolos de segurança do paciente."
    },
    "ophir loyola": {
        "resumo": "Hospital público de referência oncológica do Pará (São Brás, Belém). Trata alta e média complexidade em câncer e neuro.",
        "cultura": "Ambiente público, com equipe multiprofissional muito unida e pacientes com longa permanência.",
        "pontos_atencao": "Alta carga emocional devido aos tratamentos oncológicos.",
        "dica_entrevista": "Demonstre humanização, empatia e conhecimento prático em manipulação estéril e curativos."
    },
    "metropolitano": {
        "resumo": "Hospital Metropolitano de Urgência e Emergência (HMUE), em Ananindeua. Referência em trauma, queimados e sala vermelha.",
        "cultura": "Pancada pura, aprendizado gigantesco e acelerado para qualquer profissional.",
        "pontos_atencao": "Deslocamento na BR-316 pode ser lento em horários de pico.",
        "dica_entrevista": "Destaque agilidade em triagem (Protocolo de Manchester) e estabilização de pacientes graves."
    },
    "santa casa": {
        "resumo": "Hospital secular tradicional em Belém (Umarizal), referência estadual em saúde materno-infantil, neonatal e ginecologia.",
        "cultura": "Muito acolhedora, com forte cultura assistencial humanizada e residência médica/multiprofissional.",
        "pontos_atencao": "Estrutura com grande volume de atendimentos pelo SUS.",
        "dica_entrevista": "Evidencie carinho, paciência e manejo pediátrico/neonatal seguro."
    },
    "einstein": {
        "resumo": "Hospital Israelita Albert Einstein (Morumbi, SP). O melhor hospital da América Latina.",
        "cultura": "Padrão de excelência internacional (JCI), tecnologia de ponta, remuneração e benefícios acima da média.",
        "pontos_atencao": "Processo seletivo altamente concorrido com múltiplas etapas e provas técnicas rigorosas.",
        "dica_entrevista": "Use termos como prática baseada em evidências, segurança do paciente e comunicação não violenta."
    },
    "sírio": {
        "resumo": "Hospital Sírio-Libanês (Bela Vista, SP). Centro de excelência médica de nível mundial em oncologia, cardiologia e cirurgia.",
        "cultura": "Cultura calorosa e humanizada com altíssimo rigor técnico. Plano de carreira muito estruturado.",
        "pontos_atencao": "Exige dedicação e pontualidade britânica.",
        "dica_entrevista": "Destaque foco em detalhe, ética profissional e prontuário eletrônico."
    },
    "dasa": {
        "resumo": "Maior rede integrada de saúde da América Latina (inclui marcas como Delboni, Lavoisier e Sérgio Franco).",
        "cultura": "Ambiente laboratorial dinâmico, metas analíticas claras e forte investimento em inovação.",
        "pontos_atencao": "Cobrança frequente por agilidade na liberação de laudos e tempo de atendimento.",
        "dica_entrevista": "Ressalte domínio de sistemas laboratoriais (LIS), calibração de equipamentos e controle de qualidade (CQI/CQE)."
    },
    "fleury": {
        "resumo": "Grupo Fleury Diagnósticos (São Paulo). Referência nacional em análises clínicas sofisticadas e biologia molecular.",
        "cultura": "Excelente clima organizacional, foco em acolhimento premium ao paciente e tecnologia de ponta.",
        "pontos_atencao": "Critério rigoroso na checagem de erros pré-analíticos.",
        "dica_entrevista": "Enfatize microscopia, precisão em pipetagem e interpretação minuciosa de dados analíticos."
    },
    "copa d'or": {
        "resumo": "Hospital Copa D'Or (Rede D'Or São Luiz, Copacabana, Rio de Janeiro). Hospital de referência privada no RJ.",
        "cultura": "Hospital moderno, alto fluxo de pacientes e forte presença institucional no Rio de Janeiro.",
        "pontos_atencao": "Carga horária rigorosa na escala de 12x36.",
        "dica_entrevista": "Evidencie monitorização hemodinâmica invasiva e protocolos de prevenção de lesão por pressão."
    }
}

# --- FUNÇÕES DE ANÁLISE DE CURRÍCULO E IA ---
def normalizar_texto(txt: str) -> str:
    if not txt:
        return ""
    nfkd = unicodedata.normalize("NFKD", txt)
    return "".join([c for c in nfkd if not unicodedata.combining(c)]).lower()

PALAVRAS_CHAVE = [
    "uti", "centro cirurgico", "urgencia", "emergencia", "pediatria",
    "neonatal", "hemodialise", "oncologia", "pronto socorro", "coren",
    "crbm", "tecnico de enfermagem", "enfermeiro", "enfermeira",
    "biomedico", "biomedica", "analises clinicas", "bancada", "coleta",
    "hematologia", "bioquimica", "microbiologia", "imunologia",
    "biologia molecular", "sorologia", "laudos", "auditoria", "farmacia",
    "puncao", "gasometria", "triagem", "manchester", "quimioterapia", "drogas vasoativas"
]

def extrair_termos_chave(texto: str) -> list:
    if not texto:
        return []
    texto_norm = normalizar_texto(texto)
    return [kw for kw in PALAVRAS_CHAVE if kw in texto_norm]

def calcular_match_real(vaga: Job, texto_curriculo: str, perfil_keywords: list) -> tuple:
    termos_base = set(perfil_keywords)
    if texto_curriculo:
        termos_base.update(extrair_termos_chave(texto_curriculo))
    
    if not termos_base:
        return 0, []
        
    texto_vaga = normalizar_texto(f"{vaga.title} {vaga.description} {vaga.specialty} {vaga.hospital_or_company}")
    acertos = [t for t in termos_base if t in texto_vaga]
    
    score = int((len(acertos) / max(len(termos_base), 1)) * 100)
    score_final = min(score * 2, 100)
    return max(score_final, 15 if acertos else 5), acertos

def buscar_raio_x_empresa(nome_empresa: str) -> dict:
    nome_norm = normalizar_texto(nome_empresa)
    for chave, dados in INFO_EMPRESAS_SAUDE.items():
        if chave in nome_norm:
            return dados
    return {
        "resumo": f"Instituição de saúde com atuação regional em {nome_empresa}.",
        "cultura": "Ambiente assistencial hospitalar/laboratorial com escalas regulares e protocolos da vigilância sanitária.",
        "pontos_atencao": "Verifique a escala exata e os benefícios diretos (VT/VA) antes de aceitar a proposta.",
        "dica_entrevista": "Demonstre pontualidade, domínio dos Procedimentos Operacionais Padrão (POPs) e dedicação integral."
    }

def gerar_analise_ia_completa(vaga: Job, curriculo_texto: str, perfil_kws: list) -> str:
    score, matches = calcular_match_real(vaga, curriculo_texto, perfil_kws)
    raio_x = buscar_raio_x_empresa(vaga.hospital_or_company)
    
    gemini_key = os.getenv("GEMINI_API_KEY", "")
    
    if HAS_GENAI and gemini_key:
        try:
            client = genai.Client(api_key=gemini_key)
            prompt = f"""
            Você é um consultor de carreira em saúde de elite e mentor carinhoso da candidata (em nome do Thiago Zuza).
            Seja cruelmente honesto, direto ao ponto e transparente na avaliação da oportunidade:
            
            VAGA: {vaga.title}
            INSTITUIÇÃO: {vaga.hospital_or_company} ({vaga.location})
            DESCRIÇÃO: {vaga.description}
            CURRÍCULO DA CANDIDATA: {curriculo_texto[:2500] if curriculo_texto else 'Graduação e vivência na área da saúde'}
            
            Gere uma análise estruturada contendo:
            1. Diagnóstico do Match Real (% e se realmente vale a pena se aplicar ou se é furada).
            2. Opinião honesta sobre a vaga e o hospital/empresa (ritmo de trabalho, cobrança e se agrega peso ao currículo).
            3. Raio-X da Empresa e Estrutura física.
            4. 3 Perguntas técnicas prováveis na entrevista para ela não ser pega de surpresa.
            Assine no final: 'Com todo amor e torcida, Thiago Zuza 💕 🐾'.
            """
            response = client.models.generate_content(
                model="gemini-2.5-flash",
                contents=prompt,
            )
            if response and response.text:
                return response.text
        except Exception:
            pass

    analise = f"🐾 **Análise Crítica de Carreira da Hello Kitty & Thiago Zuza** 💕\n\n"
    analise += f"🩺 **Vaga:** {vaga.title} | **Unidade:** {vaga.hospital_or_company}\n\n"
    
    analise += f"### 📊 1. Diagnóstico de Match Real: **{score}%**\n"
    if score >= 60:
        analise += f"✨ **Afinidade Muito Alta:** Seu perfil preenche os requisitos mais pesados dessa vaga. "
        if matches:
            analise += f"Suas competências em **{', '.join([m.upper() for m in matches])}** são exatamente o que o RH está procurando. Vale muito a pena se candidatar hoje mesmo!\n\n"
    elif score >= 35:
        analise += f"🌱 **Afinidade Moderada:** Você tem boa base para concorrer, mas eles podem cobrar mais vivência prática no setor. Foque em demonstrar facilidade rápida de aprendizado e atenção rigorosa a POPs.\n\n"
    else:
        analise += f"⚠️ **Alerta Sincero:** O perfil da vaga exige requisitos que ainda não estão explícitos no seu currículo. Se for se candidatar, ajuste seu resumo para destacar vivências de estágio e procedimentos correlatos.\n\n"

    analise += f"### 💡 2. Opinião Sincera sobre a Oportunidade\n"
    analise += f"• **Vale a pena?** Sim, especialmente pelo peso no currículo. O turno **{vaga.shift_type}** exige preparo físico, mas abre portas imediatas para setores mais valorizados.\n"
    analise += f"• **Rotina provável:** {raio_x['pontos_atencao']}\n\n"

    analise += f"### 🏢 3. Raio-X da Instituição ({vaga.hospital_or_company})\n"
    analise += f"• **Perfil:** {raio_x['resumo']}\n"
    analise += f"• **Cultura interna:** {raio_x['cultura']}\n"
    analise += f"• **Como se destacar na entrevista:** {raio_x['dica_entrevista']}\n\n"

    analise += f"💌 *'Você é uma profissional brilhante, competente e dedicada. Tenho orgulho infinito de você!'* — Com todo o meu amor, Thiago Zuza 💕 🐾"
    return analise

# --- BARRA LATERAL (FILTROS + ALERTA DE E-MAIL) ---
st.sidebar.markdown("### 🎀 Localização & Carreira")

LISTA_ESTADOS = [
    "Todos os Estados",
    "PA - Pará (Belém e Região)",
    "SP - São Paulo",
    "RJ - Rio de Janeiro",
    "AC - Acre", "AL - Alagoas", "AM - Amazonas", "AP - Amapá",
    "BA - Bahia", "CE - Ceará", "DF - Distrito Federal", "ES - Espírito Santo",
    "GO - Goiás", "MA - Maranhão", "MG - Minas Gerais", "MS - Mato Grosso do Sul",
    "MT - Mato Grosso", "PB - Paraíba", "PE - Pernambuco", "PI - Piauí",
    "PR - Paraná", "RN - Rio Grande do Norte", "RO - Rondônia", "RR - Roraima",
    "RS - Rio Grande do Sul", "SC - Santa Catarina", "SE - Sergipe", "TO - Tocantins"
]

filtro_estado = st.sidebar.selectbox("📍 Filtrar por Estado / Região:", LISTA_ESTADOS)

filtro_categoria = st.sidebar.radio(
    "Área de Atuação:",
    ["Todas", "Enfermagem", "Biomedicina", "Saúde Geral"]
)
busca_termo = st.sidebar.text_input("🔍 Busca por palavra", placeholder="Ex: Sírio, Copa D'Or, Porto Dias, UTI, Coleta...")

if st.sidebar.button("🔄 Sincronizar Portais 24h Agora"):
    with st.spinner("Atualizando feed dos portais e conectando banco..."):
        adicionadas_web = 0
        try:
            scraper = ScraperHospitaisBelem()
            novas = scraper.coletar_todas()
            with Session(engine) as session:
                for v in novas:
                    if not session.exec(select(Job).where(Job.url_apply == v["url_apply"])).first():
                        session.add(Job(**v))
                        adicionadas_web += 1
                session.commit()
        except Exception:
            pass
        
        adicionadas_base = popular_catalogo_base()
        total_novas = adicionadas_web + adicionadas_base
        st.sidebar.success(f"Sincronização concluída! {total_novas} novas vagas inseridas.")
        st.rerun()

st.sidebar.markdown("---")
st.sidebar.markdown("### 💌 Alertas Automáticos por E-mail")
with st.sidebar.form("form_inscricao_alertas"):
    nome_input = st.text_input("Nome:", placeholder="Ex: Meu Amor / Candidata")
    email_input = st.text_input("E-mail para Receber Alertas:", placeholder="exemplo@gmail.com")
    ativo_check = st.checkbox("Receber alertas a cada 4 horas", value=True)
    salvar_inscricao = st.form_submit_button("🔔 Salvar Preferência de Alerta")

    if salvar_inscricao:
        email_limpo = email_input.strip().lower()
        padrao_email = r"^[\w\.-]+@[\w\.-]+\.\w+$"

        if not email_limpo or not re.match(padrao_email, email_limpo):
            st.sidebar.error("⚠️ Insira um formato de e-mail válido (ex: nome@dominio.com).")
        else:
            with Session(engine) as session:
                sub = session.exec(
                    select(UserSubscription).where(UserSubscription.email == email_limpo)
                ).first()
                if not sub:
                    sub = UserSubscription(
                        name=nome_input.strip() or "Candidato(a)",
                        email=email_limpo,
                        active=ativo_check
                    )
                    session.add(sub)
                else:
                    if nome_input.strip():
                        sub.name = nome_input.strip()
                    sub.active = ativo_check
                    session.add(sub)
                session.commit()
            st.sidebar.success("✅ Alerta cadastrado! O robô enviará as novidades.")

st.sidebar.markdown("---")
st.sidebar.markdown("##### 🥠 Biscoito do Dia")
st.sidebar.info("A dedicação que você coloca em cuidar das pessoas faz a diferença em qualquer equipe hospitalar ou laboratorial! 💕")

# --- CONSULTA DAS VAGAS NO BANCO ---
with Session(engine) as session:
    q = select(Job)
    
    if filtro_estado != "Todos os Estados":
        uf_codigo = filtro_estado[:2]
        q = q.where(Job.state == uf_codigo)
        
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

    if not vagas_lista and filtro_estado == "Todos os Estados" and filtro_categoria == "Todas" and not busca_termo:
        popular_catalogo_base()
        vagas_lista = session.exec(select(Job).order_by(Job.created_at.desc())).all()

# Perfil do usuário e Currículo salvo
with Session(engine) as session:
    perfil_user = session.exec(select(UserProfile)).first()
    user_kws = [k.strip() for k in perfil_user.skills_keywords.split(",") if k.strip()] if perfil_user and perfil_user.skills_keywords else []
    is_biomed = perfil_user.is_biomed_graduated if perfil_user else False
    curriculo_armazenado = getattr(perfil_user, "resume_raw_text", "") or ""

# --- ABAS PRINCIPAIS ---
tab_vagas, tab_biomed, tab_agenda, tab_necessidades, tab_ia_curriculo, tab_linkedin, tab_rotas_emerg, tab_candidaturas = st.tabs([
    "🌸 Mural Geral de Vagas",
    "🔬 Especial Biomedicina",
    "📅 Agenda Médica & Redes",
    "💊 Necessidades & Custos Mensais",
    "🤖 Central IA: Análise de Currículo",
    "💼 Perfil Campeão LinkedIn",
    "🗺️ Trajeto, Uber & Notícias 24h",
    "📋 Minhas Candidaturas"
])

# ================= TAB 1: MURAL DE VAGAS =================
with tab_vagas:
    st.markdown(f"<h3 style='color: #AD1457 !important;'>🩺 Oportunidades no Feed 24h: <b>{len(vagas_lista)}</b></h3>", unsafe_allow_html=True)
    
    if not vagas_lista:
        st.info("Nenhuma oportunidade localizada para estes filtros. Tente selecionar 'Todos os Estados' na barra lateral!")
    else:
        for v in vagas_lista:
            score, _ = calcular_match_real(v, curriculo_armazenado, user_kws)
            
            uf = getattr(v, "state", "PA")
            if uf == "PA":
                badge_estado = '<span class="badge">🌴 Belém - PA</span>'
            elif uf == "SP":
                badge_estado = '<span class="badge-sp">🏙️ São Paulo - SP</span>'
            elif uf == "RJ":
                badge_estado = '<span class="badge-rj">🌊 Rio de Janeiro - RJ</span>'
            else:
                badge_estado = f'<span class="badge">📍 {uf}</span>'

            badge_cat = '<span class="badge-bio">🔬 Biomedicina</span>' if getattr(v, "category", "Enfermagem") == "Biomedicina" else '<span class="badge">🩺 Enfermagem</span>'
            
            link_vaga = v.url_apply if v.url_apply.startswith("http") else f"https://{v.url_apply}"
            rota_maps = f"https://www.google.com/maps/dir/?api=1&destination={urllib.parse.quote(f'{v.hospital_or_company} {v.location}')}&travelmode=transit"
            txt_zap = urllib.parse.quote(f"Olha essa oportunidade de {v.title} no {v.hospital_or_company} ({v.location}): {link_vaga}")
            link_zap = f"https://api.whatsapp.com/send?text={txt_zap}"

            st.markdown(f"""
            <div class="job-card">
                <div class="job-title">💖 {v.title}</div>
                <div style="color: #880E4F !important; font-size: 0.95rem; margin-bottom: 8px;">
                    🏥 <b>{v.hospital_or_company}</b> &nbsp;•&nbsp; 📍 {v.location}
                </div>
                <div style="margin-bottom: 10px;">
                    {badge_estado} {badge_cat} 
                    <span class="badge-24h">🌐 {v.source}</span>
                    <span class="badge">⏰ {v.shift_type}</span>
                    <span class="badge">✨ Match Real: {score}%</span>
                </div>
                <p style="color: #333333 !important; font-size: 0.92rem; line-height: 1.4;">{v.description}</p>
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
                    with st.spinner("Analisando requisitos e consultando hospital..."):
                        st.markdown(gerar_analise_ia_completa(v, curriculo_armazenado, user_kws))
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
    st.markdown("<h2 style='color: #AD1457 !important;'>🔬 Painel Exclusivo de Biomedicina</h2>", unsafe_allow_html=True)
    st.info("Espaço dedicado a Análises Clínicas, Biologia Molecular, Imunologia e Diagnósticos Laboratoriais.")

    st.markdown("<h4 style='color: #880E4F !important;'>🎓 Verificação Profissional</h4>", unsafe_allow_html=True)
    
    with st.form("form_verificacao_biomed"):
        tem_formacao = st.radio(
            "Em Biomedicina, é necessária a sua formação completa, você já concluiu a graduação?",
            ["Sim, possuo graduação completa e registro no CRBM", "Não, estou cursando / formação em andamento"],
            index=0 if is_biomed else 1
        )
        btn_salvar_status = st.form_submit_button("Confirmar Status de Formação")

        if btn_salvar_status:
            with Session(engine) as s:
                p = s.exec(select(UserProfile)).first()
                grad_status = "Sim" in tem_formacao
                if not p:
                    p = UserProfile(
                        full_name="Usuária",
                        is_biomed_graduated=grad_status,
                        skills_keywords="uti,coleta,analises clinicas,bancada,biomedicina"
                    )
                else:
                    p.is_biomed_graduated = grad_status
                s.add(p)
                s.commit()
            st.success("Status de formação atualizado com sucesso!")
            st.rerun()

    if "Não" in tem_formacao:
        st.warning("⚠️ Atenção: Vagas para Biomédica Responsável Técnica, emissão e assinatura de laudos exigem diploma e registro ativo no CRBM. Enquanto não concluir, priorize oportunidades como Técnica de Laboratório, Auxiliar de Coleta ou Estágio em Análises Clínicas!")
    else:
        st.success("✨ Elegível: Você está apta a assumir bancadas analíticas, liberação de laudos e responsabilidade técnica laboratorial.")

    st.divider()

    col_mod1, col_mod2 = st.columns(2)
    
    texto_curriculo = """OBJETIVO:
Biomédica - Análises Clínicas / Diagnóstico Laboratorial

RESUMO DE QUALIFICAÇÕES:
• Experiência e proficiência em rotinas de bancada (Hematologia, Bioquímica, Imunologia e Microbiologia).
• Conhecimento na calibração e operação de analisadores automatizados e controle de qualidade (CQI/CQE).
• Registro ativo e regular junto ao CRBM.
• Atenção rigorosa aos procedimentos operacionais padrão (POPs) e biossegurança.

FORMAÇÃO:
• Bacharelado em Biomedicina."""

    texto_carta = """Prezada Coordenação de Laboratório e RH,

Apresento minha candidatura à oportunidade na área de Análises Clínicas.

Possuo sólido domínio dos fluxos laboratoriais pré-analíticos, analíticos e pós-analíticos, atuando com precisão em exames hematológicos, imunológicos e bioquímicos. Prezo pelo rigor metodológico e controle de qualidade para garantir a total confiabilidade dos laudos diagnósticos.

Estou à inteira disposição para entrevista técnica e demonstração de competências de bancada.

Atenciosamente,
Biomédica | Contato WhatsApp"""

    with col_mod1:
        st.markdown("<h4 style='color: #880E4F !important;'>📄 Currículo Sugestivo (Biomedicina)</h4>", unsafe_allow_html=True)
        st.markdown(f'<div class="doc-display-box">{texto_curriculo}</div>', unsafe_allow_html=True)
        st.download_button(
            label="📥 Baixar Modelo de Currículo (.txt)",
            data=texto_curriculo,
            file_name="Curriculo_Biomedicina_Modelo.txt",
            mime="text/plain",
            use_container_width=True
        )

    with col_mod2:
        st.markdown("<h4 style='color: #880E4F !important;'>✉️ Carta de Apresentação (Biomedicina)</h4>", unsafe_allow_html=True)
        st.markdown(f'<div class="doc-display-box">{texto_carta}</div>', unsafe_allow_html=True)
        st.download_button(
            label="📥 Baixar Carta de Apresentação (.txt)",
            data=texto_carta,
            file_name="Carta_Apresentacao_Biomedicina.txt",
            mime="text/plain",
            use_container_width=True
        )

# ================= TAB 3: AGENDA MÉDICA, VALORES & REDES CREDENCIADAS =================
with tab_agenda:
    st.markdown("<h2 style='color: #AD1457 !important;'>📅 Agenda Médica, Valores & Redes Credenciadas</h2>", unsafe_allow_html=True)
    st.markdown("Agendamento completo com valores estimados, redes de atendimento e contatos diretos para marcação de exames. 💕")

    hoje_str = date.today().strftime("%Y-%m-%d")
    hoje_formatada = date.today().strftime("%d/%m/%Y")

    with Session(engine) as session:
        compromissos_hoje = session.exec(
            select(MedicalAppointment).where(MedicalAppointment.scheduled_date == hoje_str).order_by(MedicalAppointment.scheduled_time)
        ).all()
        todos_compromissos = session.exec(
            select(MedicalAppointment).order_by(MedicalAppointment.scheduled_date.desc(), MedicalAppointment.scheduled_time)
        ).all()

    st.markdown(f"### 🔔 Compromissos & Exames de Hoje ({hoje_formatada})")

    if compromissos_hoje:
        itens_zap = []
        for comp in compromissos_hoje:
            status_icone = "✅ [Realizado]" if comp.is_completed else "⏰ [Pendente]"
            preco_txt = f" | R$ {comp.estimated_price:.2f}" if comp.estimated_price > 0 else ""
            st.markdown(f"""
            <div class="appointment-card" style="border-left: 6px solid {'#4CAF50' if comp.is_completed else '#FF6584'};">
                <div style="display:flex; justify-content:space-between; align-items:center;">
                    <h4 style="margin:0; color:#C2185B;">{comp.appointment_type}: {comp.title}</h4>
                    <span style="font-weight:700; color:{'#2E7D32' if comp.is_completed else '#C2185B'};">{status_icone} às {comp.scheduled_time}</span>
                </div>
                <p style="margin:6px 0; color:#333;">📍 <b>Local / Rede:</b> {comp.location} ({comp.network_provider}){preco_txt}</p>
                {f'<p style="margin:4px 0; color:#666; font-size:0.9rem;">📝 <i>Preparo / Obs: {comp.notes}</i></p>' if comp.notes else ''}
            </div>
            """, unsafe_allow_html=True)

            itens_zap.append(f"• {comp.scheduled_time} - {comp.title} em {comp.location} ({comp.network_provider}) [{'Realizado' if comp.is_completed else 'Pendente'}]")

            col_chk, _ = st.columns([2, 5])
            with col_chk:
                if not comp.is_completed:
                    if st.button("Marcar como Feito ✅", key=f"btn_chk_{comp.id}"):
                        with Session(engine) as s:
                            item = s.get(MedicalAppointment, comp.id)
                            item.is_completed = True
                            s.add(item)
                            s.commit()
                        st.rerun()

        txt_notif_agenda = f"Oi, amor! Meus exames/compromissos de hoje ({hoje_formatada}) são:\n\n" + "\n".join(itens_zap) + "\n\nTe amo! 💕"
        link_zap_agenda = f"https://api.whatsapp.com/send?phone=5511913129697&text={urllib.parse.quote(txt_notif_agenda)}"
        st.markdown(f"""
        <div style="margin-top:10px;">
            <a href="{link_zap_agenda}" target="_blank" class="btn-safety-alert" style="padding:10px 22px;">
                📲 Enviar Resumo da Minha Agenda de Hoje p/ Thiago
            </a>
        </div>
        """, unsafe_allow_html=True)
    else:
        st.success(f"✨ Nenhum exame ou compromisso médico agendado para hoje ({hoje_formatada})! Dia livre para descanso ou estudos. 💕")

    st.markdown("---")

    # --- FORMULÁRIO DE NOVO AGENDAMENTO COM VALOR E REDES CREDENCIADAS ---
    col_cad1, col_cad2 = st.columns([1, 1])
    with col_cad1:
        st.markdown("#### ➕ Agendar Novo Exame ou Consulta")
        with st.form("form_novo_compromisso"):
            novo_tipo = st.selectbox(
                "Tipo de Registro:",
                ["Exame a Realizar", "Exame Feito / Resultado", "Consulta Médica", "Retorno / Procedimento", "Compromisso Geral"]
            )
            novo_titulo = st.text_input("Nome do Exame ou Consulta:", placeholder="Ex: Hemograma Completo, Ultrassom, Consulta Gineco...")
            
            col_loc, col_rede = st.columns(2)
            with col_loc:
                novo_local = st.text_input("Unidade / Hospital:", placeholder="Ex: Unidade Nazaré, Delboni...")
            with col_rede:
                nova_rede = st.selectbox(
                    "Rede / Convênio:",
                    ["Lavoisier / Dasa", "Grupo Fleury", "Hospital Porto Dias", "Santa Casa", "Ophir Loyola", "SUS / UBS", "Particular", "Outro"]
                )

            col_val_ex, col_dt, col_hr = st.columns(3)
            with col_val_ex:
                novo_valor = st.number_input("Valor Estimado (R$):", min_value=0.0, value=0.0, step=10.0, format="%.2f")
            with col_dt:
                nova_data = st.date_input("Data:", value=date.today())
            with col_hr:
                nova_hora = st.time_input("Horário:", value=datetime.now().time())

            novas_obs = st.text_input("Instruções / Preparo:", placeholder="Ex: Jejum de 8h, levar pedido médico, retirar na recepção...")
            
            btn_salvar_comp = st.form_submit_button("💾 Salvar na Agenda Médica")

            if btn_salvar_comp:
                if not novo_titulo.strip():
                    st.error("Por favor, preencha o nome do exame ou consulta.")
                else:
                    with Session(engine) as s:
                        novo_item = MedicalAppointment(
                            title=novo_titulo.strip(),
                            appointment_type=novo_tipo,
                            location=novo_local.strip() or "A definir",
                            network_provider=nova_rede,
                            estimated_price=novo_valor,
                            scheduled_date=nova_data.strftime("%Y-%m-%d"),
                            scheduled_time=nova_hora.strftime("%H:%M"),
                            notes=novas_obs.strip(),
                            is_completed=("Exame Feito" in novo_tipo)
                        )
                        s.add(novo_item)
                        s.commit()
                    st.success("Compromisso salvo na agenda com sucesso!")
                    st.rerun()

    with col_cad2:
        st.markdown("#### 📋 Todos os Agendamentos & Histórico:")
        if todos_compromissos:
            for item in todos_compromissos:
                dataFormat = datetime.strptime(item.scheduled_date, "%Y-%m-%d").strftime("%d/%m/%Y")
                cor_borda = "#4CAF50" if item.is_completed else ("#FF9800" if item.scheduled_date == hoje_str else "#FFB6C1")
                val_badge = f" | <b>R$ {item.estimated_price:.2f}</b>" if item.estimated_price > 0 else ""
                st.markdown(f"""
                <div class="appointment-card" style="border-left: 5px solid {cor_borda}; padding:10px 14px;">
                    <b style="color:#C2185B;">{item.appointment_type}: {item.title}</b><br>
                    <span style="font-size:0.85rem; color:#444;">📅 {dataFormat} às {item.scheduled_time} &nbsp;|&nbsp; 📍 {item.location} ({item.network_provider}){val_badge}</span><br>
                    <span style="font-size:0.8rem; font-weight:700; color:{'#2E7D32' if item.is_completed else '#C2185B'};">Status: {'Concluído / Feito' if item.is_completed else 'A realizar'}</span>
                </div>
                """, unsafe_allow_html=True)
                
                col_btn_rm, _ = st.columns([1, 4])
                with col_btn_rm:
                    if st.button("Remover", key=f"rm_comp_{item.id}"):
                        with Session(engine) as s:
                            alvo = s.get(MedicalAppointment, item.id)
                            s.delete(alvo)
                            s.commit()
                        st.rerun()
        else:
            st.info("Nenhum registro encontrado na agenda médica.")

    st.markdown("---")

    # --- GUIA DE REDES CREDENCIADAS & CONTATOS DIRETOS ---
    st.markdown("### 🏥 Redes Credenciadas & Contatos Diretos de Agendamento")
    st.markdown("Canais diretos para marcação rápida de exames laboratoriais, consultas e orçamentos:")

    col_net1, col_net2, col_net3 = st.columns(3)
    with col_net1:
        st.markdown("""
        <div class="network-card">
            <h4 style="color:#C2185B !important; margin:0 0 6px 0;">🔬 Dasa / Lavoisier / Sérgio Franco</h4>
            <p style="font-size:0.88rem; color:#333; margin:0 0 10px 0;">
                Atendimento laboratorial, análises clínicas completas e exames por imagem.
            </p>
            <a href="https://api.whatsapp.com/send?phone=551130474488&text=Olá,%20gostaria%20de%20agendar%20um%20exame" target="_blank" class="btn-contact-direct">
                💬 Agendar via WhatsApp
            </a>
        </div>
        """, unsafe_allow_html=True)

    with col_net2:
        st.markdown("""
        <div class="network-card">
            <h4 style="color:#00695C !important; margin:0 0 6px 0;">🔬 Grupo Fleury Diagnósticos</h4>
            <p style="font-size:0.88rem; color:#333; margin:0 0 10px 0;">
                Referência em biologia molecular, genética e exames laboratoriais de alta precisão.
            </p>
            <a href="https://api.whatsapp.com/send?phone=551131790822&text=Olá,%20gostaria%20de%20informações%20sobre%20exames" target="_blank" class="btn-contact-direct">
                💬 Agendar via WhatsApp
            </a>
        </div>
        """, unsafe_allow_html=True)

    with col_net3:
        st.markdown("""
        <div class="network-card">
            <h4 style="color:#880E4F !important; margin:0 0 6px 0;">🏥 Hospital Porto Dias (Belém)</h4>
            <p style="font-size:0.88rem; color:#333; margin:0 0 10px 0;">
                Centro de diagnóstico hospitalar, ressonância, tomografia e exames cardiológicos.
            </p>
            <a href="https://api.whatsapp.com/send?phone=559130843000&text=Olá,%20gostaria%20de%20agendar%20exame%20no%20Porto%20Dias" target="_blank" class="btn-contact-direct">
                💬 Central de Exames PA
            </a>
        </div>
        """, unsafe_allow_html=True)

# ================= TAB 4: NECESSIDADES & CUSTOS MENSAIS =================
with tab_necessidades:
    st.markdown("<h2 style='color: #AD1457 !important;'>💊 Necessidades & Custos Mensais Tabelados</h2>", unsafe_allow_html=True)
    st.markdown("Controle prático em tabela estilo Excel de remédios, vitaminas e gastos essenciais com histórico mensal e notificação direta. 💕")

    st.markdown("""
    <div class="reminder-hk-card">
        <img src="https://upload.wikimedia.org/wikipedia/en/0/05/Hello_kitty_character_portrait.png" style="width:70px; height:auto; border-radius:10px;">
        <div>
            <h4 style="color:#C2185B !important; margin:0 0 4px 0;">🎀 Lembrete da Hello Kitty:</h4>
            <p style="color:#33101E; font-size:0.95rem; margin:0; line-height:1.4;">
                <i>"Meu bem, não se esqueça de checar seus remédios, vitaminas e coisinhas essenciais do mês. Já organizou tudo por aqui ou é só isso mesmo? Qualquer coisa avise o Thiago!"</i> 💕 ✨
            </p>
        </div>
    </div>
    """, unsafe_allow_html=True)

    mes_atual_padrao = date.today().strftime("%Y-%m")

    with Session(engine) as session:
        meses_disponiveis = session.exec(select(MonthlyNeed.month_reference).distinct()).all()
        if not meses_disponiveis or mes_atual_padrao not in meses_disponiveis:
            lista_meses = sorted(list(set(list(meses_disponiveis) + [mes_atual_padrao])), reverse=True)
        else:
            lista_meses = sorted(list(set(meses_disponiveis)), reverse=True)

        col_filtro_m, _ = st.columns([2, 3])
        with col_filtro_m:
            mes_selecionado = st.selectbox("📅 Selecione o Mês de Referência:", lista_meses, index=0)

        itens_mes = session.exec(
            select(MonthlyNeed).where(MonthlyNeed.month_reference == mes_selecionado).order_by(MonthlyNeed.category, MonthlyNeed.item_name)
        ).all()

    total_geral = sum(item.estimated_cost * item.quantity for item in itens_mes)
    total_pendente = sum(item.estimated_cost * item.quantity for item in itens_mes if not item.is_purchased)
    total_comprado = sum(item.estimated_cost * item.quantity for item in itens_mes if item.is_purchased)

    col_m1, col_m2, col_m3 = st.columns(3)
    with col_m1:
        st.markdown(f"""
        <div style="background:#FFFFFF; border:2px solid #FFCCD7; border-radius:14px; padding:14px; text-align:center;">
            <span style="font-size:0.85rem; color:#880E4F; font-weight:700;">Gasto Mensal Estimado:</span>
            <h3 style="color:#C2185B; margin:4px 0 0 0;">R$ {total_geral:.2f}</h3>
        </div>
        """, unsafe_allow_html=True)
    with col_m2:
        st.markdown(f"""
        <div style="background:#FFFFFF; border:2px solid #FFCCD7; border-radius:14px; padding:14px; text-align:center;">
            <span style="font-size:0.85rem; color:#880E4F; font-weight:700;">Itens Já Comprados / OK:</span>
            <h3 style="color:#2E7D32; margin:4px 0 0 0;">R$ {total_comprado:.2f}</h3>
        </div>
        """, unsafe_allow_html=True)
    with col_m3:
        st.markdown(f"""
        <div style="background:#FFFFFF; border:2px solid #FFCCD7; border-radius:14px; padding:14px; text-align:center;">
            <span style="font-size:0.85rem; color:#880E4F; font-weight:700;">Pendente / Falta Comprar:</span>
            <h3 style="color:#E65100; margin:4px 0 0 0;">R$ {total_pendente:.2f}</h3>
        </div>
        """, unsafe_allow_html=True)

    st.markdown("---")

    if itens_mes:
        st.markdown("### 📊 Tabela de Custos & Medicamentos (Estilo Planilha)")
        dados_tabela = []
        for i in itens_mes:
            subtotal = i.estimated_cost * i.quantity
            dados_tabela.append({
                "Item / Remédio": i.item_name,
                "Categoria": i.category,
                "Quantidade": i.quantity,
                "Valor Unit. (R$)": f"R$ {i.estimated_cost:.2f}",
                "Subtotal (R$)": f"R$ {subtotal:.2f}",
                "Status": "✅ Comprado" if i.is_purchased else "⏳ Pendente"
            })
        
        df_display = pd.DataFrame(dados_tabela)
        st.dataframe(df_display, use_container_width=True, hide_index=True)

        resumo_itens_txt = []
        for i in itens_mes:
            subtotal = i.estimated_cost * i.quantity
            status_txt = "OK" if i.is_purchased else "Falta comprar"
            resumo_itens_txt.append(f"• {i.item_name} ({i.category}): {i.quantity}x de R${i.estimated_cost:.2f} = R${subtotal:.2f} [{status_txt}]")

        msg_necessidades = (
            f"Oi, amor! Segue meu resumo de necessidades e remédios de {mes_selecionado}:\n\n"
            + "\n".join(resumo_itens_txt)
            + f"\n\n💰 Total Estimado: R$ {total_geral:.2f} (Pendente: R$ {total_pendente:.2f})\n"
            f"É só isso por enquanto! Te amo! 💕"
        )
        link_zap_nec = f"https://api.whatsapp.com/send?phone=5511913129697&text={urllib.parse.quote(msg_necessidades)}"

        st.markdown(f"""
        <div style="margin: 16px 0;">
            <a href="{link_zap_nec}" target="_blank" class="btn-safety-alert" style="padding:10px 24px;">
                📲 Enviar Lista de Necessidades/Remédios p/ Thiago
            </a>
        </div>
        """, unsafe_allow_html=True)
    else:
        st.info(f"Nenhum item cadastrado para o mês de {mes_selecionado}. Cadastre no formulário abaixo!")

    st.markdown("---")

    col_nec1, col_nec2 = st.columns([1, 1])
    with col_nec1:
        st.markdown("#### ➕ Adicionar Item / Remédio / Custo")
        with st.form("form_nova_necessidade"):
            novo_item_nome = st.text_input("Nome do Item / Medicamento:", placeholder="Ex: Antialérgico, Vitamina D, Remédio de uso contínuo...")
            nova_cat = st.selectbox(
                "Categoria:",
                ["Medicamento Contínuo", "Suplemento / Vitamina", "Mercado & Essenciais", "Cuidados Pessoais", "Outros"]
            )
            col_qtd, col_val = st.columns(2)
            with col_qtd:
                nova_qtd = st.number_input("Quantidade:", min_value=1, value=1, step=1)
            with col_val:
                novo_preco = st.number_input("Valor Estimado Unitário (R$):", min_value=0.0, value=0.0, step=1.0, format="%.2f")
            
            mes_item_input = st.text_input("Mês de Referência (YYYY-MM):", value=mes_selecionado)
            comprado_check = st.checkbox("Item já comprado?", value=False)

            btn_salvar_nec = st.form_submit_button("💾 Salvar na Planilha")

            if btn_salvar_nec:
                if not novo_item_nome.strip():
                    st.error("Informe o nome do item ou medicamento.")
                else:
                    with Session(engine) as s:
                        item_db = MonthlyNeed(
                            item_name=novo_item_nome.strip(),
                            category=nova_cat,
                            quantity=nova_qtd,
                            estimated_cost=novo_preco,
                            month_reference=mes_item_input.strip() or mes_selecionado,
                            is_purchased=comprado_check
                        )
                        s.add(item_db)
                        s.commit()
                    st.success("Item adicionado com sucesso à tabela de necessidades!")
                    st.rerun()

    with col_nec2:
        st.markdown("#### ✏️ Alterar Status ou Remover Itens:")
        if itens_mes:
            for item in itens_mes:
                sub = item.estimated_cost * item.quantity
                col_info, col_act = st.columns([3, 2])
                with col_info:
                    st.markdown(f"**{item.item_name}** ({item.quantity}x) — R$ {sub:.2f}")
                with col_act:
                    txt_btn_status = "Marcar Comprado" if not item.is_purchased else "Desmarcar"
                    if st.button(txt_btn_status, key=f"tgl_st_{item.id}"):
                        with Session(engine) as s:
                            obj = s.get(MonthlyNeed, item.id)
                            obj.is_purchased = not obj.is_purchased
                            s.add(obj)
                            s.commit()
                        st.rerun()
                    if st.button("Excluir", key=f"del_nec_{item.id}"):
                        with Session(engine) as s:
                            obj = s.get(MonthlyNeed, item.id)
                            s.delete(obj)
                            s.commit()
                        st.rerun()
                st.divider()
        else:
            st.info("Nenhum item para gerenciar neste mês.")

# ================= TAB 5: ANÁLISE IA DO CURRÍCULO =================
with tab_ia_curriculo:
    st.markdown("<h2 style='color: #AD1457 !important;'>🤖 Central IA: Análise de Currículo & Raio-X de Empresas</h2>", unsafe_allow_html=True)
    st.markdown("Suba o currículo dela para que a IA analise a compatibilidade real em cada vaga, dê opiniões diretas sobre os hospitais e prepare para as entrevistas! 💕")

    col_up1, col_up2 = st.columns([1, 1])
    with col_up1:
        st.markdown("#### 📤 1. Carregar Currículo Dela (PDF ou Texto)")
        uploaded_pdf = st.file_uploader("Envie o currículo em PDF:", type=["pdf"])
        if uploaded_pdf is not None:
            try:
                reader = PdfReader(uploaded_pdf)
                texto_extraido = "\n".join([page.extract_text() for page in reader.pages if page.extract_text()])
                if texto_extraido:
                    with Session(engine) as s:
                        p = s.exec(select(UserProfile)).first()
                        if not p:
                            p = UserProfile(full_name="Usuária", resume_raw_text=texto_extraido)
                        else:
                            p.resume_raw_text = texto_extraido
                        s.add(p)
                        s.commit()
                    st.success("✅ Currículo em PDF lido e salvo com sucesso na memória da IA!")
                    curriculo_armazenado = texto_extraido
            except Exception as e:
                st.error(f"Erro ao ler PDF: {e}")

    with col_up2:
        st.markdown("#### 🔍 2. Consultar Raio-X de Qualquer Empresa")
        empresa_busca = st.text_input("Pesquisar Hospital ou Laboratório:", placeholder="Ex: Hospital Porto Dias, Sírio-Libanês, Dasa, Fleury...")
        if empresa_busca:
            dados_emp = buscar_raio_x_empresa(empresa_busca)
            st.markdown(f"""
            <div class="news-card">
                <h4 style="color:#C2185B !important; margin:0 0 6px 0;">🏥 Raio-X: {empresa_busca.title()}</h4>
                <p style="color:#222222; font-size:0.92rem; line-height:1.5;"><b>Resumo:</b> {dados_emp['resumo']}</p>
                <p style="color:#222222; font-size:0.92rem; line-height:1.5;"><b>Ambiente e Cultura:</b> {dados_emp['cultura']}</p>
                <p style="color:#B71C1C; font-size:0.92rem; line-height:1.5;"><b>Ponto de Atenção:</b> {dados_emp['pontos_atencao']}</p>
                <p style="color:#00695C; font-size:0.92rem; line-height:1.5;"><b>Dica de Ouro p/ Entrevista:</b> {dados_emp['dica_entrevista']}</p>
            </div>
            """, unsafe_allow_html=True)

    st.markdown("---")
    st.markdown("#### 📑 Texto do Currículo Atual na Memória:")
    if curriculo_armazenado:
        st.markdown(f'<div class="doc-display-box">{curriculo_armazenado[:2000]}...</div>', unsafe_allow_html=True)
    else:
        st.info("Nenhum currículo em PDF carregado ainda. Você pode enviar acima ou colar um texto diretamente na aba de Biomedicina!")

# ================= TAB 6: PERFIL CAMPEÃO LINKEDIN =================
with tab_linkedin:
    st.markdown("<h2 style='color: #0077B5 !important;'>💼 Seu Perfil Campeão no LinkedIn</h2>", unsafe_allow_html=True)
    st.markdown("""
    Recrutadores dos melhores hospitais e redes de diagnóstico (Dasa, Fleury, Albert Einstein, Rede D'Or) procuram profissionais diariamente no LinkedIn. 
    Aqui estão modelos prontos e otimizados com as palavras-chave que eles mais pesquisam! 💕
    """)

    st.markdown("""
    <div class="linkedin-card">
        <h4 style="color:#0077B5 !important; margin:0 0 10px 0;">🌐 Acesso Rápido ao LinkedIn</h4>
        <p style="color:#333333 !important; font-size:0.95rem; margin-bottom:14px;">
            Clique no botão abaixo para criar sua conta ou acessar seu perfil direto no LinkedIn sem complicação:
        </p>
        <a href="https://www.linkedin.com/signup" target="_blank" class="btn-linkedin-direct">
            🚀 Abrir / Criar Perfil no LinkedIn
        </a>
    </div>
    """, unsafe_allow_html=True)

    st.markdown("### 📝 Textos Prontos para Copiar ou Baixar")

    col_lk1, col_lk2 = st.columns(2)

    texto_linkedin_enfermagem = """=== TÍTULO PROFISSIONAL (Headline do LinkedIn) ===
Enfermeira | Cuidado Assistencial Humanizado | Urgência & Emergência | Terapia Intensiva (UTI) | COREN Ativo

=== SOBRE MIM (Resumo Profissional) ===
Profissional de Enfermagem com dedicação integral à assistência humanizada, segurança do paciente e rigor na aplicação de protocolos clínicos. 

Minhas principais competências e áreas de atuação incluem:
• Atendimento assistencial direto a pacientes em diferentes níveis de complexidade.
• Administração segura de medicamentos, cálculo de dosagens e sondagens.
• Controle rigoroso de sinais vitais e atuação preventiva em biossegurança.
• Trabalho colaborativo em equipes multidisciplinares com foco na empatia e ética.

Estou em busca de novas oportunidades hospitalares e clínicas onde possa contribuir com excelência técnica e carinho no cuidado ao paciente.

📍 Disponibilidade para plantões e escalas.
✉️ Aberta a conexões e oportunidades no setor de Saúde."""

    texto_linkedin_biomed = """=== TÍTULO PROFISSIONAL (Headline do LinkedIn) ===
Biomédica | Análises Clínicas & Diagnóstico Laboratorial | Hematologia & Bioquímica | Biologia Molecular | CRBM Ativo

=== SOBRE MIM (Resumo Profissional) ===
Biomédica com sólida formação prática voltada para a rotina diagnóstica laboratorial, controle de qualidade analítico e biossegurança.

Minhas principais competências e áreas de atuação incluem:
• Atuação nas fases pré-analítica, analítica e pós-analítica de amostras biológicas.
• Operação e calibração de analisadores automatizados em Hematologia, Bioquímica e Imunologia.
• Interpretação de dados, microscopia e emissão responsável de laudos.
• Aplicação contínua de boas práticas laboratoriais (BPL) e controle de qualidade (CQI/CQE).

Busco oportunidades em laboratórios de análises clínicas, hospitais e centros de diagnóstico para somar à equipe com precisão, agilidade e rigor científico.

📍 Disponível para novos desafios e oportunidades na área diagnóstica."""

    with col_lk1:
        st.markdown("<h4 style='color: #C2185B !important;'>🩺 Opção 1: Foco em Enfermagem</h4>", unsafe_allow_html=True)
        st.markdown(f'<div class="doc-display-box">{texto_linkedin_enfermagem}</div>', unsafe_allow_html=True)
        st.download_button(
            label="📥 Baixar Modelo LinkedIn Enfermagem (.txt)",
            data=texto_linkedin_enfermagem,
            file_name="Perfil_LinkedIn_Enfermagem.txt",
            mime="text/plain",
            use_container_width=True
        )

    with col_lk2:
        st.markdown("<h4 style='color: #00695C !important;'>🔬 Opção 2: Foco em Biomedicina</h4>", unsafe_allow_html=True)
        st.markdown(f'<div class="doc-display-box">{texto_linkedin_biomed}</div>', unsafe_allow_html=True)
        st.download_button(
            label="📥 Baixar Modelo LinkedIn Biomedicina (.txt)",
            data=texto_linkedin_biomed,
            file_name="Perfil_LinkedIn_Biomedicina.txt",
            mime="text/plain",
            use_container_width=True
        )

# ================= TAB 7: TRAJETO, UBER, NOTÍCIAS & BEM-ESTAR =================
with tab_rotas_emerg:
    st.markdown("<h2 style='color: #AD1457 !important;'>🗺️ Trajeto, Uber, Notícias & Cuidado com Você 💕</h2>", unsafe_allow_html=True)

    SEU_WHATSAPP = "5511913129697"

    st.markdown(f"""
    <div class="wellness-card">
        <h3 style="color:#C2185B !important; margin:0 0 10px 0;">🌸 Como você está agora, meu amor? (Check-in de Saúde & Humor)</h3>
        <p style="color:#4A1525; font-size:0.95rem; margin-bottom:14px;">
            Plantões e rotina de estudos são intensos. Cuide de você a cada etapa! Atualize seu status e envie direto para o Thiago saber como você está:
        </p>
    </div>
    """, unsafe_allow_html=True)

    col_check1, col_check2 = st.columns([1, 1])
    with col_check1:
        st.markdown("#### 📋 Check-list de Saúde Física:")
        bebeu_agua = st.checkbox("💧 Beba pelo menos 500ml de água agora", value=False)
        alimentou = st.checkbox("🥗 Conseguiu fazer uma refeição adequada?", value=False)
        descansou = st.checkbox("💺 Descansou as pernas / sentou um pouco no intervalo?", value=False)
        dor = st.checkbox("💊 Sem dor nas costas ou dor de cabeça?", value=True)

    with col_check2:
        st.markdown("#### 🎭 Humor & Energia Atual (Mood):")
        mood_atual = st.radio(
            "Seu estado de espírito agora:",
            [
                "✨ Radiante, motivada e tranquila!",
                "🧸 Cansadinha da rotina, mas em paz",
                "😴 Exausta do plantão/faculdade (preciso da minha caminha)",
                "🤯 Estressada / Correria pesada hoje",
                "🥺 Com saudades do meu amor"
            ],
            index=1
        )

    status_agua = "Tomou água: Sim" if bebeu_agua else "Tomou água: Ainda não"
    status_comida = "Alimentada: Sim" if alimentou else "Alimentada: Ainda não"
    status_descanso = "Descansou: Sim" if descansou else "Descansou: Não"
    
    txt_mood_notif = (
        f"Oi, amor! Passando para te atualizar sobre mim agora:\n\n"
        f"• Meu Humor/Energia: {mood_atual}\n"
        f"• Saúde Física: {status_agua} | {status_comida} | {status_descanso}\n\n"
        f"Te amo muito! 💕"
    )
    link_zap_mood = f"https://api.whatsapp.com/send?phone={SEU_WHATSAPP}&text={urllib.parse.quote(txt_mood_notif)}"

    st.markdown(f"""
    <div style="text-align:center; margin:16px 0 24px 0;">
        <a href="{link_zap_mood}" target="_blank" class="btn-safety-alert" style="padding:12px 28px; font-size:1rem;">
            💌 Enviar Meu Check-in de Humor & Saúde p/ Thiago
        </a>
    </div>
    """, unsafe_allow_html=True)

    st.markdown("---")

    col_rot1, col_rot2 = st.columns(2)
    with col_rot1:
        msg_saida_plantao = "Oi, amor! Estou saindo do plantão agora e já a caminho de casa. Te aviso assim que chegar!"
        link_aviso_thiago = f"https://api.whatsapp.com/send?phone={SEU_WHATSAPP}&text={urllib.parse.quote(msg_saida_plantao)}"

        st.markdown(f"""
        <div class="emergency-card">
            <h4 style="color:#C2185B !important; margin:0 0 10px 0;">🚨 Botão de Segurança p/ Voltar de Plantão</h4>
            <p style="color:#333333 !important; font-size:0.92rem; line-height:1.4; margin-bottom:16px;">
                Saindo de noite ou de madrugada? Clique para mandar mensagem instantânea direta no WhatsApp do Thiago:
            </p>
            <a href="{link_aviso_thiago}" target="_blank" class="btn-safety-alert">
                📲 Mandar Aviso de Saída de Plantão p/ Thiago
            </a>
        </div>
        """, unsafe_allow_html=True)
    
    with col_rot2:
        st.markdown("""
        <h4 style="color:#880E4F !important;">📞 Contatos Úteis de Emergência & Saúde:</h4>
        <ul style="color:#333333 !important; font-weight:600; line-height: 1.8;">
            <li>🚑 <b>SAMU:</b> 192</li>
            <li>🚓 <b>Polícia Militar:</b> 190</li>
            <li>🩺 <b>COREN-PA (Belém):</b> (91) 3262-6052</li>
            <li>🩺 <b>COREN-SP (Capital):</b> (11) 3225-6300</li>
            <li>🩺 <b>COREN-RJ (Capital):</b> (21) 3232-3232</li>
            <li>🔬 <b>CRBM-4 (Norte / PA):</b> (91) 3212-3850</li>
            <li>🔬 <b>CRBM-1 (São Paulo):</b> (11) 3347-5555</li>
            <li>🔬 <b>CRBM-2 (Rio de Janeiro / ES):</b> (21) 2568-1215</li>
        </ul>
        """, unsafe_allow_html=True)

    st.markdown("---")

    # --- RADAR METEOROLÓGICO: AVISO DE CHUVAS ---
    st.markdown("<h3 style='color: #C2185B !important;'>🌧️ Alerta Meteorológico (Saída de Plantão / Faculdade)</h3>", unsafe_allow_html=True)
    
    col_tempo_sel, col_tempo_info = st.columns([1, 2])
    with col_tempo_sel:
        cidade_clima = st.selectbox("Região do Plantão / Faculdade:", ["Belém - PA", "São Paulo - SP", "Rio de Janeiro - RJ"])
        dados_clima = obter_previsao_tempo(cidade_clima)
    
    with col_tempo_info:
        prob_c = dados_clima["prob_chuva"]
        temp_c = dados_clima["temp"]
        if prob_c >= 50:
            alerta_msg = f"☔ **Atenção:** Alta probabilidade de chuva ({prob_c}% - {temp_c}°C). Leve guarda-chuva ou considere voltar de Uber/Táxi para não se molhar!"
            st.warning(alerta_msg)
        else:
            alerta_msg = f"☀️ **Tempo Estável:** Pouca chance de chuva ({prob_c}% - {temp_c}°C). Trajeto tranquilo para sair do plantão ou faculdade!"
            st.success(alerta_msg)

    st.markdown("---")

    # --- SIMULADOR DE TRAJETO, TARIFAS DE ÔNIBUS & ESTIMATIVA DE UBER/TÁXI ---
    st.markdown("<h3 style='color: #C2185B !important;'>🚌 Trajeto, Ônibus & Estimativa de Uber / Táxi</h3>", unsafe_allow_html=True)

    TARIFAS_2026 = {
        "Belém - PA (SEMOB / RMB)": 4.60,
        "São Paulo - SP (SPTrans)": 5.30,
        "Rio de Janeiro - RJ (SMTR / BRT)": 5.00
    }

    col_calc1, col_calc2, col_calc3 = st.columns(3)
    with col_calc1:
        cidade_sel = st.selectbox("Cidade da Vaga:", list(TARIFAS_2026.keys()), key="sel_cidade_calc")
        tarifa_unitaria = TARIFAS_2026[cidade_sel]
    with col_calc2:
        escala_tipo = st.selectbox("Regime de Trabalho:", ["Plantão 12x36 (15 dias/mês)", "Rotina Comercial (22 dias/mês)", "Apenas 1 Dia (Entrevista)"])
    with col_calc3:
        endereco_destino = st.text_input("Destino (Hospital, Laboratório ou Faculdade):", placeholder="Ex: Hospital Porto Dias, Einstein, Faculdade...")

    dias_mult = 15 if "15" in escala_tipo else (22 if "22" in escala_tipo else 1)
    gasto_ida_volta_dia = tarifa_unitaria * 2
    gasto_total = gasto_ida_volta_dia * dias_mult

    estimativa_uber = 24.50 if "Belém" in cidade_sel else (32.00 if "São Paulo" in cidade_sel else 29.00)

    col_val1, col_val2, col_val3, col_val4 = st.columns(4)
    with col_val1:
        st.markdown(f"""
        <div style="background:#FFFFFF; border:2px solid #FFCCD7; border-radius:12px; padding:12px; text-align:center;">
            <span style="font-size:0.8rem; color:#880E4F; font-weight:700;">Passagem de Ônibus:</span>
            <h4 style="color:#C2185B; margin:4px 0 0 0;">R$ {tarifa_unitaria:.2f}</h4>
        </div>
        """, unsafe_allow_html=True)
    with col_val2:
        st.markdown(f"""
        <div style="background:#FFFFFF; border:2px solid #FFCCD7; border-radius:12px; padding:12px; text-align:center;">
            <span style="font-size:0.8rem; color:#880E4F; font-weight:700;">Ônibus Ida & Volta/dia:</span>
            <h4 style="color:#C2185B; margin:4px 0 0 0;">R$ {gasto_ida_volta_dia:.2f}</h4>
        </div>
        """, unsafe_allow_html=True)
    with col_val3:
        st.markdown(f"""
        <div style="background:#FFFFFF; border:2px solid #FFCCD7; border-radius:12px; padding:12px; text-align:center;">
            <span style="font-size:0.8rem; color:#880E4F; font-weight:700;">Gasto Ônibus Mensal:</span>
            <h4 style="color:#00695C; margin:4px 0 0 0;">R$ {gasto_total:.2f}</h4>
        </div>
        """, unsafe_allow_html=True)
    with col_val4:
        st.markdown(f"""
        <div style="background:#FFFFFF; border:2px solid #FFCCD7; border-radius:12px; padding:12px; text-align:center;">
            <span style="font-size:0.8rem; color:#880E4F; font-weight:700;">Uber / Táxi Estimado:</span>
            <h4 style="color:#E65100; margin:4px 0 0 0;">~ R$ {estimativa_uber:.2f}</h4>
        </div>
        """, unsafe_allow_html=True)

    destino_query = endereco_destino.strip() or "Hospital"
    cidade_query = cidade_sel.split()[0]
    
    rota_maps = f"https://www.google.com/maps/dir/?api=1&destination={urllib.parse.quote(f'{destino_query}, {cidade_query}')}&travelmode=transit"
    link_uber = f"https://m.uber.com/ul/?action=setPickup&pickup=my_location&dropoff[formatted_address]={urllib.parse.quote(f'{destino_query}, {cidade_query}')}"
    link_bus = "https://www.cittamobi.com.br" if "Belém" in cidade_sel else ("https://olhovivo.sptrans.com.br" if "São Paulo" in cidade_sel else "https://www.rio.rj.gov.br/web/smtr")

    st.markdown(f"""
    <div style="text-align:center; margin-top:16px;">
        <a href="{rota_maps}" target="_blank" class="action-link" style="background:#FF69B4; color:white !important; font-weight:bold; padding:10px 18px;">
            🗺️ Ver Trajeto no Maps
        </a>
        <a href="{link_uber}" target="_blank" class="btn-uber-direct" style="padding:10px 18px;">
            🚗 Chamar Uber p/ Destino
        </a>
        <a href="{link_bus}" target="_blank" class="action-link" style="background:#E1F5FE; color:#0277BD !important; font-weight:bold; padding:10px 18px; border:1px solid #B3E5FC;">
            ⏱️ Chegada dos Ônibus em Tempo Real
        </a>
    </div>
    """, unsafe_allow_html=True)

    st.markdown("---")

    # --- RADAR DE NOTÍCIAS 24H (COFEN & CFBM) ---
    st.markdown("<h3 style='color: #880E4F !important;'>📰 Notícias & Acontecimentos Oficiais 24h (Cofen & CFBM)</h3>", unsafe_allow_html=True)
    
    noticias_enf, noticias_bio = obter_noticias_reais_saude()

    col_not1, col_not2 = st.columns(2)
    with col_not1:
        st.markdown("<h4 style='color:#C2185B !important;'>🩺 Enfermagem (Feed Oficial Cofen)</h4>", unsafe_allow_html=True)
        if noticias_enf:
            for n in noticias_enf:
                st.markdown(f"""
                <div class="news-card">
                    <h5 style="color:#C2185B !important; margin:0 0 6px 0;">{n['titulo']}</h5>
                    <small style="color:#880E4F; font-weight:600;">{n['data']}</small><br>
                    <a href="{n['link']}" target="_blank" style="color:#E91E63; font-weight:bold; font-size:0.85rem; text-decoration:none;">Ler notícia na íntegra no Cofen 🔗</a>
                </div>
                """, unsafe_allow_html=True)
        else:
            st.markdown("""
            <div class="news-card">
                <h5 style="color:#C2185B !important; margin:0 0 6px 0;">Piso Salarial e Novas Contratações Hospitalares</h5>
                <p style="color:#222; font-size:0.9rem;">Repasses e editais de hospitais filantrópicos e privados em andamento pelo Brasil.</p>
                <a href="https://www.cofen.gov.br" target="_blank" style="color:#E91E63; font-weight:bold;">Acessar Portal Cofen 🔗</a>
            </div>
            """, unsafe_allow_html=True)

    with col_not2:
        st.markdown("<h4 style='color:#00695C !important;'>🔬 Biomedicina (Feed Oficial CFBM)</h4>", unsafe_allow_html=True)
        if noticias_bio:
            for n in noticias_bio:
                st.markdown(f"""
                <div class="news-card">
                    <h5 style="color:#00695C !important; margin:0 0 6px 0;">{n['titulo']}</h5>
                    <small style="color:#004D40; font-weight:600;">{n['data']}</small><br>
                    <a href="{n['link']}" target="_blank" style="color:#00695C; font-weight:bold; font-size:0.85rem; text-decoration:none;">Ler notícia na íntegra no CFBM 🔗</a>
                </div>
                """, unsafe_allow_html=True)
        else:
            st.markdown("""
            <div class="news-card">
                <h5 style="color:#00695C !important; margin:0 0 6px 0;">Diagnóstico Molecular e Habilitações Clínicas</h5>
                <p style="color:#222; font-size:0.9rem;">Resoluções atualizadas para atuação em análises clínicas laboratoriais e genética.</p>
                <a href="https://cfbm.gov.br" target="_blank" style="color:#00695C; font-weight:bold;">Acessar Portal CFBM 🔗</a>
            </div>
            """, unsafe_allow_html=True)

# ================= TAB 8: CANDIDATURAS =================
with tab_candidaturas:
    st.markdown("<h2 style='color: #AD1457 !important;'>📋 Painel de Acompanhamento</h2>", unsafe_allow_html=True)
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
<div style="text-align: center; color: #AD1457 !important; font-size: 0.95rem; font-weight: 700; padding: 10px;">
    Desenvolvido com todo amor por Thiago Zuza 💕 🐾
</div>
""", unsafe_allow_html=True)
