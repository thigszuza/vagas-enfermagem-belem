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

# --- CONFIGURAÇÃO DA PÁGINA ---
st.set_page_config(
    page_title="Portal de Carreiras em Saúde & Biomedicina 💕",
    page_icon="🎀",
    layout="wide",
    initial_sidebar_state="expanded",
)

# --- INICIALIZAÇÃO DO BANCO ---
sqlite_url = "sqlite:///vagas_enfermagem.db"
engine = create_engine(sqlite_url, echo=False)
SQLModel.metadata.create_all(engine)

# --- CATÁLOGO DE VAGAS BASE ---
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
        "url_apply": "https://www.vagas.com.br", "source": "Vagas.com", "requires_graduation": False
    },
    {
        "title": "Enfermeiro(a) - Urgência e Emergência (Pronto Atendimento)",
        "hospital_or_company": "Hospital Metropolitano (HMUE)",
        "location": "BR-316, Ananindeua - PA",
        "state": "PA", "category": "Enfermagem", "shift_type": "12x36", "specialty": "Urgência/Emergência",
        "description": "Acolhimento com Classificação de Risco (Manchester), estabilização de politraumatizados e apoio em sala vermelha.",
        "url_apply": "https://www.catho.com.br", "source": "Catho", "requires_graduation": True
    },
    {
        "title": "Enfermeira Pediátrica & Neonatal",
        "hospital_or_company": "Hospital Santa Casa de Misericórdia do Pará",
        "location": "Umarizal, Belém - PA",
        "state": "PA", "category": "Enfermagem", "shift_type": "12x36", "specialty": "Pediatria",
        "description": "Cuidados assistenciais humanizados na UCI e UTI Neonatal, punção de acesso venoso periférico pediátrico e apoio ao aleitamento materno.",
        "url_apply": "https://www.infojobs.com.br", "source": "InfoJobs", "requires_graduation": True
    },
    {
        "title": "Biomédica Analista - Hematologia e Bioquímica Clínica",
        "hospital_or_company": "Laboratório Beneficente de Belém",
        "location": "Nazaré, Belém - PA",
        "state": "PA", "category": "Biomedicina", "shift_type": "Diurno", "specialty": "Análises Clínicas",
        "description": "Rotina de bancada automatizada, microscopia para contagem diferencial de leucócitos, controle de qualidade (CQI/CQE) e liberação de laudos. CRBM ativo.",
        "url_apply": "https://www.linkedin.com/jobs", "source": "LinkedIn", "requires_graduation": True
    },
    {
        "title": "Auxiliar Técnico de Coleta e Triagem Laboratorial",
        "hospital_or_company": "Laboratório Ruth Brazão",
        "location": "Batista Campos, Belém - PA",
        "state": "PA", "category": "Biomedicina", "shift_type": "Diurno", "specialty": "Coleta e Triagem",
        "description": "Punção venosa à vácuo, coleta pediátrica, centrifugação e envio de amostras biológicas. Aberto a graduandos ou recém-formados.",
        "url_apply": "https://www.glassdoor.com.br", "source": "Glassdoor", "requires_graduation": False
    },
    {
        "title": "Enfermeiro(a) - Pronto Socorro Adulto",
        "hospital_or_company": "Hospital Sancta Maggiore (Prevent Senior)",
        "location": "Pinheiros / Mooca, São Paulo - SP",
        "state": "SP", "category": "Enfermagem", "shift_type": "12x36", "specialty": "Pronto Socorro",
        "description": "Atendimento emergencial a pacientes idosos, classificação de risco, infusão de medicação de urgência e supervisão da equipe técnica nos hospitais Sancta Maggiore.",
        "url_apply": "https://carreiras.preventsenior.com.br", "source": "Vagas.com", "requires_graduation": True
    },
    {
        "title": "Enfermeira de Centro Cirúrgico & CME",
        "hospital_or_company": "Hospital Sancta Maggiore (Prevent Senior)",
        "location": "Bela Vista / Itaim, São Paulo - SP",
        "state": "SP", "category": "Enfermagem", "shift_type": "12x36", "specialty": "Centro Cirúrgico",
        "description": "Coordenação de sala operatória, cirurgia segura, protocolos anestésicos e cuidados transoperatórios com foco no paciente sênior.",
        "url_apply": "https://carreiras.preventsenior.com.br", "source": "Catho", "requires_graduation": True
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
        "url_apply": "https://www.linkedin.com/jobs", "source": "LinkedIn", "requires_graduation": False
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
        "title": "Analista de Farmacovigilância & Ensaios Clínicos",
        "hospital_or_company": "Eurofarma Laboratórios",
        "location": "Itapevi / São Paulo - SP",
        "state": "SP", "category": "Indústria Farmacêutica", "shift_type": "Comercial / Híbrido", "specialty": "Pesquisa Clínica",
        "description": "Monitoramento de eventos adversos pós-comercialização, suporte a estudos de bioequivalência e contato direto com centros de pesquisa e hospitais.",
        "url_apply": "https://eurofarma.gupy.io", "source": "Gupy Saúde", "requires_graduation": True
    },
    {
        "title": "Enfermeira de Suporte Clínico ao Paciente (PSP)",
        "hospital_or_company": "EMS Indústria Farmacêutica",
        "location": "São Paulo - SP",
        "state": "SP", "category": "Indústria Farmacêutica", "shift_type": "Comercial", "specialty": "Suporte Terapêutico",
        "description": "Orientação e treinamento a pacientes em uso de medicamentos de alta complexidade (injetáveis), adesão ao tratamento e navegação em saúde.",
        "url_apply": "https://www.glassdoor.com.br", "source": "Glassdoor", "requires_graduation": True
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
        "url_apply": "https://www.infojobs.com.br", "source": "InfoJobs", "requires_graduation": True
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

# --- CONSULTA FLEXÍVEL DAS VAGAS (SEM TRAVAR O FILTRO) ---
with Session(engine) as session:
    termo_empresa = st.session_state.get("filtro_empresa_rapido", "")
    
    # Se uma empresa estiver selecionada no botão, busca prioritariamente ela
    if termo_empresa:
        q = select(Job).where(Job.hospital_or_company.ilike(f"%{termo_empresa.strip()}%"))
        vagas_lista = session.exec(q.order_by(Job.created_at.desc())).all()
    else:
        # Se não tiver empresa selecionada, usa os filtros da barra lateral
        q = select(Job)
        if filtro_estado != "Todos os Estados":
            q = q.where(Job.state == filtro_estado[:2])
        if filtro_categoria != "Todas":
            q = q.where(Job.category == filtro_categoria)
        if filtro_portal != "Todos os Portais":
            q = q.where(Job.source == filtro_portal)
        vagas_lista = session.exec(q.order_by(Job.created_at.desc())).all()

    # Garantia de segurança: se mesmo assim vier vazio, recarrega o catálogo base
    if not vagas_lista:
        popular_catalogo_base()
        vagas_lista = session.exec(select(Job).order_by(Job.created_at.desc())).all()
        