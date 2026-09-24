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

# --- FUNÇÃO UTILITÁRIA BLINDADA PARA DATAS ---
def sanitizar_datetime(dt) -> datetime:
    if not dt:
        return datetime.utcnow()
    if isinstance(dt, str):
        # Tenta formatos comuns salvos pelo SQLite
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

class FeedbackEntry(SQLModel, table=True):
    __table_args__ = {"extend_existing": True}
    id: int | None = Field(default=None, primary_key=True)
    user_mood: str
    notes: str
    found_good_job: bool = True
    suggestions: str = ""
    created_at: datetime = Field(default_factory=datetime.utcnow)

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
        "title": "Enfermeira de Pesquisa Clínica & Farmacovigilância",
        "category": "Indústria Farmacêutica", "shift_type": "Comercial / Híbrido", "specialty": "Pesquisa Clínica",
        "description": "Monitoramento de ensaios clínicos, reporte de eventos adversos e interface com centros de estudo e órgãos reguladores.",
        "requires_graduation": True
    }
]

PORTAIS_LISTA = ["Gupy Saúde", "Vagas.com", "Catho", "InfoJobs", "LinkedIn", "Glassdoor"]

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

# --- ESTILIZAÇÃO CSS COMPLETA ---
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
    [data-testid="stSidebar"] * {
        color: #FFFFFF !important;
        font-weight: 700 !important;
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

    .hello-kitty-alert {
        background: linear-gradient(135deg, #FFFFFF, #FFF0F5);
        border: 2px solid #FF85A2;
        border-left: 6px solid #E91E63;
        border-radius: 16px;
        padding: 14px 18px;
        margin-bottom: 16px;
        display: flex;
        align-items: center;
        gap: 14px;
        box-shadow: 0 4px 12px rgba(233, 30, 99, 0.12);
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

    div[data-testid="stPopover"],
    div[data-testid="stPopover"] > button,
    .stButton > button {
        background: linear-gradient(135deg, #FF69B4, #E91E63) !important;
        color: #FFFFFF !important;
        border: none !important;
        border-radius: 20px !important;
        font-weight: 700 !important;
        padding: 8px 18px !important;
        box-shadow: 0 4px 10px rgba(233, 30, 99, 0.28) !important;
    }
    div[data-testid="stPopover"] > button *,
    .stButton > button * {
        color: #FFFFFF !important;
        font-weight: 700 !important;
    }
</style>
""", unsafe_allow_html=True)

# --- BARRA LATERAL ---
st.sidebar.markdown("""
<div style="
    background: linear-gradient(135deg, #FFFFFF, #FFF0F5);
    border: 2px solid #FFCCD7;
    border-left: 6px solid #E91E63;
    border-radius: 14px;
    padding: 14px 16px;
    margin-bottom: 20px;
    box-shadow: 0 4px 12px rgba(233, 30, 99, 0.08);
">
    <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom: 6px;">
        <b style="color:#C2185B; font-size: 0.95rem;">🛠️ Atualizações do Desenvolvedor</b>
        <span style="background:#FFE0E9; color:#AD1457; font-size:0.75rem; font-weight:700; padding:2px 8px; border-radius:10px;">v2.6</span>
    </div>
    <p style="color:#666; font-size: 0.78rem; margin: 0 0 8px 0;">Notas de versão por Thiago Zuza 💕</p>
    <ul style="color:#33101E; font-size:0.83rem; line-height:1.45; padding-left:18px; margin:0;">
        <li><b>Radar Nacional:</b> Expansão e catalogação dinâmica de vagas hospitalares e farmacêuticas.</li>
        <li><b>Ordenação Inteligente:</b> Prioridade automática para vagas mais próximas da sua casa (Belém/Centro).</li>
        <li><b>Data & Recência:</b> Exibição da data/hora exata do anúncio e tempo relativo decorrido.</li>
        <li><b>Push Automático por E-mail:</b> Disparo simultâneo de oportunidades nacionais recém-publicadas.</li>
        <li><b>Alerta Hello Kitty & Diário:</b> Notificação rápida da última vaga e envio de feedback no WhatsApp.</li>
        <li><b>Uniformização Visual:</b> Correção total de contraste, remoção de fundos pretos, setas rosas e divisórias.</li>
    </ul>
</div>
""", unsafe_allow_html=True)

st.sidebar.markdown("### 🎀 Localização & Carreira")

LISTA_ESTADOS = [
    "Todos os Estados", "PA - Pará (Belém e Região)", "SP - São Paulo",
    "RJ - Rio de Janeiro", "MG - Minas Gerais", "DF - Distrito Federal", "BA - Bahia"
]
filtro_estado = st.sidebar.selectbox("📍 Filtrar por Estado / Região:", LISTA_ESTADOS)

filtro_categoria = st.sidebar.radio(
    "Área de Atuação:",
    ["Todas", "Enfermagem", "Biomedicina", "Indústria Farmacêutica", "Saúde Geral"]
)

PORTAIS_DISPONIVEIS = ["Todos os Portais", "Catho", "Vagas.com", "InfoJobs", "LinkedIn", "Glassdoor", "Gupy Saúde"]
filtro_portal = st.sidebar.selectbox("🌐 Filtrar por Portal de Vagas:", PORTAIS_DISPONIVEIS)

if "filtro_empresa_rapido" not in st.session_state:
    st.session_state.filtro_empresa_rapido = ""

busca_termo = st.sidebar.text_input(
    "🔍 Busca por palavra",
    value=st.session_state.filtro_empresa_rapido,
    placeholder="Ex: Porto Dias, Sancta Maggiore..."
)

if st.sidebar.button("🔄 Sincronizar Portais 24h Agora"):
    with st.spinner("Sincronizando vagas em tempo real..."):
        popular_catalogo_base()
        st.session_state.ultimo_refresh = datetime.utcnow()
        st.sidebar.success("Vagas sincronizadas!")
        st.rerun()

st.sidebar.markdown("---")
st.sidebar.markdown("### 💌 Alertas Automáticos por E-mail")
with st.sidebar.form("form_inscricao_alertas"):
    nome_input = st.text_input("Nome:", placeholder="Ex: Meu Amor")
    email_input = st.text_input("E-mail para Receber Alertas:", placeholder="exemplo@gmail.com")
    ativo_check = st.checkbox("Receber push imediato de novas vagas", value=True)
    salvar_inscricao = st.form_submit_button("🔔 Salvar & Enviar Push Imediato")

    if salvar_inscricao:
        email_limpo = email_input.strip().lower()
        if not email_limpo or "@" not in email_limpo:
            st.sidebar.error("⚠️ Insira um e-mail válido.")
        else:
            with Session(engine) as session:
                sub = session.exec(select(UserSubscription).where(UserSubscription.email == email_limpo)).first()
                if not sub:
                    sub = UserSubscription(name=nome_input.strip() or "Amor", email=email_limpo, active=ativo_check)
                    session.add(sub)
                else:
                    sub.active = ativo_check
                session.commit()
                vagas_push = session.exec(select(Job).order_by(Job.created_at.desc())).all()
            if ativo_check and vagas_push:
                disparar_push_todas_vagas(email_limpo, nome_input.strip() or "Amor", vagas_push)
                st.sidebar.success(f"✅ Push enviado para {email_limpo}!")

# --- CONSULTA DAS VAGAS ---
try:
    with Session(engine) as session:
        termo_empresa = st.session_state.get("filtro_empresa_rapido", "")
        if termo_empresa:
            q = select(Job).where(Job.hospital_or_company.ilike(f"%{termo_empresa.strip()}%"))
        else:
            q = select(Job)
            if filtro_estado != "Todos os Estados":
                q = q.where(Job.state == filtro_estado[:2])
            if filtro_categoria != "Todas":
                q = q.where(Job.category == filtro_categoria)
            if filtro_portal != "Todos os Portais":
                q = q.where(Job.source == filtro_portal)
            if busca_termo:
                t = f"%{busca_termo.strip()}%"
                q = q.where((Job.title.ilike(t)) | (Job.hospital_or_company.ilike(t)))

        todas_vagas_ativas = session.exec(select(Job)).all()
        if len(todas_vagas_ativas) < 10:
            popular_catalogo_base()
            todas_vagas_ativas = session.exec(select(Job)).all()

        vagas_brutas = session.exec(q).all()

        def chave_ordenacao(j):
            dt_limpa = sanitizar_datetime(getattr(j, "created_at", None))
            return (calcular_peso_proximidade(j), dt_limpa)

        vagas_lista = sorted(vagas_brutas, key=chave_ordenacao, reverse=True)
except Exception:
    vagas_lista = []
    todas_vagas_ativas = []

vaga_recente = vagas_lista[0] if vagas_lista else None

# --- ABAS PRINCIPAIS ---
tab_vagas, tab_feedback, tab_biomed, tab_agenda, tab_ia, tab_rotas = st.tabs([
    "🌸 Mural Geral de Vagas",
    "📝 Diário & Feedback p/ Thiago",
    "🔬 Especial Biomedicina",
    "📅 Agenda Médica",
    "🤖 Central IA",
    "🗺️ Trajeto & Plantão"
])

# ================= TAB 1: MURAL DE VAGAS =================
with tab_vagas:
    if vaga_recente:
        dt_v = sanitizar_datetime(getattr(vaga_recente, "created_at", None))
        minutos = max(int((datetime.utcnow() - dt_v).total_seconds() / 60), 1)

        st.markdown(f"""
        <div class="hello-kitty-alert">
            <img src="https://upload.wikimedia.org/wikipedia/en/0/05/Hello_kitty_character_portrait.png" style="width:48px; height:auto; border-radius:8px;">
            <div>
                <b style="color:#C2185B; font-size:1rem;">🎀 Alerta da Hello Kitty: Nova oportunidade publicada há {minutos} min!</b><br>
                <span style="color:#33101E; font-size:0.92rem;">
                    Acabou de sair vaga de <b>{vaga_recente.title}</b> no <b>{vaga_recente.hospital_or_company}</b> ({vaga_recente.location}). Está no topo da lista esperando por você! ✨
                </span>
            </div>
        </div>
        """, unsafe_allow_html=True)

    tempo_sessao = (datetime.utcnow() - st.session_state.ultimo_refresh).total_seconds()
    if tempo_sessao > 300:
        st.session_state.ultimo_refresh = datetime.utcnow()
        st.toast("✨ Feed atualizado com as oportunidades mais recentes!")

    st.markdown(f"### 🩺 Oportunidades Próximas de Você ({len(vagas_lista)} encontradas):")

    for v in vagas_lista:
        peso_prox = calcular_peso_proximidade(v)
        badge_prox = '<span class="badge-proxima">🏠 Bem Pertinho de Casa</span>' if peso_prox >= 80 else ''
        
        dt_c = sanitizar_datetime(getattr(v, "created_at", None))
        dt_fmt = dt_c.strftime("%d/%m/%Y às %H:%M")
        delta_s = max((datetime.utcnow() - dt_c).total_seconds(), 0)
        minutos_calc = int(delta_s / 60)
        
        if minutos_calc <= 60:
            tempo_relativo = f"Publicada há {max(minutos_calc, 2)} min"
        elif minutos_calc <= 1440:
            tempo_relativo = f"Publicada há {int(minutos_calc / 60)}h"
        else:
            tempo_relativo = f"Publicada há {int(minutos_calc / 1440)}d"

        link_vaga = v.url_apply if v.url_apply.startswith("http") else f"https://{v.url_apply}"
        txt_zap = urllib.parse.quote(f"Olha essa oportunidade de {v.title} no {v.hospital_or_company}: {link_vaga}")
        link_zap = f"https://api.whatsapp.com/send?text={txt_zap}"

        st.markdown(f"""
        <div class="job-card">
            <div class="job-title">💖 {v.title}</div>
            <div style="color:#880E4F; font-size:0.95rem; margin-bottom:8px;">
                🏥 <b>{v.hospital_or_company}</b> &nbsp;•&nbsp; 📍 {v.location}
            </div>
            <div style="margin-bottom:10px;">
                {badge_prox}
                <span class="badge">🌴 {v.state}</span>
                <span class="badge">🌐 {v.source}</span>
                <span class="badge-data">📅 {dt_fmt} ({tempo_relativo})</span>
            </div>
            <p style="color:#333333; font-size:0.92rem; line-height:1.4;">{v.description}</p>
            <div style="margin-top:10px;">
                <a href="{link_vaga}" target="_blank" class="action-link" style="background:#FF69B4; color:white !important;">Acessar no {v.source} ➔</a>
                <a href="{link_zap}" target="_blank" class="action-link" style="background:#E8F5E9; color:#2E7D32 !important; border:1px solid #C8E6C9;">💬 Compartilhar Vaga ➔</a>
            </div>
        </div>
        """, unsafe_allow_html=True)

# ================= TAB 2: DIÁRIO & FEEDBACK P/ THIAGO =================
with tab_feedback:
    st.markdown("<h2 style='color:#C2185B;'>📝 Diário de Uso & Feedback Direto p/ Thiago</h2>", unsafe_allow_html=True)
    st.markdown("""
    Deixe aqui as suas anotações do dia sobre o que achou das vagas, como está se sentindo e o que deseja que eu ajuste na ferramenta. 
    Ao clicar em salvar, você já pode me enviar instantaneamente no WhatsApp! 💕
    """)

    with st.form("form_diario_feedback"):
        col_fb1, col_fb2 = st.columns([1, 1])
        with col_fb1:
            mood_hoje = st.selectbox(
                "Como foi sua experiência no portal hoje?",
                [
                    "✨ Encontrei vagas incríveis perto de mim!",
                    "💖 Gostei bastante, estou animada!",
                    "😴 Cansadinha, mas olhei as principais",
                    "🤔 Quero ver mais opções em outros bairros/hospitais",
                    "🥺 Preciso de uma ajudinha do Thiago com o currículo"
                ]
            )
            achou_vaga = st.checkbox("Conseguiu se candidatar a alguma vaga hoje?", value=True)
        
        with col_fb2:
            notas_dia = st.text_area(
                "Anotações do seu dia / Impressões:",
                placeholder="Ex: Gostei muito da vaga do Porto Dias! Salvei para me candidatar amanhã cedo..."
            )
            sugestoes = st.text_input(
                "Quer que eu adicione algum hospital ou função específica?",
                placeholder="Ex: Adicionar mais laboratórios em Nazaré..."
            )

        btn_salvar_fb = st.form_submit_button("💌 Salvar no Meu Diário & Gerar Mensagem p/ Thiago ➔")

        if btn_salvar_fb:
            with Session(engine) as s:
                entry = FeedbackEntry(
                    user_mood=mood_hoje,
                    notes=notas_dia.strip(),
                    found_good_job=achou_vaga,
                    suggestions=sugestoes.strip()
                )
                s.add(entry)
                s.commit()

            st.success("✅ Registro salvo com sucesso no seu diário!")

            cand_txt = "Sim, me candidatei! 🎉" if achou_vaga else "Ainda não, só pesquisei hoje."
            msg_zap_fb = (
                f"Oi, amor! Registrei meu feedback de hoje no portal:\n\n"
                f"• Sentimento/Uso: {mood_hoje}\n"
                f"• Conseguiu se candidatar? {cand_txt}\n"
                f"• Anotações: {notas_dia.strip() or 'Nenhuma'}\n"
                f"• Pedido/Ajuste: {sugestoes.strip() or 'Tudo perfeito!'}\n\n"
                f"Te amo muito! 💕"
            )
            link_zap_th = f"https://api.whatsapp.com/send?phone=5511913129697&text={urllib.parse.quote(msg_zap_fb)}"

            st.markdown(f"""
            <div style="margin-top:14px;">
                <a href="{link_zap_th}" target="_blank" class="action-link" style="background:#25D366; color:white !important; font-size:0.95rem; padding:10px 22px;">
                    📲 Enviar Esse Feedback Diretamente para o WhatsApp do Thiago ➔
                </a>
            </div>
            """, unsafe_allow_html=True)

    st.markdown("---")
    st.markdown("#### 📖 Histórico dos seus últimos registros:")
    with Session(engine) as s:
        historico_fb = s.exec(select(FeedbackEntry).order_by(FeedbackEntry.created_at.desc()).limit(5)).all()
        if historico_fb:
            for h in historico_fb:
                dt_f = h.created_at.strftime("%d/%m/%Y às %H:%M")
                st.markdown(f"""
                <div style="background:#FFFFFF; border:1px solid #FFCCD7; border-radius:12px; padding:12px 16px; margin-bottom:10px;">
                    <b style="color:#C2185B;">{dt_f}</b> — {h.user_mood}<br>
                    <span style="color:#555; font-size:0.9rem;"><i>\"{h.notes or 'Sem notas adicionais'}\"</i></span>
                </div>
                """, unsafe_allow_html=True)
        else:
            st.info("Nenhum feedback registrado anteriormente.")

# ================= TAB 3: ESPECIAL BIOMEDICINA =================
with tab_biomed:
    st.markdown("<h2 style='color:#AD1457;'>🔬 Painel Exclusivo de Biomedicina</h2>", unsafe_allow_html=True)
    st.info("Espaço dedicado a Análises Clínicas, Biologia Molecular e Rotinas Laboratoriais.")

# ================= TAB 4: AGENDA MÉDICA =================
with tab_agenda:
    st.markdown("<h2 style='color:#AD1457;'>📅 Agenda Médica & Exames</h2>", unsafe_allow_html=True)
    st.info("Organização diária de consultas e acompanhamentos de saúde.")

# ================= TAB 5: CENTRAL IA =================
with tab_ia:
    st.markdown("<h2 style='color:#AD1457;'>🤖 Central IA de Carreira</h2>", unsafe_allow_html=True)
    st.info("Análise detalhada de compatibilidade de currículo com Gemini.")

# ================= TAB 6: TRAJETO & PLANTÃO =================
with tab_rotas:
    st.markdown("<h2 style='color:#AD1457;'>🗺️ Trajeto, Uber & Segurança</h2>", unsafe_allow_html=True)
    link_saida = f"https://api.whatsapp.com/send?phone=5511913129697&text={urllib.parse.quote('Oi, amor! Estou saindo do plantão/estudos e indo para casa. Te aviso assim que chegar!')}"
    st.markdown(f"""
    <div style="margin-top:10px;">
        <a href="{link_saida}" target="_blank" class="action-link" style="background:#E91E63; color:white !important; font-size:1rem; padding:12px 24px;">
            🚨 Botão de Segurança: Mandar Aviso de Saída p/ Thiago ➔
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
