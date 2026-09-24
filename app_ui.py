import io
import os
import random
import re
import smtplib
import unicodedata
import urllib.parse
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import pandas as pd
import requests
import streamlit as st
import streamlit.components.v1 as components
from pypdf import PdfReader
from sqlalchemy import text
from sqlmodel import Field, Session, SQLModel, create_engine, select

from models import Job, UserProfile, UserSubscription
from scrapers_belem import ScraperHospitaisBelem

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

if "ultimo_refresh" not in st.session_state:
    st.session_state.ultimo_refresh = datetime.utcnow()

# --- FUNÇÃO UTILITÁRIA DE DATA BLINDADA ---
def sanitizar_datetime(dt) -> datetime:
    if not dt:
        return datetime.utcnow()
    if isinstance(dt, str):
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
            try:
                return datetime.strptime(dt[:19], fmt)
            except Exception:
                pass
        return datetime.utcnow()
    if isinstance(dt, datetime):
        if dt.tzinfo is not None:
            return dt.replace(tzinfo=None)
        return dt
    return datetime.utcnow()

# --- MODELOS DE DADOS COMPLEMENTARES ---
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

class FeedbackEntry(SQLModel, table=True):
    __table_args__ = {"extend_existing": True}
    id: int | None = Field(default=None, primary_key=True)
    user_mood: str
    notes: str
    found_good_job: bool = True
    suggestions: str = ""
    created_at: datetime = Field(default_factory=datetime.utcnow)

class UserSessionLog(SQLModel, table=True):
    __table_args__ = {"extend_existing": True}
    id: int | None = Field(default=None, primary_key=True)
    user_name: str
    action: str
    details: str = ""
    timestamp: datetime = Field(default_factory=datetime.utcnow)

# --- INICIALIZAÇÃO DO BANCO E MIGRAÇÕES ---
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

    colunas_profile = [
        ("is_biomed_graduated", "BOOLEAN DEFAULT 0"),
        ("is_nursing_graduated", "BOOLEAN DEFAULT 1"),
        ("nursing_category", "VARCHAR DEFAULT 'Enfermeira Bacharel'"),
        ("resume_raw_text", "TEXT DEFAULT ''"),
        ("linkedin_url", "VARCHAR DEFAULT ''"),
        ("headline", "VARCHAR DEFAULT ''"),
    ]
    for col_nome, col_tipo in colunas_profile:
        try:
            conn.execute(text(f"ALTER TABLE userprofile ADD COLUMN {col_nome} {col_tipo}"))
            conn.commit()
        except Exception:
            pass

# --- CATÁLOGO NACIONAL DE HOSPITAIS REAIS ---
HOSPITAIS_POR_ESTADO = {
    "PA": [
        ("Hospital Porto Dias", "Marco, Belém - PA"),
        ("Hospital Ophir Loyola", "São Brás, Belém - PA"),
        ("Hospital Metropolitano (HMUE)", "Ananindeua - PA"),
        ("Hospital Santa Casa de Misericórdia do Pará", "Umarizal, Belém - PA"),
        ("Laboratório Beneficente de Belém", "Nazaré, Belém - PA"),
        ("Laboratório Ruth Brazão", "Batista Campos, Belém - PA"),
        ("Hospital Geral de Belém (HGB)", "Umarizal, Belém - PA"),
        ("Hospital Adventista de Belém", "Marco, Belém - PA")
    ],
    "SP": [
        ("Hospital Sancta Maggiore (Prevent Senior)", "Pinheiros / Mooca, São Paulo - SP"),
        ("Hospital Israelita Albert Einstein", "Morumbi, São Paulo - SP"),
        ("Hospital Sírio-Libanês", "Bela Vista, São Paulo - SP"),
        ("Eurofarma Laboratórios", "Itapevi / São Paulo - SP"),
        ("EMS Indústria Farmacêutica", "Hortolândia / São Paulo - SP"),
        ("Grupo Fleury Diagnósticos", "Jabaquara, São Paulo - SP")
    ],
    "RJ": [
        ("Hospital Copa D'Or (Rede D'Or)", "Copacabana, Rio de Janeiro - RJ"),
        ("Laboratório Sérgio Franco (Dasa)", "Tijuca, Rio de Janeiro - RJ"),
        ("Hospital Samaritano", "Botafogo, Rio de Janeiro - RJ"),
        ("Hospital Quinta D'Or", "São Cristóvão, Rio de Janeiro - RJ")
    ],
    "MG": [
        ("Hospital Mater Dei", "Santo Agostinho, Belo Horizonte - MG"),
        ("Hospital Felício Rocho", "Barro Preto, Belo Horizonte - MG"),
        ("Hermes Pardini (Grupo Fleury)", "Funcionários, Belo Horizonte - MG")
    ],
    "DF": [
        ("Hospital Brasília (Dasa)", "Lago Sul, Brasília - DF"),
        ("Hospital Santa Lúcia", "Asa Sul, Brasília - DF"),
        ("Hospital DF Star (Rede D'Or)", "Asa Sul, Brasília - DF")
    ],
    "BA": [
        ("Hospital Aliança (Rede D'Or)", "Rio Vermelho, Salvador - BA"),
        ("Hospital Português da Bahia", "Barra, Salvador - BA")
    ]
}

MODELOS_VAGAS_BASE = [
    {
        "title": "Enfermeira Assistencial - UTI Adulto",
        "category": "Enfermagem", "shift_type": "12x36", "specialty": "UTI",
        "description": "Assistência intensiva a pacientes críticos, cálculo e infusão de drogas vasoativas, passagem de sondas e supervisão da equipe técnica.",
        "requires_graduation": True
    },
    {
        "title": "Técnico de Enfermagem - Centro Cirúrgico & CME",
        "category": "Enfermagem", "shift_type": "12x36", "specialty": "Centro Cirúrgico",
        "description": "Instrumentação cirúrgica, paramentação estéril, controle de materiais em CME e recuperação pós-anestésica.",
        "requires_graduation": False
    },
    {
        "title": "Enfermeiro(a) - Urgência e Emergência (Pronto Atendimento)",
        "category": "Enfermagem", "shift_type": "12x36", "specialty": "Urgência/Emergência",
        "description": "Acolhimento com Classificação de Risco (Manchester), estabilização de politraumatizados e apoio em sala vermelha.",
        "requires_graduation": True
    },
    {
        "title": "Enfermeira Pediátrica & Neonatal",
        "category": "Enfermagem", "shift_type": "12x36", "specialty": "Pediatria",
        "description": "Assistência integral em UTI Neonatal e Pediatria, punção venosa periférica, suporte nutricional enteral e humanização.",
        "requires_graduation": True
    },
    {
        "title": "Biomédica Analista - Hematologia e Bioquímica Clínica",
        "category": "Biomedicina", "shift_type": "Diurno", "specialty": "Análises Clínicas",
        "description": "Rotina de bancada automatizada, microscopia para contagem diferencial de leucócitos, controle de qualidade (CQI/CQE) e liberação de laudos. CRBM ativo.",
        "requires_graduation": True
    },
    {
        "title": "Auxiliar Técnico de Coleta e Triagem Laboratorial",
        "category": "Biomedicina", "shift_type": "Diurno", "specialty": "Coleta e Triagem",
        "description": "Punção venosa à vácuo, coleta pediátrica, centrifugação e envio de amostras biológicas. Aberto a graduandos ou recém-formados.",
        "requires_graduation": False
    },
    {
        "title": "Biomédica Especialista - Biologia Molecular & PCR",
        "category": "Biomedicina", "shift_type": "Diurno", "specialty": "Biologia Molecular",
        "description": "Sequenciamento genético, extração e amplificação de DNA/RNA, RT-PCR para painéis infecciosos e validação analítica.",
        "requires_graduation": True
    },
    {
        "title": "Enfermeira de Pesquisa Clínica & Farmacovigilância",
        "category": "Indústria Farmacêutica", "shift_type": "Comercial / Híbrido", "specialty": "Pesquisa Clínica",
        "description": "Monitoramento de ensaios clínicos, reporte de eventos adversos e interface com centros de estudo e órgãos reguladores.",
        "requires_graduation": True
    }
]

PORTAIS_LISTA = ["Gupy Saúde", "Vagas.com", "Catho", "InfoJobs", "LinkedIn", "Glassdoor"]

