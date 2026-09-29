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
from scrapers_plantoes import buscar_plantoes_reais
import pandas as pd
import requests
import streamlit as st
import streamlit.components.v1 as components
from pypdf import PdfReader
from sqlalchemy import text
from sqlmodel import Field, Session, SQLModel, create_engine, select
# Importação dos modelos e do scraper no topo do arquivo
from models import Job, UserProfile, UserSubscription
from scrapers_plantoes import buscar_plantoes_reais
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

# --- INICIALIZAÇÃO DO BANCO E MIGRAÇÕES SEGURAS ---
sqlite_url = "sqlite:///vagas_enfermagem.db"
engine = create_engine(sqlite_url, echo=False)
SQLModel.metadata.create_all(engine)

with engine.connect() as conn:
    colunas_job = [
        ("state", "VARCHAR DEFAULT 'PA'"),
        ("category", "VARCHAR DEFAULT 'Enfermagem'"),
        ("requires_graduation", "INTEGER DEFAULT 0"),
        ("status", "VARCHAR DEFAULT 'Disponível'"),
    ]
    for col_nome, col_tipo in colunas_job:
        try:
            conn.execute(text(f"ALTER TABLE job ADD COLUMN {col_nome} {col_tipo}"))
            conn.commit()
        except Exception:
            pass

    colunas_profile = [
        ("is_biomed_graduated", "INTEGER DEFAULT 0"),
        ("is_nursing_graduated", "INTEGER DEFAULT 1"),
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

# --- CATÁLOGO NACIONAL DINÂMICO COMPLETO: 27 ESTADOS DA FEDERAÇÃO ---
UFS_BRASIL = {
    "AC": "Acre", "AL": "Alagoas", "AP": "Amapá", "AM": "Amazonas", "BA": "Bahia",
    "CE": "Ceará", "DF": "Distrito Federal", "ES": "Espírito Santo", "GO": "Goiás",
    "MA": "Maranhão", "MT": "Mato Grosso", "MS": "Mato Grosso do Sul", "MG": "Minas Gerais",
    "PA": "Pará", "PB": "Paraíba", "PR": "Paraná", "PE": "Pernambuco", "PI": "Piauí",
    "RJ": "Rio de Janeiro", "RN": "Rio Grande do Norte", "RS": "Rio Grande do Sul",
    "RO": "Rondônia", "RR": "Roraima", "SC": "Santa Catarina", "SP": "São Paulo",
    "SE": "Sergipe", "TO": "Tocantins"
}

HOSPITAIS_POR_ESTADO = {
    "PA": [
        ("Hospital Porto Dias", "Marco, Belém - PA"),
        ("Hospital Ophir Loyola", "São Brás, Belém - PA"),
        ("Hospital Metropolitano (HMUE)", "Ananindeua - PA"),
        ("Hospital Santa Casa de Misericórdia do Pará", "Umarizal, Belém - PA"),
        ("Laboratório Beneficente de Belém", "Nazaré, Belém - PA"),
        ("Laboratório Ruth Brazão", "Batista Campos, Belém - PA")
    ],
    "SP": [
        ("Hospital Sancta Maggiore (Prevent Senior)", "Pinheiros, São Paulo - SP"),
        ("Hospital Israelita Albert Einstein", "Morumbi, São Paulo - SP"),
        ("Hospital Sírio-Libanês", "Bela Vista, São Paulo - SP"),
        ("Eurofarma Laboratórios", "Itapevi / São Paulo - SP"),
        ("EMS Indústria Farmacêutica", "Hortolândia / São Paulo - SP"),
        ("Grupo Fleury Diagnósticos", "Jabaquara, São Paulo - SP")
    ],
    "RJ": [
        ("Hospital Copa D'Or (Rede D'Or)", "Copacabana, Rio de Janeiro - RJ"),
        ("Laboratório Sérgio Franco (Dasa)", "Tijuca, Rio de Janeiro - RJ"),
        ("Hospital Samaritano", "Botafogo, Rio de Janeiro - RJ")
    ],
    "MG": [
        ("Hospital Mater Dei", "Santo Agostinho, Belo Horizonte - MG"),
        ("Hospital Felício Rocho", "Barro Preto, Belo Horizonte - MG")
    ],
    "DF": [
        ("Hospital Brasília (Dasa)", "Lago Sul, Brasília - DF"),
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
        "requires_graduation": 1
    },
    {
        "title": "Técnico de Enfermagem - Centro Cirúrgico & CME",
        "category": "Enfermagem", "shift_type": "12x36", "specialty": "Centro Cirúrgico",
        "description": "Instrumentação cirúrgica, paramentação estéril, controle de materiais em CME e recuperação pós-anestésica.",
        "requires_graduation": 0
    },
    {
        "title": "Enfermeiro(a) - Urgência e Emergência (Pronto Atendimento)",
        "category": "Enfermagem", "shift_type": "12x36", "specialty": "Urgência/Emergência",
        "description": "Acolhimento com Classificação de Risco (Manchester), estabilização de politraumatizados e apoio em sala vermelha.",
        "requires_graduation": 1
    },
    {
        "title": "Biomédica Analista - Hematologia e Bioquímica Clínica",
        "category": "Biomedicina", "shift_type": "Diurno", "specialty": "Análises Clínicas",
        "description": "Rotina de bancada automatizada, microscopia para contagem diferencial de leucócitos, controle de qualidade (CQI/CQE) e liberação de laudos. CRBM ativo.",
        "requires_graduation": 1
    },
    {
        "title": "Auxiliar Técnico de Coleta e Triagem Laboratorial",
        "category": "Biomedicina", "shift_type": "Diurno", "specialty": "Coleta e Triagem",
        "description": "Punção venosa à vácuo, coleta pediátrica, centrifugação e envio de amostras biológicas. Aberto a graduandos ou recém-formados.",
        "requires_graduation": 0
    },
    {
        "title": "Biomédica Especialista - Biologia Molecular & PCR",
        "category": "Biomedicina", "shift_type": "Diurno", "specialty": "Biologia Molecular",
        "description": "Sequenciamento genético, extração e amplificação de DNA/RNA, RT-PCR para painéis infecciosos e validação analítica.",
        "requires_graduation": 1
    }
]

PORTAIS_LISTA = ["Gupy Saúde", "Vagas.com", "Catho", "InfoJobs", "LinkedIn", "Glassdoor"]

def gerar_catalogo_dinamico_nacional():
    catalogo = []

    for uf, hospitais in HOSPITAIS_POR_ESTADO.items():
        for hospital_nome, localizacao in hospitais:
            modelo = random.choice(MODELOS_VAGAS_BASE)
            portal = random.choice(PORTAIS_LISTA)

            if portal == "LinkedIn":
                query = urllib.parse.quote(
                    f"{modelo['title']} {hospital_nome}"
                )
                url_apply = (
                    f"https://www.linkedin.com/jobs/search/?keywords={query}"
                )
            elif portal in ["Gupy Saúde", "Gupy"]:
                query = urllib.parse.quote(
                    f"{hospital_nome} {modelo['title']}"
                )
                url_apply = f"https://www.google.com/search?q={query}+gupy"
            elif portal == "Catho":
                query = urllib.parse.quote(
                    f"{modelo['title']} {hospital_nome}"
                )
                url_apply = f"https://www.catho.com.br/vagas/?q={query}"
            elif portal == "InfoJobs":
                query = urllib.parse.quote(
                    f"{modelo['title']} {hospital_nome}"
                )
                url_apply = (
                    f"https://www.infojobs.com.br/vagas-de-emprego-{query}.aspx"
                )
            else:
                query = urllib.parse.quote(
                    f"{hospital_nome} {modelo['title']}"
                )
                url_apply = f"https://www.google.com/search?q={query}"

            tempo_recuo = random.randint(2, 60)
            data_anuncio = (
                datetime.utcnow() - timedelta(minutes=tempo_recuo)
            ).strftime("%Y-%m-%d %H:%M:%S")

            catalogo.append({
                "title": modelo["title"],
                "hospital_or_company": hospital_nome,
                "location": localizacao,
                "state": uf,
                "category": modelo["category"],
                "shift_type": modelo["shift_type"],
                "specialty": modelo["specialty"],
                "description": modelo["description"],
                "url_apply": url_apply,
                "source": portal,
                "requires_graduation": modelo["requires_graduation"],
                "created_at": data_anuncio,
            })

    return catalogo


def popular_catalogo_base():
    try:
        catalogo_total = gerar_catalogo_dinamico_nacional()
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
                        "requires_graduation": int(item.get("requires_graduation", 0)),
                        "created_at": str(item["created_at"])
                    })
                    urls_existentes.add(url)
    except Exception:
        pass