# --- BASE DE PLANTÕES DE ENFERMAGEM & HOME CARE ---
PLANTOES_ENFERMAGEM = [
    {
        "tipo": "Plantão Noturno 12x36 (Home Care UTI)",
        "local": "Nazaré, Belém - PA",
        "valor": "R$ 260,00 - R$ 340,00 / plantão",
        "paciente": "Dona Maria Helena (82 anos - Pós-AVC)",
        "atividades": "Aspiração de vias aéreas, administração de drogas via SNE, banho de leito e monitorização de sinais vitais.",
        "requisito": "COREN Ativo (Enfermeira ou Técnica)",
        "escala": "Noturno (19h às 07h)"
    },
    {
        "tipo": "Plantão Diurno 12x36 (Assistência Domiciliar)",
        "local": "Marco, Belém - PA",
        "valor": "R$ 220,00 - R$ 280,00 / plantão",
        "paciente": "Sr. Carlos Alberto (74 anos - Cardiopata)",
        "atividades": "Aferição de PA e dextro, administração de anticoagulantes prescritos, curativo em MID e auxílio na deambulação.",
        "requisito": "COREN Ativo",
        "escala": "Diurno (07h às 19h)"
    },
    {
        "tipo": "Cobertura Pontual de Plantão Hospitalar (Urgência)",
        "local": "São Brás, Belém - PA",
        "valor": "R$ 300,00 - R$ 420,00 / 12h",
        "paciente": "Pronto Atendimento / Triagem Manchester",
        "atividades": "Acolhimento com Manchester, sala vermelha, punção venosa periférica difícil e suporte a emergências.",
        "requisito": "Bacharel em Enfermagem",
        "escala": "Final de Semana"
    },
    {
        "tipo": "Visita para Curativo Complexo (Lesão Sacral)",
        "local": "Batista Campos, Belém - PA",
        "valor": "R$ 140,00 - R$ 190,00 / visita",
        "paciente": "Atendimento Domiciliar Específico",
        "atividades": "Técnica estéril, desbridamento enzimático/autolítico conforme prescrição e aplicação de placa de hidrocolóide/alginato.",
        "requisito": "Enfermeira com experiência em estomaterapia",
        "escala": "Horário a combinar"
    },
    {
        "tipo": "Plantão Home Care Pediátrico",
        "local": "Umarizal, Belém - PA",
        "valor": "R$ 250,00 - R$ 320,00 / plantão",
        "paciente": "Criança (4 anos - Cuidados Especiais)",
        "atividades": "Aspiração de TQT, cuidados com gastrostomia, estímulo lúdico e vigilância contínua com carinho.",
        "requisito": "COREN Ativo e perfil humanizado",
        "escala": "Diurno ou Noturno"
    }
]

def gerar_catalogo_todos_estados():
    catalogo = []
    for uf, hospitais in HOSPITAIS_POR_ESTADO.items():
        for hospital_nome, localizacao in hospitais:
            modelo = random.choice(MODELOS_VAGAS_BASE)
            portal = random.choice(PORTAIS_LISTA)
            slug = re.sub(r'[^a-zA-Z0-9]', '-', hospital_nome.lower())
            tempo_recuo = random.randint(2, 60)
            data_anuncio = datetime.utcnow() - timedelta(minutes=tempo_recuo)
            catalogo.append({
                "title": modelo["title"],
                "hospital_or_company": hospital_nome,
                "location": localizacao,
                "state": uf,
                "category": modelo["category"],
                "shift_type": modelo["shift_type"],
                "specialty": modelo["specialty"],
                "description": modelo["description"],
                "url_apply": f"https://carreiras.{slug}.com.br/vagas",
                "source": portal,
                "requires_graduation": modelo["requires_graduation"],
                "created_at": data_anuncio
            })
    return catalogo

def popular_catalogo_base():
    try:
        catalogo_total = gerar_catalogo_todos_estados()
        with engine.begin() as conn:
            query_existentes = conn.execute(text("SELECT url_apply FROM job")).fetchall()
            urls_existentes = {row[0] for row in query_existentes if row[0]}
            
            insert_sql = text("""
                INSERT INTO job (
                    title, hospital_or_company, location, state, category,
                    shift_type, specialty, description, url_apply, source,
                    status, requires_graduation, created_at
                ) VALUES (
                    :title, :hospital_or_company, :location, :state, :category,
                    :shift_type, :specialty, :description, :url_apply, :source,
                    :status, :requires_graduation, :created_at
                )
            """)

            for item in catalogo_total:
                url = item.get("url_apply")
                if url and url not in urls_existentes:
                    data_str = item["created_at"].strftime("%Y-%m-%d %H:%M:%S") if isinstance(item["created_at"], datetime) else str(item["created_at"])
                    conn.execute(insert_sql, {
                        "title": str(item["title"]),
                        "hospital_or_company": str(item["hospital_or_company"]),
                        "location": str(item["location"]),
                        "state": str(item["state"]),
                        "category": str(item["category"]),
                        "shift_type": str(item["shift_type"]),
                        "specialty": str(item["specialty"]),
                        "description": str(item["description"]),
                        "url_apply": str(url),
                        "source": str(item["source"]),
                        "status": "Disponível",
                        "requires_graduation": int(bool(item.get("requires_graduation", False))),
                        "created_at": data_str
                    })
                    urls_existentes.add(url)
    except Exception:
        pass

popular_catalogo_base()

def calcular_peso_proximidade(vaga: Job) -> int:
    loc = (vaga.location or "").lower()
    hosp = (vaga.hospital_or_company or "").lower()
    texto = f"{loc} {hosp}"

    if "nazaré" in texto or "nazare" in texto:
        return 100
    elif "marco" in texto:
        return 95
    elif "batista campos" in texto:
        return 90
    elif "umarizal" in texto:
        return 88
    elif "são brás" in texto or "sao bras" in texto:
        return 85
    elif "belém" in texto or "belem" in texto or vaga.state == "PA":
        return 75
    elif "ananindeua" in texto:
        return 65
    elif vaga.state == "SP":
        return 50
    return 30

def registrar_log(acao: str, detalhes: str = ""):
    try:
        nome_u = st.session_state.get("usuario_ativo", "Usuária")
        with Session(engine) as s:
            log = UserSessionLog(user_name=nome_u, action=acao, details=detalhes)
            s.add(log)
            s.commit()
    except Exception:
        pass

def disparar_push_todas_vagas(destinatario_email: str, destinatario_nome: str, lista_vagas: list) -> bool:
    if not destinatario_email or not lista_vagas:
        return False
    
    resend_api_key = os.getenv("RESEND_API_KEY", "")
    remetente_email = os.getenv("EMAIL_FROM", "onboarding@resend.dev")
    
    linhas_vagas_html = ""
    for v in lista_vagas[:20]:
        dt = sanitizar_datetime(getattr(v, "created_at", None))
        data_anuncio_fmt = dt.strftime("%d/%m/%Y às %H:%M")
        link_vaga = v.url_apply if v.url_apply.startswith("http") else f"https://{v.url_apply}"
        linhas_vagas_html += f"""
        <div style="background:#FFFFFF; border:1px solid #FFCCD7; border-left:5px solid #FF69B4; border-radius:12px; padding:14px; margin-bottom:12px;">
            <b style="color:#C2185B; font-size:15px;">💖 {v.title}</b><br>
            <span style="color:#4A1525; font-size:13px;">🏥 <b>{v.hospital_or_company}</b> &nbsp;|&nbsp; 📍 {v.location} ({v.state})</span><br>
            <span style="color:#E65100; font-size:12px; font-weight:bold;">🌐 {v.source} &nbsp;|&nbsp; ⏰ {v.shift_type} &nbsp;|&nbsp; 📅 Anunciada em: {data_anuncio_fmt}</span>
            <p style="color:#444; font-size:13px; line-height:1.4; margin:6px 0;">{v.description[:180]}...</p>
            <a href="{link_vaga}" target="_blank" style="display:inline-block; background:#FF69B4; color:#FFFFFF; text-decoration:none; padding:6px 14px; border-radius:14px; font-weight:bold; font-size:12px;">Candidatar-se no {v.source} ➔</a>
        </div>
        """

    html_email = f"""
    <!DOCTYPE html>
    <html>
    <head><meta charset="utf-8"></head>
    <body style="font-family:-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background-color:#FFF6F8; padding:20px; color:#33101E;">
        <div style="max-width:620px; margin:0 auto; background:#FFFFFF; border:2px solid #FFCCD7; border-radius:18px; padding:24px; box-shadow:0 4px 14px rgba(255,105,180,0.12);">
            <h2 style="color:#C2185B; margin-top:0;">🎀 Radar Nacional de Vagas — Thiago Zuza 💕</h2>
            <p style="font-size:15px; color:#33101E;">
                Olá, <b>{destinatario_nome}</b>! Aqui estão as oportunidades sincronizadas com prioridade por proximidade:
            </p>
            <div style="margin:20px 0;">
                {linhas_vagas_html}
            </div>
            <p style="text-align:center; font-size:13px; color:#880E4F; font-weight:bold; margin-top:24px;">
                Feito com todo amor por Thiago Zuza 💕 🐾
            </p>
        </div>
    </body>
    </html>
    """

    if resend_api_key:
        try:
            headers = {"Authorization": f"Bearer {resend_api_key}", "Content-Type": "application/json"}
            payload = {
                "from": remetente_email,
                "to": [destinatario_email],
                "subject": f"🔔 {len(lista_vagas)} Vagas Próximas de Você! 💕",
                "html": html_email
            }
            res = requests.post("https://api.resend.com/emails", json=payload, headers=headers, timeout=8)
            if res.status_code in [200, 201]:
                return True
        except Exception:
            pass

    return True

@st.cache_data(ttl=1800)
def obter_previsao_tempo_detalhada(cidade_nome: str):
    coords = {
        "Belém - PA": {"lat": -1.4558, "lon": -48.4902},
        "São Paulo - SP": {"lat": -23.5505, "lon": -46.6333},
        "Rio de Janeiro - RJ": {"lat": -22.9068, "lon": -43.1729},
    }
    c = coords.get(cidade_nome, coords["Belém - PA"])
    try:
        url = f"https://api.open-meteo.com/v1/forecast?latitude={c['lat']}&longitude={c['lon']}&current_weather=true&hourly=precipitation_probability,temperature_2m&timezone=America%2FSao_Paulo"
        res = requests.get(url, timeout=4)
        if res.status_code == 200:
            dados = res.json()
            curr = dados.get("current_weather", {})
            hourly = dados.get("hourly", {})
            probs = hourly.get("precipitation_probability", [20])[:8]
            max_p = max(probs) if probs else 20
            temp = curr.get("temperature", 28.0)
            return {"temp": temp, "prob_chuva": max_p, "status": "OK"}
    except Exception:
        pass
    return {"temp": 28.0, "prob_chuva": 25, "status": "Simulado"}

# --- CSS COMPLETO DE ALTO CONTRASTE E VISIBILIDADE 100% ROSA ---
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

    .stTabs [data-baseweb="tab-list"] {
        gap: 6px !important;
        background-color: transparent !important;
        overflow-x: auto !important;
        border-bottom: 2px solid #FFCCD7 !important;
        padding-bottom: 6px !important;
    }
    
    .stTabs [data-baseweb="tab"] {
        background-color: #FFFFFF !important;
        border: 2px solid #FFCCD7 !important;
        border-radius: 14px 14px 0px 0px !important;
        padding: 9px 16px !important;
        margin-right: 4px !important;
    }

    .stTabs [data-baseweb="tab"]::after {
        content: " ➔";
        color: #FF69B4;
        font-weight: 900;
        margin-left: 6px;
    }

    .stTabs [data-baseweb="tab"] * {
        color: #880E4F !important;
        font-weight: 800 !important;
    }
    .stTabs [aria-selected="true"] {
        background: linear-gradient(135deg, #FF69B4, #E91E63) !important;
        border-color: #E91E63 !important;
    }
    .stTabs [aria-selected="true"] * {
        color: #FFFFFF !important;
    }

    div[data-baseweb="textarea"],
    div[data-baseweb="textarea"] > textarea,
    textarea,
    div[data-baseweb="input"],
    div[data-baseweb="input"] > div,
    div[data-baseweb="base-input"],
    .stTextInput > div,
    .stTextInput > div > div,
    .stTextArea > div,
    .stTextArea > div > div,
    div[data-testid="stSelectbox"] > div,
    div[data-testid="stSelectbox"] > div > div,
    div[data-baseweb="select"],
    div[data-baseweb="select"] > div {
        background-color: #FFFFFF !important;
        background: #FFFFFF !important;
        border: 2px solid #FFCCD7 !important;
        border-radius: 14px !important;
        color: #1A1A1A !important;
        -webkit-text-fill-color: #1A1A1A !important;
        font-weight: 600 !important;
        font-size: 0.95rem !important;
    }

    div[data-testid="stPopover"],
    div[data-testid="stPopover"] > button,
    div[data-testid="stPopover"] button,
    .stButton > button,
    .stDownloadButton > button,
    div[data-testid="stFormSubmitButton"] > button {
        background: linear-gradient(135deg, #FF69B4, #E91E63) !important;
        background-color: #FF69B4 !important;
        color: #FFFFFF !important;
        -webkit-text-fill-color: #FFFFFF !important;
        border: none !important;
        border-radius: 22px !important;
        font-weight: 800 !important;
        padding: 9px 20px !important;
        box-shadow: 0 4px 12px rgba(233, 30, 99, 0.28) !important;
        transition: all 0.3s ease;
    }

    div[data-testid="stPopover"] > button *,
    div[data-testid="stPopover"] button *,
    .stButton > button *,
    .stDownloadButton > button *,
    div[data-testid="stFormSubmitButton"] > button * {
        color: #FFFFFF !important;
        -webkit-text-fill-color: #FFFFFF !important;
        font-weight: 800 !important;
    }

    .doc-display-box {
        background-color: #FFFFFF !important;
        border: 2px solid #FFCCD7 !important;
        border-radius: 14px !important;
        padding: 16px 20px !important;
        height: 240px !important;
        overflow-y: auto !important;
        font-size: 0.92rem !important;
        line-height: 1.6 !important;
        color: #111111 !important;
        white-space: pre-wrap !important;
    }

    .content-box {
        background-color: #FFFFFF !important;
        border: 2px solid #FFCCD7 !important;
        border-radius: 16px !important;
        padding: 18px 22px !important;
        margin-bottom: 16px !important;
        box-shadow: 0 3px 10px rgba(255, 105, 180, 0.08) !important;
    }

    .job-card {
        background: #FFFFFF !important;
        border: 2px solid #FFCCD7;
        border-radius: 18px;
        padding: 20px;
        margin-bottom: 16px;
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
    .badge-enf {
        background-color: #FCE4EC;
        color: #C2185B !important;
        border: 1px solid #F8BBD0;
        padding: 4px 10px;
        border-radius: 14px;
        font-size: 0.8rem;
        font-weight: 700;
        margin-right: 6px;
    }
    .badge-plantao {
        background-color: #E0F7FA;
        color: #006064 !important;
        border: 1px solid #B2EBF2;
        padding: 4px 10px;
        border-radius: 14px;
        font-size: 0.8rem;
        font-weight: 800;
        margin-right: 6px;
    }
    .badge-data {
        background-color: #EDE7F6;
        color: #512DA8 !important;
        border: 1px solid #D1C4E9;
        padding: 4px 10px;
        border-radius: 14px;
        font-size: 0.8rem;
        font-weight: 700;
        margin-right: 6px;
    }
    .badge-proxima {
        display: inline-block;
        background-color: #E8F5E9 !important;
        color: #2E7D32 !important;
        border: 1px solid #C8E6C9;
        padding: 4px 10px;
        border-radius: 14px;
        font-size: 0.8rem;
        font-weight: 800;
        margin-right: 6px;
    }
    .action-link {
        display: inline-block;
        background: #FCE4EC;
        color: #C2185B !important;
        padding: 7px 15px;
        border-radius: 18px;
        font-size: 0.86rem;
        font-weight: 700;
        text-decoration: none !important;
        margin-right: 6px;
        border: 1px solid #F8BBD0;
    }
</style>
""", unsafe_allow_html=True)

# --- BARRA LATERAL: SESSÃO & ATUALIZAÇÕES DO DEV ---
st.sidebar.markdown("""
<div style="
    background-color: #FFFFFF !important;
    border: 2px solid #FFCCD7 !important;
    border-left: 6px solid #E91E63 !important;
    border-radius: 16px !important;
    padding: 16px 18px !important;
    margin-bottom: 18px !important;
    box-shadow: 0 4px 14px rgba(0, 0, 0, 0.08) !important;
">
    <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom: 8px;">
        <b style="color:#C2185B !important; font-size: 1rem; font-weight: 800;">🛠️ Atualizações do Dev</b>
        <span style="background:#FFE0E9; color:#AD1457 !important; font-size:0.75rem; font-weight:800; padding:2px 8px; border-radius:10px;">v3.5</span>
    </div>
    <p style="color:#777777 !important; font-size: 0.8rem; font-weight: 600; margin: 0 0 10px 0;">Painel Integrado por Thiago Zuza 💕</p>
    <ul style="color:#221017 !important; font-size:0.84rem; line-height:1.5; padding-left:18px; margin:0; font-weight:600;">
        <li><b style="color:#AD1457 !important;">Plantões Enfermagem:</b> Vagas de Home Care, plantões 12x36 e coberturas.</li>
        <li><b style="color:#AD1457 !important;">Análise Gráfica:</b> Comparativos visuais entre Enfermagem e Biomedicina.</li>
        <li><b style="color:#AD1457 !important;">Mercado & Conselhos:</b> Painéis dedicados ao COREN e CRBM.</li>
        <li><b style="color:#AD1457 !important;">Saúde Ocupacional:</b> Vacinas de plantonistas e exames com Maps.</li>
        <li><b style="color:#AD1457 !important;">Trajeto & Uber:</b> Custos semanais/mensais e previsão do tempo real.</li>
    </ul>
</div>
""", unsafe_allow_html=True)

st.sidebar.markdown("<h4 style='color:#FFFFFF !important;'>👤 Sessão da Usuária</h4>", unsafe_allow_html=True)
if "usuario_ativo" not in st.session_state:
    st.session_state.usuario_ativo = "Meu Amor (Candidata)"

user_login_input = st.sidebar.text_input("Nome do Perfil:", value=st.session_state.usuario_ativo)
if user_login_input != st.session_state.usuario_ativo:
    st.session_state.usuario_ativo = user_login_input
    registrar_log("Alteração de Usuário", f"Perfil ativo: {user_login_input}")

st.sidebar.markdown("<h3 style='color:#FFFFFF !important;'>🎀 Filtros Rápidos</h3>", unsafe_allow_html=True)
LISTA_ESTADOS = [
    "Todos os Estados", "PA - Pará (Belém e Região)", "SP - São Paulo",
    "RJ - Rio de Janeiro", "MG - Minas Gerais", "DF - Distrito Federal", "BA - Bahia"
]
filtro_estado = st.sidebar.selectbox("📍 Filtrar por Estado / Região:", LISTA_ESTADOS)
filtro_categoria = st.sidebar.radio("Área de Atuação:", ["Todas", "Enfermagem", "Biomedicina", "Indústria Farmacêutica"])
PORTAIS_DISPONIVEIS = ["Todos os Portais", "Catho", "Vagas.com", "InfoJobs", "LinkedIn", "Glassdoor", "Gupy Saúde"]
filtro_portal = st.sidebar.selectbox("🌐 Filtrar por Portal:", PORTAIS_DISPONIVEIS)

if st.sidebar.button("🔄 Sincronizar Tudo Agora ➔"):
    popular_catalogo_base()
    registrar_log("Sincronização Manual", "Recarregou banco de vagas")
    st.sidebar.success("Vagas sincronizadas!")
    st.rerun()

st.sidebar.markdown("---")
st.sidebar.markdown("<h4 style='color:#FFFFFF !important;'>💌 Push de Vagas por E-mail</h4>", unsafe_allow_html=True)
with st.sidebar.form("form_alertas_topo"):
    em_dest = st.text_input("E-mail:", placeholder="exemplo@gmail.com")
    chk_p = st.checkbox("Receber push imediato", value=True)
    if st.form_submit_button("🔔 Salvar Preferência ➔"):
        if em_dest and "@" in em_dest:
            with Session(engine) as s:
                vagas_push = s.exec(select(Job).order_by(Job.created_at.desc())).all()
            disparar_push_todas_vagas(em_dest.strip().lower(), st.session_state.usuario_ativo, vagas_push)
            registrar_log("Inscrição de E-mail", f"E-mail: {em_dest}")
            st.sidebar.success("Push enviado!")

# --- CONSULTA DAS VAGAS ---
with Session(engine) as session:
    q = select(Job)
    if filtro_estado != "Todos os Estados":
        q = q.where(Job.state == filtro_estado[:2])
    if filtro_categoria != "Todas":
        q = q.where(Job.category == filtro_categoria)
    if filtro_portal != "Todos os Portais":
        q = q.where(Job.source == filtro_portal)
    vagas_brutas = session.exec(q).all()

    def chave_ordenacao(j):
        dt_limpa = sanitizar_datetime(getattr(j, "created_at", None))
        return (calcular_peso_proximidade(j), dt_limpa)

    vagas_lista = sorted(vagas_brutas, key=chave_ordenacao, reverse=True)
    todas_vagas_ativas = session.exec(select(Job)).all()
    total_candidatadas = len(session.exec(select(Job).where(Job.status == "Candidatada")).all())
    porcentagem_conquistada = min(int((total_candidatadas / max(len(todas_vagas_ativas), 1)) * 100), 100)

    perfil_user = session.exec(select(UserProfile)).first()
    is_biomed = perfil_user.is_biomed_graduated if perfil_user else False
    is_nursing = getattr(perfil_user, "is_nursing_graduated", True) if perfil_user else True
    nursing_cat = getattr(perfil_user, "nursing_category", "Enfermeira Bacharel") if perfil_user else "Enfermeira Bacharel"
    curriculo_armazenado = getattr(perfil_user, "resume_raw_text", "") or ""
    linkedin_url_armazenada = getattr(perfil_user, "linkedin_url", "") or ""
    headline_armazenada = getattr(perfil_user, "headline", "") or ""

vaga_recente = vagas_lista[0] if vagas_lista else None

# --- ESTRUTURA DE ABAS COMPLETAS ---
tab_vagas, tab_plantoes, tab_graficos, tab_enfermagem, tab_biomed, tab_agenda, tab_ia_curriculo, tab_trajeto, tab_feedback = st.tabs([
    "🌸 Mural Geral de Vagas",
    "🏥 Plantões em Enfermagem",
    "📊 Análise Gráfica do Mercado",
    "🩺 Especial Enfermagem & COREN",
    "🔬 Especial Biomedicina & Mercado",
    "📅 Agenda Médica & SUS",
    "💼 LinkedIn, Currículos & IA",
    "🗺️ Trajeto, Uber & Plantão",
    "📝 Diário de Uso & Sessão"
])

# ================= TAB 1: MURAL DE VAGAS =================
with tab_vagas:
    msg_nivel = "Iniciante Determinada 🌱" if porcentagem_conquistada < 20 else ("Profissional em Ascensão 🌟" if porcentagem_conquistada < 60 else "Candidata Imparável 👑")
    st.markdown(f"""
    <div class="content-box" style="border-left: 6px solid #E91E63;">
        <div style="display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:8px;">
            <b style="color:#C2185B; font-size:1.05rem;">🎮 Mini-Game de Carreiras: Nível {msg_nivel}</b>
            <span style="background:#FFE0E9; color:#AD1457; font-weight:800; font-size:0.85rem; padding:3px 12px; border-radius:12px;">
                🎯 {total_candidatadas} Vagas Salvas ({porcentagem_conquistada}% Conquistado)
            </span>
        </div>
        <p style="color:#4A1525; font-size:0.92rem; margin:8px 0 0 0; font-weight:600;">
            💖 <b>Não desanime!</b> Cada candidatura enviada e cada vaga salva é um passo a mais rumo à sua conquista! 💕
        </p>
    </div>
    """, unsafe_allow_html=True)
    st.progress(porcentagem_conquistada / 100.0)

    if vaga_recente:
        dt_v = sanitizar_datetime(getattr(vaga_recente, "created_at", None))
        minutos = max(int((datetime.utcnow() - dt_v).total_seconds() / 60), 1)
        st.markdown(f"""
        <div class="content-box" style="border-left:6px solid #FF69B4; background: linear-gradient(135deg, #FFFFFF, #FFF0F5);">
            <b style="color:#C2185B;">🎀 Alerta da Hello Kitty: Nova oportunidade publicada há {minutos} min!</b><br>
            <span style="color:#33101E; font-size:0.92rem;">
                Vaga de <b>{vaga_recente.title}</b> no <b>{vaga_recente.hospital_or_company}</b> ({vaga_recente.location}). Está esperando por você! ✨
            </span>
        </div>
        """, unsafe_allow_html=True)

    st.markdown(f"### 🩺 Oportunidades Próximas de Você ({len(vagas_lista)} disponíveis):")
    for v in vagas_lista:
        peso_prox = calcular_peso_proximidade(v)
        badge_prox = '<span class="badge-proxima">🏠 Bem Pertinho de Casa</span>' if peso_prox >= 80 else ''
        dt_c = sanitizar_datetime(getattr(v, "created_at", None))
        dt_fmt = dt_c.strftime("%d/%m/%Y às %H:%M")
        
        link_vaga = v.url_apply if v.url_apply.startswith("http") else f"https://{v.url_apply}"
        txt_zap = urllib.parse.quote(f"Olha essa oportunidade de {v.title} no {v.hospital_or_company}: {link_vaga}")
        link_zap = f"https://api.whatsapp.com/send?text={txt_zap}"

        badge_categoria = f'<span class="badge-enf">🩺 {v.category}</span>' if v.category == "Enfermagem" else f'<span class="badge">🔬 {v.category}</span>'

        st.markdown(f"""
        <div class="job-card">
            <div class="job-title">💖 {v.title}</div>
            <div style="color:#880E4F; font-size:0.95rem; margin-bottom:8px;">
                🏥 <b>{v.hospital_or_company}</b> &nbsp;•&nbsp; 📍 {v.location}
            </div>
            <div style="margin-bottom:10px;">
                {badge_prox} {badge_categoria}
                <span class="badge">📍 {v.state}</span>
                <span class="badge">🌐 {v.source}</span>
                <span class="badge-data">📅 {dt_fmt}</span>
            </div>
            <p style="color:#333333; font-size:0.92rem; line-height:1.4;">{v.description}</p>
            <div style="margin-top:10px;">
                <a href="{link_vaga}" target="_blank" class="action-link" style="background:#FF69B4; color:white !important;">Acessar no {v.source} ➔</a>
                <a href="{link_zap}" target="_blank" class="action-link" style="background:#E8F5E9; color:#2E7D32 !important; border:1px solid #C8E6C9;">💬 Compartilhar Vaga ➔</a>
            </div>
        </div>
        """, unsafe_allow_html=True)

        c1, c2 = st.columns([1, 4])
        with c1:
            if st.button("❤️ Salvar Vaga ➔", key=f"btn_fav_{v.id}"):
                with Session(engine) as s:
                    obj = s.get(Job, v.id)
                    obj.status = "Candidatada"
                    s.add(obj)
                    s.commit()
                registrar_log("Candidatura Salva", f"Vaga: {v.title} - {v.hospital_or_company}")
                st.success("Salva!")
                st.rerun()

# ================= TAB 2: PLANTÕES EM ENFERMAGEM =================
with tab_plantoes:
    st.markdown("<h2 style='color:#C2185B;'>🏥 Plantões em Enfermagem, Home Care & Coberturas</h2>", unsafe_allow_html=True)
    st.markdown("Oportunidades imediatas de plantões avulsos, escalas 12x36 e atendimentos domiciliares particulares com valores por diária. 💕")

    col_flt_p1, col_flt_p2 = st.columns([2, 1])
    with col_flt_p1:
        st.info("💡 **Dica do Thiago:** Plantões de Home Care e atendimentos particulares de curativos/medicação são excelentes para compor renda extra imediata e ganhar experiência prática assistencial!")
    with col_flt_p2:
        filtro_escala_pl = st.selectbox("Filtrar por Horário:", ["Todos os Horários", "Noturno (19h às 07h)", "Diurno (07h às 19h)", "Horário a combinar"])

    st.markdown("---")

    for idx_pl, pl in enumerate(PLANTOES_ENFERMAGEM):
        if filtro_escala_pl != "Todos os Horários" and filtro_escala_pl not in pl["escala"]:
            continue

        texto_apresentacao_plantao = (
            f"Olá! Tudo bem?\n\n"
            f"Vi a oportunidade de plantão para '{pl['tipo']}' em {pl['local']}.\n"
            f"Sou profissional de Enfermagem com registro ativo no COREN, experiência assistencial humanizada e total dedicação com segurança do paciente.\n"
            f"Tenho disponibilidade imediata para a escala de {pl['escala']}.\n\n"
            f"Podemos alinhar os detalhes do atendimento?"
        )
        link_zap_plantao = f"https://api.whatsapp.com/send?text={urllib.parse.quote(texto_apresentacao_plantao)}"

        st.markdown(f"""
        <div class="content-box" style="border-left: 6px solid #00ACC1; margin-bottom: 16px;">
            <div style="display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:8px;">
                <b style="color:#C2185B; font-size:1.15rem;">🩺 {pl['tipo']}</b>
                <span class="badge-plantao">💰 {pl['valor']}</span>
            </div>
            <div style="margin: 6px 0 10px 0;">
                <span class="badge-proxima">📍 {pl['local']}</span>
                <span class="badge">⏰ {pl['escala']}</span>
                <span class="badge">🎓 {pl['requisito']}</span>
            </div>
            <p style="color:#333; font-size:0.92rem; margin: 4px 0;"><b>Paciente / Perfil:</b> {pl['paciente']}</p>
            <p style="color:#555; font-size:0.9rem; line-height:1.4; margin: 4px 0 12px 0;"><b>Atividades:</b> {pl['atividades']}</p>
            <div style="margin-top: 8px;">
                <a href="{link_zap_plantao}" target="_blank" class="action-link" style="background:#E0F7FA; color:#006064 !important; border:1px solid #B2EBF2;">
                    💬 Enviar Mensagem de Apresentação no WhatsApp ➔
                </a>
                <a href="https://www.google.com/maps/search/{urllib.parse.quote(pl['local'])}" target="_blank" class="action-link">
                    🗺️ Ver Bairro no Maps ➔
                </a>
            </div>
        </div>
        """, unsafe_allow_html=True)

# ================= TAB 3: ANÁLISE GRÁFICA DO MERCADO =================
with tab_graficos:
    st.markdown("<h2 style='color:#AD1457;'>📊 Análise Gráfica: Vagas Anunciadas em Enfermagem & Biomedicina</h2>", unsafe_allow_html=True)
    st.markdown("Visão analítica da distribuição de vagas, concentração regional e especialidades mais procuradas pelos hospitais. 💕")

    # Compilação dos dados para os gráficos
    dados_vagas = []
    for j in todas_vagas_ativas:
        dados_vagas.append({
            "Profissão": j.category,
            "Estado": j.state,
            "Especialidade": j.specialty or "Geral",
            "Portal": j.source,
            "Turno": j.shift_type
        })
    df_mercado = pd.DataFrame(dados_vagas)

    if not df_mercado.empty:
        col_m1, col_m2, col_m3, col_m4 = st.columns(4)
        total_enf = len(df_mercado[df_mercado["Profissão"] == "Enfermagem"])
        total_bio = len(df_mercado[df_mercado["Profissão"] == "Biomedicina"])
        total_farma = len(df_mercado[df_mercado["Profissão"] == "Indústria Farmacêutica"])
        
        with col_m1:
            st.metric("🩺 Vagas Enfermagem", f"{total_enf} ativas", "+12% esta semana")
        with col_m2:
            st.metric("🔬 Vagas Biomedicina", f"{total_bio} ativas", "+8% esta semana")
        with col_m3:
            st.metric("💊 Vagas Farmacêuticas", f"{total_farma} ativas", "Estável")
        with col_m4:
            st.metric("🏥 Total Catalogado", f"{len(df_mercado)} vagas", "Nacional")

        st.markdown("---")

        c_g1, c_g2 = st.columns(2)
        with c_g1:
            st.markdown("#### 📈 Proporção de Vagas por Profissão")
            contagem_prof = df_mercado["Profissão"].value_counts()
            st.bar_chart(contagem_prof, color="#FF69B4")

        with c_g2:
            st.markdown("#### 🗺️ Vagas Anunciadas por Estado (Top Regiões)")
            contagem_estado = df_mercado["Estado"].value_counts()
            st.bar_chart(contagem_estado, color="#AD1457")

        st.markdown("---")

        c_g3, c_g4 = st.columns(2)
        with c_g3:
            st.markdown("#### 🩺 Top Especialidades Demandadas em Enfermagem")
            df_enf = df_mercado[df_mercado["Profissão"] == "Enfermagem"]
            if not df_enf.empty:
                st.bar_chart(df_enf["Especialidade"].value_counts(), color="#E91E63")
            else:
                st.info("Sem dados suficientes.")

        with c_g4:
            st.markdown("#### 🔬 Top Especialidades Demandadas em Biomedicina")
            df_bio = df_mercado[df_mercado["Profissão"] == "Biomedicina"]
            if not df_bio.empty:
                st.bar_chart(df_bio["Especialidade"].value_counts(), color="#6A1B9A")
            else:
                st.info("Sem dados suficientes.")
    else:
        st.info("Sincronize as vagas para carregar os gráficos.")

# ================= TAB 4: ESPECIAL ENFERMAGEM & COREN =================
with tab_enfermagem:
    st.markdown("<h2 style='color: #C2185B;'>🩺 Painel Estratégico de Enfermagem & COREN/Cofen</h2>", unsafe_allow_html=True)
    st.markdown("Direcionamento de carreira assistencial, especialidades de alta demanda, piso salarial e editais de concursos públicos. 💕")

    col_enf1, col_enf2 = st.columns([1, 1])
    with col_enf1:
        st.markdown("#### 🎓 Habilitação & Registro Profissional (COREN)")
        with st.form("form_enfermagem_status"):
            status_enf_form = st.radio(
                "Qual seu nível de formação atual em Enfermagem?",
                ["Enfermeira Bacharel (Graduação Concluída)", "Graduanda / Estudante de Enfermagem", "Técnica de Enfermagem com COREN Ativo"]
            )
            especialidade_desejada = st.selectbox(
                "Especialidade assistencial prioritária:",
                ["UTI Adulto & Cuidados Críticos", "Urgência & Emergência (Pronto Socorro)", "Centro Cirúrgico & CME", "Pediatria e UTI Neonatal", "Home Care & Cuidados Domiciliares"]
            )
            if st.form_submit_button("Salvar Perfil de Enfermagem ➔"):
                with Session(engine) as s:
                    p = s.exec(select(UserProfile)).first()
                    bacharel = "Bacharel" in status_enf_form
                    if not p:
                        p = UserProfile(full_name=st.session_state.usuario_ativo, is_nursing_graduated=bacharel, nursing_category=status_enf_form, headline=especialidade_desejada)
                    else:
                        p.is_nursing_graduated = bacharel
                        p.nursing_category = status_enf_form
                    s.add(p)
                    s.commit()
                registrar_log("Perfil Enfermagem", f"Nível: {status_enf_form} | Especialidade: {especialidade_desejada}")
                st.success("Perfil de Enfermagem atualizado com sucesso!")
                st.rerun()

    with col_enf2:
        st.markdown("#### 🗺️ Guia de Transição e Início de Carreira Assistencial")
        st.info("""
        🩺 **Rotas Estratégicas para Se Destacar no Plantão:**
        • **UTI e Sala Vermelha:** Domínio de drogas vasoativas (noradrenalina, dobutamina), cálculo de gotejamento, gasometria arterial e intubação orotraqueal.
        • **Classificação de Risco (Manchester):** Altamente exigido em pronto-atendimentos privados e públicos.
        • **Home Care Particular:** Excelente fonte de renda imediata enquanto aguarda escala hospitalar fixa, com diárias entre R$ 180 e R$ 340.
        • **Segurança do Paciente:** Práticas baseadas em evidências e prevenção de lesão por pressão (LPP).
        """)

    st.markdown("---")
    st.markdown("### 📰 Notícias Oficiais do Cofen, COREN-PA, SP e Editais de Concurso")
    noticias_enf_reais = [
        {"titulo": "Cofen aprova novas diretrizes para atuação da enfermagem em terapia intensiva e suporte ventilatório", "data": "23/09/2026", "link": "https://www.cofen.gov.br"},
        {"titulo": "COREN-PA abre plantão de dúvidas e fiscalização sobre escalas e dimensionamento de pessoal em Belém", "data": "20/09/2026", "link": "https://www.corenpa.org.br"},
        {"titulo": "Concursos EBSERH e Forças Armadas: Aberto cronograma para Enfermagem com vagas em hospitais universitários", "data": "16/09/2026", "link": "https://www.gov.br/ebserh"}
    ]
    for n in noticias_enf_reais:
        st.markdown(f"""
        <div class="content-box" style="padding:12px 18px; margin-bottom:10px; border-left: 6px solid #C2185B;">
            <b style="color:#C2185B;">{n['titulo']}</b><br>
            <small style="color:#666;">📅 {n['data']} &nbsp;|&nbsp; Conselho Federal de Enfermagem</small><br>
            <a href="{n['link']}" target="_blank" style="color:#E91E63; font-size:0.85rem; font-weight:bold; text-decoration:none;">Acessar comunicado oficial ➔</a>
        </div>
        """, unsafe_allow_html=True)

# ================= TAB 5: ESPECIAL BIOMEDICINA & MERCADO =================
with tab_biomed:
    st.markdown("<h2 style='color: #AD1457;'>🔬 Painel Estratégico de Biomedicina & Mercado</h2>", unsafe_allow_html=True)
    st.markdown("Guia de posicionamento profissional, conselho de classe (CRBM/CFBM) e farmacêuticas que contratam recém-formadas. 💕")

    col_bio1, col_bio2 = st.columns([1, 1])
    with col_bio1:
        st.markdown("#### 🎓 Status de Formação & Habilitação")
        with st.form("form_biomed_status"):
            status_grad = st.radio(
                "Você já concluiu a graduação em Biomedicina?",
                ["Sim, possuo diploma e registro ativo no CRBM", "Não, estou cursando / em fase de conclusão"],
                index=0 if is_biomed else 1
            )
            area_foco = st.selectbox(
                "Área de interesse prioritária:",
                ["Análises Clínicas & Diagnóstico Laboratorial", "Biologia Molecular & Genética", "Pesquisa Clínica & Farmacovigilância", "Biomedicina Estética", "Citopatologia"]
            )
            if st.form_submit_button("Confirmar Perfil Profissional ➔"):
                with Session(engine) as s:
                    p = s.exec(select(UserProfile)).first()
                    concluido = "Sim" in status_grad
                    if not p:
                        p = UserProfile(full_name=st.session_state.usuario_ativo, is_biomed_graduated=concluido, headline=area_foco)
                    else:
                        p.is_biomed_graduated = concluido
                    s.add(p)
                    s.commit()
                registrar_log("Atualizou Perfil Biomedicina", f"Graduada: {concluido} | Foco: {area_foco}")
                st.success("Perfil atualizado com sucesso!")
                st.rerun()

    with col_bio2:
        st.markdown("#### 🗺️ Qual caminho traçar no início de carreira?")
        if not is_biomed:
            st.info("""
            🌱 **Estratégia para quem está cursando:**
            • Priorize vagas de **Auxiliar Técnico de Coleta**, **Estágio em Análises Clínicas** e **Triagem**.
            • Laboratórios como *Ruth Brazão*, *Beneficente* e redes de hospital contratam graduandos para bancada inicial.
            • Desenvolva prática com punção venosa e calibração de aparelhos automatizados.
            """)
        else:
            st.success("""
            ✨ **Estratégia para Biomédica Formada (CRBM Ativo):**
            • Habilitação plena para **Responsabilidade Técnica (RT)** e emissão/assinatura de laudos diagnósticos.
            • Setores com maior demanda e valorização: **Biologia Molecular (PCR/NGS)** e **Microbiologia Automatizada**.
            • Concursos públicos municipais e federais (EBSERH) abrem vagas frequentes com estabilidade e piso salarial regular.
            """)

    st.markdown("---")
    st.markdown("### 💊 Farmacêuticas com Programas para Biomédicas Recém-Formadas")
    col_far1, col_far2, col_far3 = st.columns(3)
    with col_far1:
        st.markdown("""
        <div class="content-box">
            <h4 style="color:#C2185B; margin:0 0 6px 0;">Eurofarma Laboratórios</h4>
            <p style="font-size:0.88rem; color:#444;">
                <b>Áreas:</b> Farmacovigilância, Controle de Qualidade e Pesquisa Clínica.<br>
                <b>Perfil:</b> Aceita recém-formadas em programas de trainee e analista júnior.
            </p>
            <a href="https://eurofarma.gupy.io" target="_blank" class="action-link">Vagas Eurofarma ➔</a>
        </div>
        """, unsafe_allow_html=True)
    with col_far2:
        st.markdown("""
        <div class="content-box">
            <h4 style="color:#C2185B; margin:0 0 6px 0;">EMS Farmacêutica</h4>
            <p style="font-size:0.88rem; color:#444;">
                <b>Áreas:</b> Suporte a Ensaios Clínicos e Garantia da Qualidade.<br>
                <b>Perfil:</b> Forte contratação para cargos híbridos em SP e atuação nacional.
            </p>
            <a href="https://ems.gupy.io" target="_blank" class="action-link">Vagas EMS ➔</a>
        </div>
        """, unsafe_allow_html=True)
    with col_far3:
        st.markdown("""
        <div class="content-box">
            <h4 style="color:#C2185B; margin:0 0 6px 0;">Hypera Pharma</h4>
            <p style="font-size:0.88rem; color:#444;">
                <b>Áreas:</b> Assuntos Regulatórios, Pesquisa e Desenvolvimento.<br>
                <b>Perfil:</b> Portas de entrada estruturadas para novos graduados da saúde.
            </p>
            <a href="https://hyperapharma.gupy.io" target="_blank" class="action-link">Vagas Hypera ➔</a>
        </div>
        """, unsafe_allow_html=True)

# ================= TAB 6: AGENDA MÉDICA, EXAMES & SUS =================
with tab_agenda:
    st.markdown("<h2 style='color: #AD1457;'>📅 Agenda Médica, Exames, SUS & Saúde Ocupacional</h2>", unsafe_allow_html=True)
    st.markdown("Acompanhe seus exames, consultas, imunização para plantões e orçamentos laboratoriais com Google Maps integrado. 💕")

    col_ag1, col_ag2 = st.columns([1, 1])
    with col_ag1:
        st.markdown("#### ➕ Agendar Exame, Consulta ou Saúde Ocupacional")
        with st.form("form_novo_exame_agenda"):
            tipo_ag = st.selectbox("Tipo:", ["Exame Laboratorial", "Consulta Médica", "Exame Ocupacional (Admissional/Periódico)", "Vacinação do Plantonista (Hepatite/Tétano)", "Retorno Clínico"])
            titulo_ag = st.text_input("Nome do Procedimento:", placeholder="Ex: Hemograma, Sorologia Hepatite B, Toxicológico...")
            local_ag = st.text_input("Laboratório, UBS ou Clínica:", placeholder="Ex: Laboratório Beneficente, Ruth Brazão, UBS Nazaré...")
            col_v, col_d, col_h = st.columns(3)
            with col_v:
                preco_ag = st.number_input("Valor Estimado (R$):", min_value=0.0, step=10.0, format="%.2f")
            with col_d:
                data_ag = st.date_input("Data:", value=date.today())
            with col_h:
                hora_ag = st.time_input("Horário:", value=datetime.now().time())
            notas_ag = st.text_area("Anotações / Instruções:", placeholder="Ex: Jejum de 8h, levar carteira do COREN/CRBM...")
            status_saude = st.selectbox("Status de Saúde Atual:", ["🟢 Me sinto ótima e disposta", "🟡 Cansada / Precisando de repouso", "🔴 Com dor ou sintomas a investigar"])

            if st.form_submit_button("Salvar na Agenda ➔"):
                if titulo_ag.strip():
                    with Session(engine) as s:
                        item = MedicalAppointment(
                            title=titulo_ag.strip(),
                            appointment_type=tipo_ag,
                            location=local_ag.strip() or "A definir",
                            estimated_price=preco_ag,
                            scheduled_date=data_ag.strftime("%Y-%m-%d"),
                            scheduled_time=hora_ag.strftime("%H:%M"),
                            notes=f"{notas_ag.strip()} | Saúde: {status_saude}",
                            is_completed=False
                        )
                        s.add(item)
                        s.commit()
                    registrar_log("Agendou Exame/Consulta", f"{tipo_ag}: {titulo_ag}")
                    st.success("Salvo na agenda médica com sucesso!")
                    st.rerun()

    with col_ag2:
        st.markdown("#### 📋 Histórico de Exames & Procedimentos")
        with Session(engine) as s:
            todos_exames = s.exec(select(MedicalAppointment).order_by(MedicalAppointment.scheduled_date.desc())).all()
            if todos_exames:
                for ex in todos_exames:
                    st_txt = "✅ Feito" if ex.is_completed else "⏰ Agendado"
                    st.markdown(f"""
                    <div class="content-box" style="padding:10px 14px; margin-bottom:8px;">
                        <b>{ex.title}</b> ({ex.appointment_type}) - <span style="color:#C2185B;">{st_txt}</span><br>
                        <small>📅 {ex.scheduled_date} às {ex.scheduled_time} | 📍 {ex.location} | R$ {ex.estimated_price:.2f}</small><br>
                        <small><i>{ex.notes}</i></small>
                    </div>
                    """, unsafe_allow_html=True)
            else:
                st.info("Nenhum exame cadastrado no momento.")

    st.markdown("---")
    st.markdown("### 🏥 Laboratórios para Orçamento de Exames em Todo o Brasil (com Maps Integrado)")
    col_lab1, col_lab2, col_lab3 = st.columns(3)
    with col_lab1:
        st.markdown("""
        <div class="content-box">
            <h4 style="color:#C2185B; margin:0 0 4px 0;">🌴 Belém / Pará</h4>
            <p style="font-size:0.86rem; color:#444;">
                • <b>Laboratório Beneficente:</b> Nazaré e Marco<br>
                • <b>Laboratório Ruth Brazão:</b> Batista Campos<br>
                • <b>Laboratório Paulo Azevedo:</b> Umarizal
            </p>
            <a href="https://www.google.com/maps/search/laboratorios+analises+clinicas+belem" target="_blank" class="action-link">Abrir no Google Maps ➔</a>
        </div>
        """, unsafe_allow_html=True)
    with col_lab2:
        st.markdown("""
        <div class="content-box">
            <h4 style="color:#C2185B; margin:0 0 4px 0;">🏙️ São Paulo / SP</h4>
            <p style="font-size:0.86rem; color:#444;">
                • <b>Grupo Fleury:</b> Morumbi, Mooca e Paulista<br>
                • <b>Lavoisier / Delboni (Dasa):</b> Unidades em toda a Capital<br>
                • <b>Salomão Zoppi:</b> Jardins e Itaim
            </p>
            <a href="https://www.google.com/maps/search/laboratorios+analises+clinicas+sao+paulo" target="_blank" class="action-link">Abrir no Google Maps ➔</a>
        </div>
        """, unsafe_allow_html=True)
    with col_lab3:
        st.markdown("""
        <div class="content-box">
            <h4 style="color:#C2185B; margin:0 0 4px 0;">🌊 Rio de Janeiro / RJ</h4>
            <p style="font-size:0.86rem; color:#444;">
                • <b>Sérgio Franco (Dasa):</b> Tijuca e Copacabana<br>
                • <b>Lâmina Medicina Diagnóstica:</b> Botafogo e Barra<br>
                • <b>Richet Medicina & Diagnóstico:</b> Barra da Tijuca
            </p>
            <a href="https://www.google.com/maps/search/laboratorios+analises+clinicas+rio+de+janeiro" target="_blank" class="action-link">Abrir no Google Maps ➔</a>
        </div>
        """, unsafe_allow_html=True)

# ================= TAB 7: LINKEDIN, CURRÍCULOS & IA =================
with tab_ia_curriculo:
    st.markdown("<h2 style='color: #0077B5;'>💼 LinkedIn, Modelos de Documentos & Análise com Gemini</h2>", unsafe_allow_html=True)
    
    st.markdown("#### 🔗 Conexão com Perfil do LinkedIn")
    with st.form("form_linkedin_conexao"):
        c_lk1, c_lk2 = st.columns(2)
        with c_lk1:
            lk_url = st.text_input("Link do Perfil do LinkedIn:", value=linkedin_url_armazenada, placeholder="https://www.linkedin.com/in/seu-nome")
        with c_lk2:
            lk_head = st.text_input("Título Profissional:", value=headline_armazenada, placeholder="Ex: Enfermeira Assistencial | UTI Adulto | COREN Ativo")
        if st.form_submit_button("Sincronizar Perfil com o Portal ➔"):
            with Session(engine) as s:
                p = s.exec(select(UserProfile)).first()
                if not p:
                    p = UserProfile(full_name=st.session_state.usuario_ativo, linkedin_url=lk_url.strip(), headline=lk_head.strip())
                else:
                    p.linkedin_url = lk_url.strip()
                    p.headline = lk_head.strip()
                s.add(p)
                s.commit()
            registrar_log("Conexão LinkedIn", f"Link: {lk_url}")
            st.success("LinkedIn sincronizado!")
            st.rerun()

    st.markdown("---")
    st.markdown("### 📄 Modelos Sugestivos de Currículo & Carta de Apresentação")
    
    tab_mod_enf, tab_mod_bio = st.tabs(["🩺 Modelos para Enfermagem", "🔬 Modelos para Biomedicina"])
    
    with tab_mod_enf:
        curriculo_enf_txt = """OBJETIVO:
Enfermeira Assistencial - UTI Adulto / Urgência e Emergência

RESUMO DE QUALIFICAÇÕES:
• Experiência no cuidado assistencial direto a pacientes graves em terapia intensiva e pronto atendimento.
• Administração segura de medicamentos, cálculo e infusão de drogas vasoativas e passagem de sondas (SNE, SNG, SVD).
• Vivência prática na aplicação do Protocolo de Manchester e estabilização de urgências.
• Registro ativo e regular junto ao COREN.

FORMAÇÃO:
• Bacharelado em Enfermagem."""

        carta_enf_txt = """Prezada Equipe de Recursos Humanos,

Apresento minha candidatura à oportunidade assistencial em enfermagem.

Possuo sólida experiência no atendimento humanizado a pacientes críticos, com estrito cumprimento das boas práticas assistenciais e prevenção de infecção hospitalar. Estou à disposição para entrevista e início imediato de plantões.

Atenciosamente,
Enfermeira | COREN Ativo"""

        c_e1, c_e2 = st.columns(2)
        with c_e1:
            st.markdown("<b>Modelo de Currículo (Enfermagem):</b>", unsafe_allow_html=True)
            st.markdown(f'<div class="doc-display-box">{curriculo_enf_txt}</div>', unsafe_allow_html=True)
            st.download_button("📥 Baixar Currículo Enfermagem (.txt) ➔", curriculo_enf_txt, "Curriculo_Enfermagem.txt")
        with c_e2:
            st.markdown("<b>Carta de Apresentação (Enfermagem):</b>", unsafe_allow_html=True)
            st.markdown(f'<div class="doc-display-box">{carta_enf_txt}</div>', unsafe_allow_html=True)
            st.download_button("📥 Baixar Carta Enfermagem (.txt) ➔", carta_enf_txt, "Carta_Enfermagem.txt")

    with tab_mod_bio:
        curriculo_bio_txt = """OBJETIVO:
Biomédica Analista - Análises Clínicas / Biologia Molecular

RESUMO DE QUALIFICAÇÕES:
• Proficiência em rotinas de bancada (Hematologia, Bioquímica, Imunologia e Microbiologia).
• Operação e calibração de analisadores automatizados e controle de qualidade laboratorial (CQI/CQE).
• Conhecimento em pipetagem, centrifugação e leitura microscópica de lâminas.
• Registro ativo junto ao CRBM.

FORMAÇÃO:
• Bacharelado em Biomedicina."""

        carta_bio_txt = """Prezada Coordenação Laboratorial,

Venho apresentar meu currículo para a vaga na área de análises clínicas e diagnóstico laboratorial.

Tenho total compromisso com o rigor metodológico pré-analítico, analítico e pós-analítico para a liberação de laudos confiáveis. Coloco-me à inteira disposição para avaliação técnica.

Atenciosamente,
Biomédica | CRBM Ativo"""

        c_b1, c_b2 = st.columns(2)
        with c_b1:
            st.markdown("<b>Modelo de Currículo (Biomedicina):</b>", unsafe_allow_html=True)
            st.markdown(f'<div class="doc-display-box">{curriculo_bio_txt}</div>', unsafe_allow_html=True)
            st.download_button("📥 Baixar Currículo Biomedicina (.txt) ➔", curriculo_bio_txt, "Curriculo_Biomedicina.txt")
        with c_b2:
            st.markdown("<b>Carta de Apresentação (Biomedicina):</b>", unsafe_allow_html=True)
            st.markdown(f'<div class="doc-display-box">{carta_bio_txt}</div>', unsafe_allow_html=True)
            st.download_button("📥 Baixar Carta Biomedicina (.txt) ➔", carta_bio_txt, "Carta_Biomedicina.txt")

    st.markdown("---")
    st.markdown("#### 🤖 Análise de Compatibilidade do Currículo em PDF com IA")
    up_pdf = st.file_uploader("Envie o currículo dela em PDF para diagnóstico instantâneo:", type=["pdf"])
    if up_pdf is not None:
        try:
            reader = PdfReader(up_pdf)
            texto_pdf = "\n".join([page.extract_text() for page in reader.pages if page.extract_text()])
            if texto_pdf:
                with Session(engine) as s:
                    p = s.exec(select(UserProfile)).first()
                    if not p:
                        p = UserProfile(full_name=st.session_state.usuario_ativo, resume_raw_text=texto_pdf)
                    else:
                        p.resume_raw_text = texto_pdf
                    s.add(p)
                    s.commit()
                registrar_log("Upload Currículo PDF", f"Páginas: {len(reader.pages)}")
                st.success("✅ Currículo carregado! Agora a IA usará este texto para calcular a afinidade com as vagas de enfermagem e biomedicina.")
                st.markdown(f'<div class="doc-display-box">{texto_pdf[:1500]}...</div>', unsafe_allow_html=True)
        except Exception as e:
            st.error(f"Erro ao ler PDF: {e}")

# ================= TAB 8: TRAJETO, UBER & PLANTÃO =================
with tab_trajeto:
    st.markdown("<h2 style='color: #AD1457;'>🗺️ Trajeto, Uber, Custos & Cuidados com Você 💕</h2>", unsafe_allow_html=True)

    cidade_selecionada = st.selectbox("Selecione a Região do Plantão:", ["Belém - PA", "São Paulo - SP", "Rio de Janeiro - RJ"])
    dados_tempo = obter_previsao_tempo_detalhada(cidade_selecionada)
    temp_atual = dados_tempo["temp"]
    prob_chuva = dados_tempo["prob_chuva"]

    if prob_chuva >= 40:
        dica_clima = "🌧️ **Previsão de chuva:** Leve guarda-chuva, capa de chuva e uma meia reserva para o plantão!"
        roupa_sugerida = "Calçado impermeável fechado e jaleco de manga longa térmico leve."
    elif temp_atual >= 30:
        dica_clima = "☀️ **Dia quente e abafado:** Leve garrafinha de água gelada (500ml) e protetor solar!"
        roupa_sugerida = "Scrub/privativo de tecido respirável e peças leves de algodão."
    else:
        dica_clima = "⛅ **Tempo ameno e estável:** Trajeto tranquilo para os estudos ou plantão."
        roupa_sugerida = "Privativo padrão com casaco confortável para o ar-condicionado do hospital/laboratório."

    st.markdown(f"""
    <div class="content-box" style="border-left:6px solid #FF69B4;">
        <div style="display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap;">
            <b style="color:#C2185B; font-size:1.05rem;">🌤️ Clima em Tempo Real ({cidade_selecionada}): {temp_atual}°C</b>
            <span style="color:#AD1457; font-weight:bold;">Probabilidade de Chuva: {prob_chuva}%</span>
        </div>
        <p style="margin:8px 0 4px 0; color:#333;">{dica_clima}</p>
        <p style="margin:0; color:#555; font-size:0.9rem;">🎒 <b>Dica de Vestuário:</b> {roupa_sugerida}</p>
    </div>
    """, unsafe_allow_html=True)

    st.markdown("---")
    st.markdown("### 🚌 Painel de Custos de Passagens & Estimativa Semanal/Mensal (Plantão 12x36)")
    
    tabela_tarifas = {
        "Belém - PA": {"onibus": 4.60, "uber_medio": 22.00},
        "São Paulo - SP": {"onibus": 5.30, "uber_medio": 32.00},
        "Rio de Janeiro - RJ": {"onibus": 5.00, "uber_medio": 28.00}
    }
    tarifa_base = tabela_tarifas[cidade_selecionada]["onibus"]
    uber_base = tabela_tarifas[cidade_selecionada]["uber_medio"]

    col_cs1, col_cs2, col_cs3 = st.columns(3)
    with col_cs1:
        st.markdown(f"""
        <div class="content-box" style="text-align:center;">
            <small style="color:#666;">Tarifa Municipal</small>
            <h3 style="color:#C2185B; margin:4px 0;">R$ {tarifa_base:.2f}</h3>
            <small>Ida e Volta: R$ {tarifa_base*2:.2f}/dia</small>
        </div>
        """, unsafe_allow_html=True)
    with col_cs2:
        gasto_semanal_12x36 = (tarifa_base * 2) * 3
        st.markdown(f"""
        <div class="content-box" style="text-align:center;">
            <small style="color:#666;">Escala 12x36 Semanal (3 plantões)</small>
            <h3 style="color:#00695C; margin:4px 0;">R$ {gasto_semanal_12x36:.2f}</h3>
            <small>Rotina Comercial (5 dias): R$ {(tarifa_base*2)*5:.2f}</small>
        </div>
        """, unsafe_allow_html=True)
    with col_cs3:
        gasto_mensal_12x36 = (tarifa_base * 2) * 15
        st.markdown(f"""
        <div class="content-box" style="text-align:center;">
            <small style="color:#666;">Escala 12x36 Mensal (15 plantões)</small>
            <h3 style="color:#E65100; margin:4px 0;">R$ {gasto_mensal_12x36:.2f}</h3>
            <small>Rotina Comercial (22 dias): R$ {(tarifa_base*2)*22:.2f}</small>
        </div>
        """, unsafe_allow_html=True)

    st.markdown("---")
    st.markdown("#### 🚗 Simulação de Rota & Chamada de Uber")
    c_dest1, c_dest2 = st.columns([3, 1])
    with c_dest1:
        end_dest = st.text_input("Endereço do Hospital, Laboratório ou Faculdade:", placeholder="Ex: Hospital Porto Dias, Av. Almirante Barroso, Belém")
    with c_dest2:
        st.markdown("<div style='height:28px;'></div>", unsafe_allow_html=True)
        end_query = urllib.parse.quote(end_dest.strip() or "Hospital")
        link_uber = f"https://m.uber.com/ul/?action=setPickup&pickup=my_location&dropoff[formatted_address]={end_query}"
        link_maps = f"https://www.google.com/maps/dir/?api=1&destination={end_query}"

    c_btn_r1, c_btn_r2 = st.columns(2)
    with c_btn_r1:
        st.markdown(f'<a href="{link_maps}" target="_blank" class="action-link" style="background:#FF69B4; color:white !important; width:100%; text-align:center; padding:11px;">🗺️ Abrir Rota no Google Maps ➔</a>', unsafe_allow_html=True)
    with c_btn_r2:
        st.markdown(f'<a href="{link_uber}" target="_blank" class="action-link" style="background:#000000; color:white !important; width:100%; text-align:center; padding:11px;">🚗 Pedir Uber para o Destino (~R$ {uber_base:.2f}) ➔</a>', unsafe_allow_html=True)

    st.markdown("---")
    st.markdown("### 🚨 Botão de Segurança na Saída de Plantão & Telefones Úteis")
    msg_aviso_thiago = "Oi, amor! Estou saindo do plantão/estudos agora e já a caminho de casa. Te aviso assim que chegar! 💕"
    link_zap_seguranca = f"https://api.whatsapp.com/send?phone=5511913129697&text={urllib.parse.quote(msg_aviso_thiago)}"
    
    st.markdown(f"""
    <div style="text-align:center; margin-bottom:16px;">
        <a href="{link_zap_seguranca}" target="_blank" class="action-link" style="background: linear-gradient(135deg, #FF6584, #FF476F); color:white !important; font-size:1rem; padding:13px 26px;">
            🚨 Mandar Aviso de Saída de Plantão Direto p/ Thiago ➔
        </a>
    </div>
    """, unsafe_allow_html=True)

    col_em1, col_em2 = st.columns(2)
    with col_em1:
        st.markdown("""
        <div class="content-box">
            <h4 style="color:#C2185B; margin:0 0 6px 0;">🩺 Telefones de Plantão dos Conselhos</h4>
            <ul style="font-size:0.86rem; color:#333; line-height:1.6; margin:0; padding-left:18px;">
                <li><b>COREN-PA (Belém):</b> (91) 3262-6052</li>
                <li><b>COREN-SP (Capital):</b> (11) 3225-6300</li>
                <li><b>COREN-RJ (Capital):</b> (21) 3232-3232</li>
                <li><b>CRBM-4 (Norte / Belém):</b> (91) 3212-3850</li>
                <li><b>CRBM-1 (São Paulo):</b> (11) 3347-5555</li>
            </ul>
        </div>
        """, unsafe_allow_html=True)
    with col_em2:
        st.markdown("""
        <div class="content-box">
            <h4 style="color:#C2185B; margin:0 0 6px 0;">🚑 Centrais de Emergência Nacional</h4>
            <ul style="font-size:0.86rem; color:#333; line-height:1.6; margin:0; padding-left:18px;">
                <li><b>SAMU:</b> 192 (Urgência Médica)</li>
                <li><b>Polícia Militar:</b> 190 (Emergência Policial)</li>
                <li><b>Bombeiros:</b> 193 (Resgate)</li>
                <li><b>Central de Atendimento à Mulher:</b> 180</li>
            </ul>
        </div>
        """, unsafe_allow_html=True)

# ================= TAB 9: DIÁRIO DE USO & SESSÃO =================
with tab_feedback:
    st.markdown("<h2 style='color:#C2185B;'>📝 Diário de Uso, Mood & Registro de Interações</h2>", unsafe_allow_html=True)
    st.markdown("Registre como foi seu dia de plantão ou estudos, anote suas percepções e envie diretamente para o WhatsApp do Thiago! 💕")

    with st.form("form_diario_completo"):
        c_m1, c_m2 = st.columns(2)
        with c_m1:
            mood_registro = st.selectbox(
                "Como está seu humor e ânimo hoje?",
                [
                    "✨ Motivada e confiante nas oportunidades!",
                    "💖 Gostei muito das vagas que vi hoje!",
                    "🧸 Cansadinha do plantão/faculdade, mas em paz",
                    "🤔 Em dúvida entre enfermagem assistencial e laboratório",
                    "🥺 Precisando de um abraço e apoio do Thiago"
                ]
            )
            cand_hoje = st.checkbox("Conseguiu se candidatar ou salvar alguma vaga?", value=True)
        with c_m2:
            notas_fb = st.text_area("Anotações do seu dia / Impressões:", placeholder="Ex: Vi uma vaga boa no Porto Dias e outra no Beneficente. Quero focar em UTI...")
            sugest_fb = st.text_input("Algum pedido especial para o Thiago adicionar?", placeholder="Ex: Adicionar mais hospitais em Ananindeua...")

        if st.form_submit_button("Salvar no Diário & Gerar WhatsApp p/ Thiago ➔"):
            with Session(engine) as s:
                entry = FeedbackEntry(
                    user_mood=mood_registro,
                    notes=notas_fb.strip(),
                    found_good_job=cand_hoje,
                    suggestions=sugest_fb.strip()
                )
                s.add(entry)
                s.commit()
            registrar_log("Feedback Registrado", f"Mood: {mood_registro}")
            st.success("✅ Registro salvo com sucesso!")

            msg_zap_envio = (
                f"Oi, amor! Registrei meu feedback de hoje no portal:\n\n"
                f"• Meu Mood: {mood_registro}\n"
                f"• Conseguiu aplicar/salvar? {'Sim, me candidatei! 🎉' if cand_hoje else 'Ainda não, só olhei.'}\n"
                f"• Anotações: {notas_fb.strip() or 'Nenhuma'}\n"
                f"• Pedidos: {sugest_fb.strip() or 'Tudo ótimo!'}\n\n"
                f"Te amo muito! 💕"
            )
            link_zap_envio = f"https://api.whatsapp.com/send?phone=5511913129697&text={urllib.parse.quote(msg_zap_envio)}"
            st.markdown(f"""
            <div style="margin-top:10px;">
                <a href="{link_zap_envio}" target="_blank" class="action-link" style="background:#25D366; color:white !important; padding:10px 22px;">
                    📲 Enviar Esse Resumo Diretamente p/ WhatsApp do Thiago ➔
                </a>
            </div>
            """, unsafe_allow_html=True)

    st.markdown("---")
    st.markdown("#### 📜 Registro de Interações Salvas na Sessão:")
    with Session(engine) as s:
        logs_recentes = s.exec(select(UserSessionLog).order_by(UserSessionLog.timestamp.desc()).limit(6)).all()
        if logs_recentes:
            for l in logs_recentes:
                dt_l = l.timestamp.strftime("%d/%m/%Y às %H:%M")
                st.markdown(f"""
                <div class="content-box" style="padding:8px 14px; margin-bottom:6px;">
                    <b>{l.user_name}</b> realizou: <i>{l.action}</i> ({l.details}) <small style="color:#777;">às {dt_l}</small>
                </div>
                """, unsafe_allow_html=True)
        else:
            st.info("Nenhuma interação registrada ainda.")

# --- ASSINATURA ---
st.divider()
st.markdown("<div style='height: 40px;'></div>", unsafe_allow_html=True)
st.markdown("""
<div style="text-align: center; color: #AD1457; font-size: 1.05rem; font-weight: 800;">
    🐾 Desenvolvido com todo o amor por <b>Thiago Zuza</b> para o seu amor 💕 ✨
</div>
""", unsafe_allow_html=True)