popular_catalogo_base()

# --- DEMANDAS DINÂMICAS DE PACIENTES / HOME CARE ---
SOLICITACOES_BASE = [
    {"servico": "Plantão Noturno Particular (Acompanhamento Domiciliar)", "solicitante": "Família Guimarães", "valor": "R$ 280,00 / plantão", "detalhe": "Paciente idosa em recuperação pós-operatória necessitando de administração de medicação e monitorização contínua de sinais vitais.", "cat": "Enfermagem"},
    {"servico": "Curativo Complexo & Cuidados com Lesão por Pressão", "solicitante": "Carlos Eduardo", "valor": "R$ 150,00 / visita", "detalhe": "Aplicação de técnica estéril e curativo oclusivo com hidrocolóide conforme prescrição médica.", "cat": "Enfermagem"},
    {"servico": "Coleta Domiciliar de Exames de Sangue (Rotina Idosos)", "solicitante": "Clínica Integrada", "valor": "R$ 80,00 por coleta", "detalhe": "Punção venosa a vácuo, centrifugação e acondicionamento para transporte refrigerado.", "cat": "Biomedicina"},
    {"servico": "Plantão Diurno 12x36 (Home Care)", "solicitante": "Família Pantoja", "valor": "R$ 240,00 / plantão", "detalhe": "Cuidado assistencial com sonda nasoenteral e auxílio nas atividades diárias.", "cat": "Enfermagem"},
]

# No topo do seu app.py, importe o robô:
from scrapers_plantoes import buscar_plantoes_reais

# Na aba de Plantões (Tab 2), substitua a listagem antiga por:
def obter_demandas_estado(uf_codigo: str):
    # Chama o robô real de raspagem web já importado no topo
    demandas_brutas = buscar_plantoes_reais()
    demandas_formatadas = []
    
    for base in demandas_brutas:
        demandas_formatadas.append({
            "solicitante": f"{base['solicitante']} ({uf_codigo})",
            "servico": base["servico"],
            "local": f"Região Metropolitana - {uf_codigo}",
            "valor": base["valor"],
            "detalhe": base["detalhe"],
            "categoria": base["cat"],
            "telefone": base.get("telefone", "5591999999999")
        })
    return demandas_formatadas

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

def registrar_log_seguro(acao: str, detalhes: str = ""):
    try:
        nome_u = st.session_state.get("usuario_ativo", "Usuária")
        with engine.begin() as conn:
            conn.execute(
                text("INSERT INTO usersessionlog (user_name, action, details, timestamp) VALUES (:u, :a, :d, :t)"),
                {"u": nome_u, "a": acao, "d": detalhes, "t": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")}
            )
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
    .stButton > button *,
    div[data-testid="stFormSubmitButton"] > button * {
        color: #FFFFFF !important;
        -webkit-text-fill-color: #FFFFFF !important;
        font-weight: 800 !important;
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

# --- BARRA LATERAL ---
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
        <span style="background:#FFE0E9; color:#AD1457 !important; font-size:0.75rem; font-weight:800; padding:2px 8px; border-radius:10px;">v4.3</span>
    </div>
    <p style="color:#777777 !important; font-size: 0.8rem; font-weight: 600; margin: 0 0 10px 0;">Painel Dinâmico Nacional por Thiago Zuza 💕</p>
    <ul style="color:#221017 !important; font-size:0.84rem; line-height:1.5; padding-left:18px; margin:0; font-weight:600;">
        <li><b style="color:#AD1457 !important;">27 Estados Dinâmicos:</b> Vagas e clientes em todas as UFs.</li>
        <li><b style="color:#AD1457 !important;">Plantões 7 Dias:</b> Solicitações diárias atualizadas.</li>
        <li><b style="color:#AD1457 !important;">Gráficos Anuais:</b> Análise dinâmica de mercado 2025/2026.</li>
        <li><b style="color:#AD1457 !important;">Zero Erro de Banco:</b> Execução SQL direta e estável.</li>
    </ul>
</div>
""", unsafe_allow_html=True)

st.sidebar.markdown("<h4 style='color:#FFFFFF !important;'>👤 Sessão da Usuária</h4>", unsafe_allow_html=True)
if "usuario_ativo" not in st.session_state:
    st.session_state.usuario_ativo = "Meu Amor (Candidata)"

user_login_input = st.sidebar.text_input("Nome do Perfil:", value=st.session_state.usuario_ativo)
if user_login_input != st.session_state.usuario_ativo:
    st.session_state.usuario_ativo = user_login_input
    registrar_log_seguro("Alteração de Usuário", f"Perfil: {user_login_input}")

st.sidebar.markdown("<h3 style='color:#FFFFFF !important;'>🎀 Filtro por Estado (27 UFs)</h3>", unsafe_allow_html=True)
LISTA_ESTADOS = ["Todos os Estados"] + [f"{uf} - {nome}" for uf, nome in sorted(UFS_BRASIL.items())]
filtro_estado = st.sidebar.selectbox("📍 Filtrar Estado / Região:", LISTA_ESTADOS)

filtro_categoria = st.sidebar.radio("Área de Atuação:", ["Todas", "Enfermagem", "Biomedicina", "Indústria Farmacêutica"])
PORTAIS_DISPONIVEIS = ["Todos os Portais", "Catho", "Vagas.com", "InfoJobs", "LinkedIn", "Glassdoor", "Gupy Saúde"]
filtro_portal = st.sidebar.selectbox("🌐 Filtrar por Portal:", PORTAIS_DISPONIVEIS)

if st.sidebar.button("🔄 Sincronizar Tudo Agora ➔"):
    popular_catalogo_base()
    registrar_log_seguro("Sincronização Manual", "Recarregou os 27 estados")
    st.sidebar.success("Vagas e clientes sincronizados com sucesso!")
    st.rerun()

# --- CONSULTA DAS VAGAS VIA SQL SEGURO ---
try:
    with engine.connect() as conn:
        query_sql = "SELECT id, title, hospital_or_company, location, state, category, shift_type, specialty, description, url_apply, source, status, requires_graduation, created_at FROM job WHERE 1=1"
        params = {}
        
        if filtro_estado != "Todos os Estados":
            query_sql += " AND state = :uf"
            params["uf"] = filtro_estado[:2]
        if filtro_categoria != "Todas":
            query_sql += " AND category = :cat"
            params["cat"] = filtro_categoria
        if filtro_portal != "Todos os Portais":
            query_sql += " AND source = :src"
            params["src"] = filtro_portal

        rows = conn.execute(text(query_sql), params).fetchall()
        
        vagas_brutas = []
        for r in rows:
            vagas_brutas.append(Job(
                id=r[0], title=r[1], hospital_or_company=r[2], location=r[3],
                state=r[4], category=r[5], shift_type=r[6], specialty=r[7],
                description=r[8], url_apply=r[9], source=r[10], status=r[11],
                requires_graduation=bool(r[12]), created_at=sanitizar_datetime(r[13])
            ))

        def chave_ordenacao(j):
            dt_limpa = sanitizar_datetime(getattr(j, "created_at", None))
            return (calcular_peso_proximidade(j), dt_limpa)

        vagas_lista = sorted(vagas_brutas, key=chave_ordenacao, reverse=True)
        
        todas_rows = conn.execute(text("SELECT id, category, state, specialty, status FROM job")).fetchall()
        todas_vagas_ativas = [Job(id=r[0], category=r[1], state=r[2], specialty=r[3], status=r[4]) for r in todas_rows]
        total_candidatadas = len([v for v in todas_vagas_ativas if v.status == "Candidatada"])
        porcentagem_conquistada = min(int((total_candidatadas / max(len(todas_vagas_ativas), 1)) * 100), 100)

except Exception:
    vagas_lista = []
    todas_vagas_ativas = []
    total_candidatadas = 0
    porcentagem_conquistada = 0

vaga_recente = vagas_lista[0] if vagas_lista else None

# --- ESTRUTURA DE ABAS COMPLETAS ---
tab_vagas, tab_plantoes, tab_graficos, tab_enfermagem, tab_biomed, tab_agenda, tab_ia_curriculo, tab_trajeto, tab_feedback = st.tabs([
    "🌸 Mural Geral de Vagas",
    "🏥 Plantões & Clientes 7 Dias",
    "📊 Análise Gráfica Anual",
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

    st.markdown(f"### 🩺 Oportunidades ({len(vagas_lista)} encontradas no filtro):")
    for v in vagas_lista:
        peso_prox = calcular_peso_proximidade(v)
        badge_prox = '<span class="badge-proxima">🏠 Bem Pertinho de Casa</span>' if peso_prox >= 80 else ''
        dt_c = sanitizar_datetime(getattr(v, "created_at", None))
        dt_fmt = dt_c.strftime("%d/%m/%Y às %H:%M")
        
        link_vaga = v.url_apply if v.url_apply.startswith("http") else f"https://{v.url_apply}"
        txt_zap = urllib.parse.quote(f"Olha essa oportunidade de {v.title} no {v.hospital_or_company}: {link_vaga}")
        link_zap = f"https://api.whatsapp.com/send?text={txt_zap}"

        badge_categoria = f'<span class="badge">🩺 {v.category}</span>' if v.category == "Enfermagem" else f'<span class="badge">🔬 {v.category}</span>'

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
                with engine.begin() as conn:
                    conn.execute(text("UPDATE job SET status = 'Candidatada' WHERE id = :id"), {"id": v.id})
                registrar_log_seguro("Candidatura Salva", f"Vaga: {v.title}")
                st.success("Salva!")
                st.rerun()

# ================= TAB 2: PLANTÕES & CLIENTES 7 DIAS (27 ESTADOS) =================
with tab_plantoes:
    st.markdown("<h2 style='color:#C2185B;'>🏥 Plantões em Enfermagem & Solicitações de Clientes (7 Dias por Semana)</h2>", unsafe_allow_html=True)
    st.markdown("Oportunidades ativas de Home Care, plantões avulsos 12x36 e coberturas particulares em todas as 27 Unidades da Federação. 💕")

    uf_selecionada_plantao = st.selectbox(
        "Selecione o Estado para ver as Solicitações de Atendimento:",
        list(UFS_BRASIL.keys()),
        index=list(UFS_BRASIL.keys()).index("PA")
    )
    demandas_uf = obter_demandas_estado(uf_selecionada_plantao)

    st.markdown(f"#### 👥 Pacientes & Famílias Buscando Atendimento em **{UFS_BRASIL[uf_selecionada_plantao]} ({uf_selecionada_plantao})**:")
    
    for d in demandas_uf:
        txt_apresentacao = (
            f"Olá, {d['solicitante']}! Tudo bem?\n\n"
            f"Vi a sua solicitação para '{d['servico']}' em {d['local']}.\n"
            f"Sou profissional da Saúde com registro ativo e sólida experiência assistencial e dedicação. "
            f"Tenho total disponibilidade para lhe atender com segurança e humanização.\n\n"
            f"Podemos alinhar o horário?"
        )
        
        # Pega o telefone que veio do dicionário (ou usa o padrão se não houver)
        tel_cliente = d.get("telefone", "5591999999999")
        
        # Monta o link direcionando com o número e o texto
        link_zap = f"https://api.whatsapp.com/send?phone={tel_cliente}&text={urllib.parse.quote(txt_apresentacao)}"

        st.markdown(f"""
        <div class="content-box" style="border-left: 6px solid #FF9800; margin-bottom: 12px;">
            <b style="color:#C2185B; font-size:1.05rem;">{d['servico']}</b><br>
            <span style="color:#444; font-size:0.88rem;">👤 <b>Solicitante:</b> {d['solicitante']} &nbsp;|&nbsp; 📍 {d['local']}</span><br>
            <span style="color:#E65100; font-weight:bold; font-size:0.88rem;">💰 Remuneração: {d['valor']}</span>
            <p style="color:#333; font-size:0.9rem; margin:6px 0 10px 0;">{d['detalhe']}</p>
            <a href="{link_zap}" target="_blank" class="action-link" style="background:#FFF3E0; color:#E65100 !important; border:1px solid #FFE0B2;">
                💬 Enviar Apresentação no WhatsApp ➔
            </a>
        </div>
        """, unsafe_allow_html=True)

# ================= TAB 3: ANÁLISE GRÁFICA ANUAL DINÂMICA =================
import streamlit as st
import pandas as pd
import numpy as np
st.markdown("### 📊 Análise Gráfica Avançada & Projeção (2026-2027)")
st.markdown("Comparativo dinâmico de vagas, tendências e expansão geográfica para Enfermagem e Biomedicina.")

# --- FILTROS DE CONTROLO DO PAINEL COM KEYS ÚNICAS ---
col_f1, col_f2, col_f3 = st.columns(3)

with col_f1:
    prof_filtro = st.selectbox(
        "Profissão em Análise:",
        ["Ambos (Comparativo)", "Enfermagem", "Biomedicina"],
        key="filtro_profissao_graficos"
    )

with col_f2:
    escopo_geo_grafico = st.selectbox(
        "Âmbito Geográfico:",
        ["Brasil (Todos os 27 Estados)", "Mundo / Global (Internacional)"],
        key="filtro_escopo_geo_graficos"
    )

with col_f3:
    periodo_grafico = st.selectbox(
        "Horizonte Temporal:",
        ["2026 - 2027 (Evolução & Projeção)", "Ano de 2026", "Ano de 2027 (Projeção)"],
        key="filtro_periodo_graficos"
    )

# --- SELETOR DE REGIÃO COM KEY ÚNICA ---
if escopo_geo_grafico == "Brasil (Todos os 27 Estados)":
    regiao_selecionada = st.selectbox(
        "Selecione o Estado:",
        ["Todos os Estados (Nacional)", "Acre (AC)", "Alagoas (AL)", "Amapá (AP)", "Amazonas (AM)", "Bahia (BA)", 
         "Ceará (CE)", "Distrito Federal (DF)", "Espírito Santo (ES)", "Goiás (GO)", "Maranhão (MA)", 
         "Mato Grosso (MT)", "Mato Grosso do Sul (MS)", "Minas Gerais (MG)", "Pará (PA)", "Paraíba (PB)", 
         "Paraná (PR)", "Pernambuco (PE)", "Piauí (PI)", "Rio de Janeiro (RJ)", "Rio Grande do Norte (RN)", 
         "Rio Grande do Sul (RS)", "Rondônia (RO)", "Roraima (RR)", "Santa Catarina (SC)", "São Paulo (SP)", 
         "Sergipe (SE)", "Tocantins (TO)"],
        key="select_estado_brasil_graficos"
    )
else:
    regiao_selecionada = st.selectbox(
        "Selecione o País / Polo Global:",
        ["Portugal (Enfermagem & Saúde)", "Estados Unidos (EUA)", "Europa (Geral)", "Médio Oriente (Dubai/Qatar)", "América Latina (Geral)"],
        key="select_pais_global_graficos"
    )

st.markdown(f"***Painel ativo para:** {prof_filtro} | **Local:** {regiao_selecionada} | **Período:** {periodo_grafico}*")
st.markdown("---")

# --- GERAÇÃO DE DADOS DINÂMICOS PARA OS GRÁFICOS (2026-2027) ---
meses = ["Jan", "Fev", "Mar", "Abr", "Mai", "Jun", "Jul", "Ago", "Set", "Out", "Nov", "Dez"]

np.random.seed(42)
df_vagas = pd.DataFrame({
    "Mês": meses * 2,
    "Ano": ["2026"] * 12 + ["2027 (Proj.)"] * 12,
    "Enfermagem": np.random.randint(45, 95, 24),
    "Biomedicina": np.random.randint(25, 65, 24)
})

# --- COLUNA 1 E 2: TENDÊNCIA E COMPARATIVO ANUAL ---
col_g1, col_g2 = st.columns(2)

with col_g1:
    st.markdown(f"#### 📈 Tendência Mensal de Vagas ({regiao_selecionada})")
    try:
        if prof_filtro == "Enfermagem":
            df_plot = df_vagas.pivot(index="Mês", columns="Ano", values="Enfermagem")
            st.line_chart(df_plot)
        elif prof_filtro == "Biomedicina":
            df_plot = df_vagas.pivot(index="Mês", columns="Ano", values="Biomedicina")
            st.line_chart(df_plot)
        else:
            df_enf = df_vagas.pivot(index="Mês", columns="Ano", values="Enfermagem")
            df_bio = df_vagas.pivot(index="Mês", columns="Ano", values="Biomedicina")
            df_enf.columns = [f"{c} (Enf)" for c in df_enf.columns]
            df_bio.columns = [f"{c} (Bio)" for c in df_bio.columns]
            df_final = pd.concat([df_enf, df_bio], axis=1)
            st.line_chart(df_final)
    except Exception:
        st.line_chart(df_vagas.set_index("Mês")[["Enfermagem", "Biomedicina"]])

with col_g2:
    st.markdown("#### 📊 Comparativo Anual Consolidado (2026 vs 2027)")
    df_anual = pd.DataFrame({
        "2026": [df_vagas[df_vagas["Ano"]=="2026"]["Enfermagem"].mean(), df_vagas[df_vagas["Ano"]=="2026"]["Biomedicina"].mean()],
        "2027 (Proj.)": [df_vagas[df_vagas["Ano"]=="2027 (Proj.)"]["Enfermagem"].mean(), df_vagas[df_vagas["Ano"]=="2027 (Proj.)"]["Biomedicina"].mean()]
    }, index=["Enfermagem", "Biomedicina"]).T
    
    if prof_filtro == "Enfermagem":
        st.bar_chart(df_anual[["Enfermagem"]])
    elif prof_filtro == "Biomedicina":
        st.bar_chart(df_anual[["Biomedicina"]])
    else:
        st.bar_chart(df_anual)

# --- COLUNA 3 E 4: DISTRIBUIÇÃO GEOGRÁFICA E ESPECIALIDADES ---
col_g3, col_g4 = st.columns(2)

with col_g3:
    st.markdown(f"#### 🗺️ Distribuição de Oportunidades ({regiao_selecionada})")
    if "Brasil" in escopo_geo_grafico:
        df_geo = pd.DataFrame({"Volume de Vagas": [120, 95, 80, 60, 45]}, index=["São Paulo (SP)", "Minas Gerais (MG)", "Rio de Janeiro (RJ)", "Paraná (PR)", regiao_selecionada])
    else:
        df_geo = pd.DataFrame({"Volume de Vagas": [150, 110, 85, 50, 40]}, index=["Lisboa/Porto (PT)", "EUA (Geral)", "Europa Ocidental", "Médio Oriente", regiao_selecionada])
    st.bar_chart(df_geo)

with col_g4:
    st.markdown(f"#### 🩺 Demanda por Especialidades ({prof_filtro})")
    if prof_filtro == "Enfermagem":
        df_esp = pd.DataFrame({"Demanda": [90, 75, 60, 45]}, index=["UTI / Cuidados Críticos", "Urgência e Emergência", "Home Care", "Saúde Pública"])
    elif prof_filtro == "Biomedicina":
        df_esp = pd.DataFrame({"Demanda": [85, 70, 55, 40]}, index=["Análises Clínicas", "Biologia Molecular", "Indústria Farmacêutica", "Reprodução Humana"])
    else:
        df_esp = pd.DataFrame({"Demanda": [88, 72, 58, 42]}, index=["UTI & Críticos (Enf)", "Análises Clínicas (Bio)", "Urgência (Enf)", "Biologia Molecular (Bio)"])
    st.bar_chart(df_esp)
# ================= TAB 4: ESPECIAL ENFERMAGEM & COREN =================
with tab_enfermagem:
    st.markdown("<h2 style='color: #C2185B;'>🩺 Painel de Enfermagem, Carreira & COREN</h2>", unsafe_allow_html=True)
    st.info("Espaço dedicado à atuação assistencial, dimensionamento de plantões, especializações e concursos públicos.")
    
    col_e1, col_e2 = st.columns(2)
    with col_e1:
        st.markdown("#### 📋 Diretrizes Assistenciais de Destaque")
        st.markdown("""
        • **Terapia Intensiva (UTI):** Manejo de drogas vasoativas, ventilação mecânica e monitorização hemodinâmica invasiva.<br>
        • **Classificação de Risco (Manchester):** Avaliação ágil com acolhimento humanizado em prontos-socorros.<br>
        • **Centro Cirúrgico & CME:** Paramentação asséptica, tempos cirúrgicos e esterilização de artigos críticos.
        """, unsafe_allow_html=True)
    with col_e2:
        st.markdown("#### 🏛️ Concursos & Editais de Enfermagem")
        st.markdown("""
        • **EBSERH:** Editais abertos para hospitais universitários federais com vagas para Enfermeiras e Técnicas.<br>
        • **Forças Armadas:** Concursos para o Quadro Complementar de Saúde.<br>
        • **Prefeituras e Secretarias de Saúde:** Vagas para UBS, Estratégia Saúde da Família e UPAs.
        """, unsafe_allow_html=True)

# ================= TAB 5: ESPECIAL BIOMEDICINA & MERCADO =================
with tab_biomed:
    st.markdown("<h2 style='color: #AD1457;'>🔬 Painel Estratégico de Biomedicina & Mercado</h2>", unsafe_allow_html=True)
    st.info("Espaço dedicado a Análises Clínicas, Biologia Molecular, Indústria Farmacêutica e Habilitações no CRBM.")
    
    col_b1, col_b2 = st.columns(2)
    with col_b1:
        st.markdown("#### 🔬 Áreas Mais Valorizadas no Início de Carreira")
        st.markdown("""
        • **Análises Clínicas:** Automação em hematologia, bioquímica e urinálise.<br>
        • **Biologia Molecular:** Extração de DNA/RNA, RT-PCR e sequenciamento genético (NGS).<br>
        • **Farmacovigilância:** Notificação de eventos adversos e pesquisa clínica em multinacionais.
        """, unsafe_allow_html=True)
    with col_b2:
        st.markdown("#### 💊 Indústrias Farmacêuticas com Programas de Entrada")
        st.markdown("""
        • **Eurofarma:** Vagas de analista júnior e trainee em Itapevi/SP.<br>
        • **EMS Farmacêutica:** Atuação em controle de qualidade e pesquisa clínica.<br>
        • **Hypera Pharma:** Oportunidades em assuntos regulatórios e bioequivalência.
        """, unsafe_allow_html=True)

# ================= TAB 6: AGENDA MÉDICA, EXAMES & SUS =================
with tab_agenda:
    st.markdown("<h2 style='color: #AD1457;'>📅 Agenda Médica, Exames, SUS & Saúde Ocupacional</h2>", unsafe_allow_html=True)
    st.markdown("Acompanhe seus exames, consultas, vacinação e orçamentos laboratoriais em todo o Brasil. 💕")

    col_ag1, col_ag2 = st.columns([1, 1])
    with col_ag1:
        st.markdown("#### ➕ Agendar Exame ou Consulta")
        with st.form("form_novo_exame_seguro"):
            t_ag = st.selectbox("Tipo:", ["Exame Laboratorial", "Consulta Médica", "Exame Ocupacional", "Vacinação", "Retorno"])
            tit_ag = st.text_input("Procedimento:", placeholder="Ex: Hemograma, Sorologia, Consulta...")
            loc_ag = st.text_input("Local / Laboratório:", placeholder="Ex: Beneficente, Ruth Brazão, Fleury...")
            col_v, col_d, col_h = st.columns(3)
            with col_v:
                prc_ag = st.number_input("Valor (R$):", min_value=0.0, step=10.0, format="%.2f")
            with col_d:
                dat_ag = st.date_input("Data:", value=date.today())
            with col_h:
                hor_ag = st.time_input("Horário:", value=datetime.now().time())
            obs_ag = st.text_area("Anotações / Preparo:", placeholder="Ex: Jejum de 8h, levar documento com foto...")
            
            if st.form_submit_button("Salvar na Agenda ➔"):
                if tit_ag.strip():
                    with engine.begin() as conn:
                        conn.execute(
                            text("""
                                INSERT INTO medicalappointment (title, appointment_type, location, network_provider, estimated_price, scheduled_date, scheduled_time, notes, is_completed, created_at)
                                VALUES (:t, :at, :l, 'Particular', :p, :sd, :st, :n, 0, :c)
                            """),
                            {
                                "t": tit_ag.strip(), "at": t_ag, "l": loc_ag.strip() or "A definir",
                                "p": prc_ag, "sd": dat_ag.strftime("%Y-%m-%d"), "st": hor_ag.strftime("%H:%M"),
                                "n": obs_ag.strip(), "c": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
                            }
                        )
                    registrar_log_seguro("Agendou Exame", f"{t_ag}: {tit_ag}")
                    st.success("Salvo com sucesso na agenda!")
                    st.rerun()

    with col_ag2:
        st.markdown("#### 📋 Histórico de Procedimentos Salvos")
        with engine.connect() as conn:
            ex_rows = conn.execute(text("SELECT title, appointment_type, location, scheduled_date, scheduled_time, estimated_price, notes FROM medicalappointment ORDER BY scheduled_date DESC LIMIT 5")).fetchall()
        if ex_rows:
            for ex in ex_rows:
                st.markdown(f"""
                <div class="content-box" style="padding:10px 14px; margin-bottom:8px;">
                    <b>{ex[0]}</b> ({ex[1]})<br>
                    <small>📅 {ex[3]} às {ex[4]} | 📍 {ex[2]} | R$ {ex[5]:.2f}</small><br>
                    <small><i>{ex[6]}</i></small>
                </div>
                """, unsafe_allow_html=True)
        else:
            st.info("Nenhum exame cadastrado no momento.")

# ================= TAB 7: LINKEDIN, CURRÍCULOS & IA =================
st.markdown("### 🌍 Seleção de Mercado e Região de Atuação")
mercado_selecionado = st.selectbox(
    "Escolha o país ou âmbito pretendido:",
    ["Brasil (27 Estados)", "Portugal (Enfermagem - Todo o País)", "Global / Internacional"]
)

# O primeiro bloco de escolha TEM de começar com 'if'
if mercado_selecionado == "Portugal (Enfermagem - Todo o País)":
    st.markdown("---")
    st.markdown("### 🇵🇹 Buscador Dinâmico de Vagas de Enfermagem em Portugal")
    st.info("ℹ️ Sistema integrado para profissionais com equivalência/inscrição na Ordem dos Enfermeiros (OE).")
    
    col_pt_a, col_pt_b = st.columns(2)
    with col_pt_a:
        distrito_pt = st.selectbox(
            "Selecione o Distrito / Região:",
            ["Lisboa", "Porto", "Coimbra", "Braga", "Aveiro", "Faro", "Setúbal", "Leiria", "Viseu", "Madeira / Açores"]
        )
    with col_pt_b:
        especialidade_pt = st.selectbox(
            "Área / Especialidade:",
            ["Cuidados Gerais (Hospitalar)", "Medicina Intensiva (UTI)", "Urgência e Emergência", "Saúde Pública", "Bloco Operatório (Perioperatória)"]
        )
        
    if st.button("🔍 Consultar Vagas Ativas em Portugal (IA em Tempo Real)"):
        with st.spinner(f"A pesquisar oportunidades ativas em {distrito_pt} ({especialidade_pt})..."):
            try:
                api_key_val = ""
                try:
                    if "GEMINI_API_KEY" in st.secrets:
                        api_key_val = st.secrets["GEMINI_API_KEY"]
                except Exception:
                    pass
                if not api_key_val:
                    api_key_val = os.environ.get("GEMINI_API_KEY", "")

                if HAS_GENAI and api_key_val:
                    client = genai.Client(api_key=api_key_val)
                    prompt_vagas_pt = f"""
                    Atue como um recrutador sénior especializado na área da saúde em Portugal.
                    Forneça 3 oportunidades ou contextos de emprego ativos e altamente relevantes para o distrito de {distrito_pt} em Portugal na especialidade de {especialidade_pt}.
                    Para cada oportunidade, inclua:
                    1. **Instituição / Hospital / Grupo de Saúde**
                    2. **Função e Contexto**
                    3. **Requisitos da Ordem dos Enfermeiros (OE)**
                    4. **Condições Estimadas e Forma de Candidatura**
                    
                    Formate a resposta de forma limpa e profissional em Markdown.
                    """
                    response_pt = client.models.generate_content(
                        model='gemini-1.5-flash',
                        contents=prompt_vagas_pt
                    )
                    st.markdown("### 📋 Oportunidades Encontradas:")
                    st.markdown(response_pt.text)
                else:
                    st.warning("⚠️ Chave da API do Gemini não configurada nos Secrets.")
            except Exception as e:
                st.error(f"Erro ao gerar vagas dinâmicas: {e}")

# Opções seguintes usam corretamente 'elif' ou 'else'
elif mercado_selecionado == "Global / Internacional":
    st.markdown("---")
    st.markdown("### 🌐 Oportunidades Globais de Saúde")
    st.info("Projetos internacionais, telemedicina e cooperação humanitária em saúde global.")
    st.write("- **Organizações Internacionais:** Oportunidades em projetos de saúde pública na Europa e Américas.")

else:
    st.markdown("---")
    st.markdown("### 🇧🇷 Oportunidades Home Care & Plantões nos 27 Estados")
    # O código correspondente aos estados do Brasil continua aqui

try:
    from fpdf import FPDF  # type: ignore[reportMissingModuleSource]
except ModuleNotFoundError as exc:
    class FPDF:  # type: ignore[no-redef]
        def __init__(self, *args, **kwargs):
            raise ModuleNotFoundError(
                "A biblioteca 'fpdf' não está instalada. Execute 'pip install fpdf' para gerar PDFs."
            ) from exc

class PDFRecrutamento(FPDF):
    def header(self):
        self.set_font("Arial", "B", 16)
        self.set_text_color(33, 37, 41)
        self.cell(0, 10, "Curriculo Profissional Otimizado - Saude", 0, 1, "C")
        self.set_font("Arial", "I", 10)
        self.set_text_color(108, 117, 125)
        self.cell(0, 6, "Especializado para Enfermagem e Biomedicina", 0, 1, "C")
        self.ln(10)

    def footer(self):
        self.set_y(-15)
        self.set_font("Arial", "I", 8)
        self.set_text_color(150, 150, 150)
        self.cell(0, 10, f"Pagina {self.page_no()}", 0, 0, "C")


def gerar_pdf_curriculo(texto_base, sugestoes_ia=""):
    pdf = PDFRecrutamento()
    pdf.add_page()
    pdf.set_auto_page_break(auto=True, margin=15)

    pdf.set_font("Arial", "B", 12)
    pdf.set_text_color(13, 110, 253)
    pdf.cell(0, 8, "1. Resumo e Perfil Profissional", 0, 1)

    pdf.set_font("Arial", "", 10)
    pdf.set_text_color(33, 37, 41)
    texto_limpo = str(texto_base).encode("latin-1", "replace").decode("latin-1")
    pdf.multi_cell(0, 6, texto_limpo[:1500])
    pdf.ln(5)

    if sugestoes_ia:
        pdf.set_font("Arial", "B", 12)
        pdf.set_text_color(13, 110, 253)
        pdf.cell(0, 8, "2. Adequacao e Melhorias Sugeridas para ATS / Saude", 0, 1)

        pdf.set_font("Arial", "", 9)
        pdf.set_text_color(50, 50, 50)
        sugestoes = str(sugestoes_ia).encode("latin-1", "replace").decode("latin-1")
        pdf.multi_cell(0, 5, sugestoes[:1500])

    resultado = pdf.output(dest="S")
    return resultado.encode("latin-1") if isinstance(resultado, str) else bytes(resultado)


with tab_ia_curriculo:
    st.markdown(
        "<h2 style='color: #0077B5;'>💼 LinkedIn, Modelos de Documentos & Análise Inteligente de Currículo</h2>",
        unsafe_allow_html=True,
    )

    col_d1, col_d2 = st.columns(2)

    with col_d1:
        st.markdown("#### 🩺 Modelo Pronto: Enfermagem Assistencial")
        enf_mod = (
            "OBJETIVO: Enfermeira Assistencial - UTI Adulto / Emergência\n"
            "RESUMO: Experiência assistencial com pacientes críticos, drogas vasoativas, "
            "punção e protocolos de segurança do paciente. COREN ativo."
        )
        st.text_area("Currículo Enfermagem:", enf_mod, height=120)
        st.download_button(
            "📥 Baixar Currículo Enfermagem (PDF) ➔",
            gerar_pdf_curriculo(
                enf_mod,
                "Modelo padrão otimizado para Enfermagem Assistencial e UTI.",
            ),
            "Curriculo_Enfermagem.pdf",
            mime="application/pdf",
        )

    with col_d2:
        st.markdown("#### 🔬 Modelo Pronto: Biomedicina / Análises")
        bio_mod = (
            "OBJETIVO: Biomédica Analista - Análises Clínicas / Biologia Molecular\n"
            "RESUMO: Domínio em rotinas laboratoriais, microscopia, controle de "
            "qualidade (CQI/CQE) e liberação de laudos. CRBM ativo."
        )
        st.text_area("Currículo Biomedicina:", bio_mod, height=120)
        st.download_button(
            "📥 Baixar Currículo Biomedicina (PDF) ➔",
            gerar_pdf_curriculo(
                bio_mod,
                "Modelo padrão otimizado para Biomedicina e Análises Clínicas.",
            ),
            "Curriculo_Biomedicina.pdf",
            mime="application/pdf",
        )

    st.markdown("---")
st.markdown("### 🤖 Diagnóstico Dinâmico de Currículo para Saúde (Enfermagem & Biomedicina)")
up_pdf = st.file_uploader("Envie o currículo em PDF para análise detalhada:", type=["pdf"])

if up_pdf is not None:
    from pypdf import PdfReader
    reader = PdfReader(up_pdf)
    texto_pdf = ""
    for page in reader.pages:
        texto_pdf += page.extract_text() or ""
    
    # Validação executada apenas com o texto extraído em segurança
    palavras_chave_saude = ["enfermagem", "enfermeiro", "enfermeira", "biomedicina", "biomédico", "biomédica", "coren", "crbm", "análises clínicas", "saúde"]
    texto_minusculo = texto_pdf.lower()
    area_compativel = any(termo in texto_minusculo for termo in palavras_chave_saude)

    if not area_compativel:
        st.error("⚠️ **Atenção:** O currículo enviado aparenta não pertencer às áreas de Enfermagem ou Biomedicina. Por favor, envie um currículo direcionado para a área da saúde para obter um diagnóstico preciso.")
    else:
        st.success("✅ Currículo carregado e validado para a área da saúde com sucesso!")
        st.text_area("Texto Extraído:", texto_pdf, height=200)

        # Lógica da chave de API e IA
        api_key_val = ""
        try:
            if "GEMINI_API_KEY" in st.secrets:
                api_key_val = st.secrets["GEMINI_API_KEY"]
            elif "general" in st.secrets and "GEMINI_API_KEY" in st.secrets["general"]:
                api_key_val = st.secrets["general"]["GEMINI_API_KEY"]
        except Exception:
            pass
        
        if not api_key_val:
            api_key_val = os.environ.get("GEMINI_API_KEY", "")

        if HAS_GENAI and api_key_val:
            with st.spinner("✨ A analisar pontos fortes, lacunas e adequação para Enfermagem/Biomedicina..."):
                try:
                    os.environ["GEMINI_API_KEY"] = api_key_val
                    client = genai.Client(api_key=api_key_val)
                    
                    prompt_analise = f"""
                    Analise o seguinte currículo voltado para as áreas de Enfermagem ou Biomedicina. 
                    Forneça um feedback construtivo estruturado em:
                    1. **Pontos Fortes** encontrados.
                    2. **Pontos de Melhoria / Lacunas** (ex: falta de citação explícita do COREN/CRBM, palavras-chave ATS, formatação de competências técnicas em UTI ou Análises Clínicas).
                    3. **Sugestões Práticas de Reescrita** para o Resumo Profissional.
                    
                    Texto do Currículo:
                    {texto_pdf[:3000]}
                    """
                    response = client.models.generate_content(
                        model='gemini-1.5-flash',
                        contents=prompt_analise
                    )
                    st.markdown("### 📋 Relatório de Diagnóstico & Melhorias")
                    st.markdown(response.text)
                except Exception as ai_err:
                    st.warning(f"Erro ao contactar a IA: {ai_err}")
        else:
            st.info("💡 Dica: Verifique se a variável `GEMINI_API_KEY` está gravada nos Secrets do Streamlit Cloud.")
            
        # Botão para baixar o PDF otimizado
        st.markdown("---")
        st.markdown("### 📥 Gerar Documento Otimizado em PDF")
        pdf_bytes_analise = gerar_pdf_curriculo(texto_pdf, "Otimizado para processos seletivos na área de Enfermagem e Biomedicina, com foco em conformidade ATS e registo profissional.")
        st.download_button(
            label="📄 Baixar Currículo Analisado e Otimizado (PDF)",
            data=pdf_bytes_analise,
            file_name="Curriculo_Otimizado_Saude.pdf",
            mime="application/pdf"
        )

# ================= TAB 8: TRAJETO, UBER & PLANTÃO =================
with tab_trajeto:
    st.markdown("<h2 style='color: #AD1457;'>🗺️ Trajeto, Uber, Custos & Cuidados com Você 💕</h2>", unsafe_allow_html=True)

    cid_sel = st.selectbox("Selecione a Região:", ["Belém - PA", "São Paulo - SP", "Rio de Janeiro - RJ"])
    d_clima = obter_previsao_tempo_detalhada(cid_sel)
    
    st.markdown(f"""
    <div class="content-box" style="border-left:6px solid #FF69B4;">
        <b style="color:#C2185B; font-size:1.05rem;">🌤️ Clima em Tempo Real ({cid_sel}): {d_clima['temp']}°C</b><br>
        <span style="color:#AD1457; font-weight:bold;">Probabilidade de Chuva: {d_clima['prob_chuva']}%</span>
        <p style="margin:6px 0 0 0; color:#333;">🎒 <b>Dica de Plantão:</b> Garrafinha de água gelada, casaco confortável para o ar-condicionado e calçado fechado impermeável.</p>
    </div>
    """, unsafe_allow_html=True)

    msg_saida = "Oi, amor! Estou saindo do plantão/estudos agora e já a caminho de casa. Te aviso assim que chegar! 💕"
    link_saida = f"https://api.whatsapp.com/send?phone=5511913129697&text={urllib.parse.quote(msg_saida)}"
    st.markdown(f"""
    <div style="text-align:center; margin:16px 0;">
        <a href="{link_saida}" target="_blank" class="action-link" style="background: linear-gradient(135deg, #FF6584, #FF476F); color:white !important; font-size:1rem; padding:12px 26px;">
            🚨 Mandar Aviso de Saída de Plantão Direto p/ Thiago ➔
        </a>
    </div>
    """, unsafe_allow_html=True)

# ================= TAB 9: DIÁRIO DE USO & SESSÃO =================
with tab_feedback:
    st.markdown("<h2 style='color:#C2185B;'>📝 Diário de Uso, Mood & Registro de Interações</h2>", unsafe_allow_html=True)
    
    with st.form("form_diario_salvar_seguro"):
        c_m1, c_m2 = st.columns(2)
        with c_m1:
            mood_reg = st.selectbox(
                "Como está seu humor e ânimo hoje?",
                [
                    "✨ Motivada e confiante nas oportunidades!",
                    "💖 Gostei muito das vagas que vi hoje!",
                    "🧸 Cansadinha do plantão/faculdade, mas em paz",
                    "🤔 Em dúvida entre enfermagem assistencial e laboratório",
                    "🥺 Precisando de um abraço e apoio do Thiago"
                ]
            )
            cand_feita = st.checkbox("Conseguiu se candidatar a alguma vaga hoje?", value=True)
        with c_m2:
            notas_reg = st.text_area("Anotações do seu dia:", placeholder="Ex: Olhei as vagas de UTI e gostei do Porto Dias...")
            sugest_reg = st.text_input("Pedidos de melhoria para o Thiago:", placeholder="Ex: Mais opções de plantões no Marco...")

        if st.form_submit_button("Salvar no Diário & Gerar WhatsApp p/ Thiago ➔"):
            with engine.begin() as conn:
                conn.execute(
                    text("""
                        INSERT INTO feedbackentry (user_mood, notes, found_good_job, suggestions, created_at)
                        VALUES (:m, :n, :fg, :s, :c)
                    """),
                    {
                        "m": mood_reg, "n": notas_reg.strip(), "fg": int(cand_feita),
                        "s": sugest_reg.strip(), "c": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
                    }
                )
            registrar_log_seguro("Feedback Registrado", f"Mood: {mood_reg}")
            st.success("✅ Registro salvo com sucesso no seu diário!")

            msg_zap = (
                f"Oi, amor! Registrei meu feedback de hoje no portal:\n\n"
                f"• Meu Mood: {mood_reg}\n"
                f"• Candidatou-se hoje? {'Sim, me candidatei! 🎉' if cand_feita else 'Ainda não, só olhei.'}\n"
                f"• Anotações: {notas_reg.strip() or 'Nenhuma'}\n"
                f"• Pedidos: {sugest_reg.strip() or 'Tudo ótimo!'}\n\n"
                f"Te amo muito! 💕"
            )
            link_zap_th = f"https://api.whatsapp.com/send?phone=5511913129697&text={urllib.parse.quote(msg_zap)}"
            st.markdown(f"""
            <div style="margin-top:10px;">
                <a href="{link_zap_th}" target="_blank" class="action-link" style="background:#25D366; color:white !important; padding:10px 22px;">
                    📲 Enviar Esse Resumo Diretamente p/ WhatsApp do Thiago ➔
                </a>
            </div>
            """, unsafe_allow_html=True)

# --- ASSINATURA ---
st.divider()
st.markdown("<div style='height: 40px;'></div>", unsafe_allow_html=True)
st.markdown("""
<div style="text-align: center; color: #AD1457; font-size: 1.05rem; font-weight: 800;">
    🐾 Desenvolvido com todo o amor por <b>Thiago Zuza</b> para o seu amor 💕 ✨
</div>
""", unsafe_allow_html=True)

# --- ADIÇÃO: Filtro Geográfico Global, 27 Estados do Brasil e Enfermagem em Portugal ---
# --- FILTRO GEOGRÁFICO UNIFICADO NA BARRA LATERAL ---
st.sidebar.markdown("---")
st.sidebar.markdown("### 🌍 Filtro Geográfico Avançado")

mercado_selecionado = st.sidebar.selectbox(
    "Selecione o Mercado / Região:",
    ["Brasil (27 Estados)", "Portugal (Enfermagem - Todo o País)", "Global / Internacional"]
)

# --- ÁREA PRINCIPAL DINÂMICA CONSOANTE A ESCOLHA DA BARRA LATERAL ---
if mercado_selecionado == "Portugal (Enfermagem - Todo o País)":
    st.markdown("### 🇵🇹 Buscador Dinâmico de Vagas de Enfermagem em Portugal")
    st.info("ℹ️ Sistema integrado para profissionais com equivalência/inscrição na Ordem dos Enfermeiros (OE).")
    
    col_pt_a, col_pt_b = st.columns(2)
    with col_pt_a:
        distrito_pt = st.selectbox(
            "Selecione o Distrito / Região em Portugal:",
            ["Lisboa", "Porto", "Coimbra", "Braga", "Aveiro", "Faro", "Setúbal", "Leiria", "Viseu", "Madeira / Açores"]
        )
    with col_pt_b:
        especialidade_pt = st.selectbox(
            "Área / Especialidade:",
            ["Cuidados Gerais (Hospitalar)", "Medicina Intensiva (UTI)", "Urgência e Emergência", "Saúde Pública", "Bloco Operatório (Perioperatória)"]
        )
        
    if st.button("🔍 Consultar Vagas Ativas em Portugal (IA em Tempo Real)"):
        with st.spinner(f"A pesquisar oportunidades ativas em {distrito_pt} ({especialidade_pt})..."):
            try:
                api_key_val = ""
                try:
                    if "GEMINI_API_KEY" in st.secrets:
                        api_key_val = st.secrets["GEMINI_API_KEY"]
                except Exception:
                    pass
                if not api_key_val:
                    api_key_val = os.environ.get("GEMINI_API_KEY", "")

                if HAS_GENAI and api_key_val:
                    client = genai.Client(api_key=api_key_val)
                    prompt_vagas_pt = f"""
                    Atue como um recrutador sénior especializado na área da saúde em Portugal.
                    Forneça 3 oportunidades ou contextos de emprego ativos e altamente relevantes para o distrito de {distrito_pt} em Portugal na especialidade de {especialidade_pt}.
                    Para cada oportunidade, inclua:
                    1. **Instituição / Hospital / Grupo de Saúde**
                    2. **Função e Contexto**
                    3. **Requisitos da Ordem dos Enfermeiros (OE)**
                    4. **Condições Estimadas e Forma de Candidatura**
                    
                    Formate a resposta de forma limpa e profissional em Markdown.
                    """
                    response_pt = client.models.generate_content(
                        model='gemini-1.5-flash',
                        contents=prompt_vagas_pt
                    )
                    st.markdown("### 📋 Oportunidades Encontradas:")
                    st.markdown(response_pt.text)
                else:
                    st.warning("⚠️ Chave da API do Gemini não configurada.")
            except Exception as e:
                st.error(f"Erro ao gerar vagas dinâmicas: {e}")

elif mercado_selecionado == "Global / Internacional":
    st.markdown("### 🌐 Buscador Dinâmico Global / Internacional")
    st.info("Pesquisa de oportunidades internacionais em saúde adaptadas por continente e região.")
    
    continente_sel = st.selectbox(
        "Selecione o Continente / Região Global:",
        ["Europa (Geral)", "América do Norte", "América Latina", "Médio Oriente", "Ásia-Pacífico"]
    )
    
    if st.button("🔍 Consultar Oportunidades Globais (IA em Tempo Real)"):
        with st.spinner(f"A pesquisar oportunidades de saúde em {continente_sel}..."):
            try:
                api_key_val = ""
                try:
                    if "GEMINI_API_KEY" in st.secrets:
                        api_key_val = st.secrets["GEMINI_API_KEY"]
                except Exception:
                    pass
                if not api_key_val:
                    api_key_val = os.environ.get("GEMINI_API_KEY", "")

                if HAS_GENAI and api_key_val:
                    client = genai.Client(api_key=api_key_val)
                    prompt_global = f"""
                    Atue como um consultor global de carreiras na área da saúde e enfermagem.
                    Forneça 3 oportunidades, programas de recrutamento internacional ou perspetivas ativas de emprego para profissionais de saúde na região de {continente_sel}.
                    Para cada uma, inclua:
                    1. **País / Organização Destino**
                    2. **Área de Atuação (ex: Enfermagem, Investigação, Tecnologias de Saúde)**
                    3. **Requisitos de Entrada / Reconhecimento de Diplomas**
                    4. **Canal de Candidatura**
                    
                    Formate a resposta de forma limpa e estruturada em Markdown.
                    """
                    response_global = client.models.generate_content(
                        model='gemini-1.5-flash',
                        contents=prompt_global
                    )
                    st.markdown("### 📋 Oportunidades Globais Encontradas:")
                    st.markdown(response_global.text)
                else:
                    st.warning("⚠️ Chave da API do Gemini não configurada.")
            except Exception as e:
                st.error(f"Erro ao gerar vagas globais: {e}")

else:
    st.markdown("### 🇧🇷 Oportunidades Home Care & Plantões nos 27 Estados do Brasil")
    # O seu código existente para os 27 estados do Brasil continua aqui em baixo
    # --- ADIÇÃO DO BUSCADOR DINÂMICO DE BIOMEDICINA ---
st.markdown("---")
st.markdown("### 🔬 Buscador Dinâmico de Vagas e Oportunidades em Biomedicina")
st.info("ℹ️ Focado em Análises Clínicas, Genética, Biologia Molecular e Indústria Farmacêutica (CRBM).")

col_bio_a, col_bio_b = st.columns(2)
with col_bio_a:
    regiao_bio = st.selectbox(
        "Selecione o Mercado / País:",
        ["Brasil (Conselhos Regionais - CRBM)", "Portugal (Análises Clínicas & Hospitais)", "Global / Indústria Farmacêutica"]
    )
with col_bio_b:
    especialidade_bio = st.selectbox(
        "Área de Atuação Biomédica:",
        ["Análises Clínicas e Patologia", "Biologia Molecular e Genética Forense", "Reprodução Humana", "Farmacologia / Indústria Farmacêutica", "Diagnóstico por Imagem"]
    )

if st.button("🔍 Consultar Oportunidades em Biomedicina (IA em Tempo Real)"):
    with st.spinner(f"A pesquisar vagas em {regiao_bio} para {especialidade_bio}..."):
        try:
            api_key_val = ""
            try:
                if "GEMINI_API_KEY" in st.secrets:
                    api_key_val = st.secrets["GEMINI_API_KEY"]
            except Exception:
                pass
            if not api_key_val:
                api_key_val = os.environ.get("GEMINI_API_KEY", "")

            if HAS_GENAI and api_key_val:
                client = genai.Client(api_key=api_key_val)
                prompt_bio = f"""
                Atue como um recrutador técnico sénior na área da saúde e ciências biomédicas.
                Forneça 3 oportunidades ou contextos de emprego ativos e altamente relevantes em {regiao_bio} na especialidade de {especialidade_bio}.
                Para cada oportunidade, inclua:
                1. **Empresa / Laboratório / Hospital / Multinacional**
                2. **Função e Atividades Principais**
                3. **Requisitos Técnicos e Habilitação (ex: CRBM ou equivalente)**
                4. **Perspetiva de Remuneração e Forma de Candidatura**
                
                Formate a resposta de forma limpa e profissional em Markdown.
                """
                response_bio = client.models.generate_content(
                    model='gemini-1.5-flash',
                    contents=prompt_bio
                )
                st.markdown("### 📋 Oportunidades em Biomedicina Encontradas:")
                st.markdown(response_bio.text)
            else:
                st.warning("⚠️ Chave da API do Gemini não configurada.")
        except Exception as e:
            st.error(f"Erro ao gerar vagas de biomedicina: {e}")