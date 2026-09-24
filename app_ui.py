import io
import random
import re
import unicodedata
import urllib.parse
from datetime import datetime
import streamlit as st
import streamlit.components.v1 as components
from sqlmodel import Session, SQLModel, create_engine, select
from pypdf import PdfReader

from models import Job, UserProfile, UserSubscription
from scrapers_belem import ScraperHospitaisBelem

# Inicialização da Base de Dados
sqlite_url = "sqlite:///vagas_enfermagem.db"
engine = create_engine(sqlite_url, echo=False)
SQLModel.metadata.create_all(engine)

st.set_page_config(
    page_title="Vagas Enfermagem Belém 💕",
    page_icon="🎀",
    layout="wide",
    initial_sidebar_state="expanded",
)

# Toast de carinho ao abrir
frases_toasts = [
    "eu te amo ou eu te lobo <3",
    "Você vai ser uma profissional incrível! Orgulho imenso de você 💕",
    "Torcendo por cada processo seu, meu bem! ✨",
    "eu te lobo infinito <3 🐾",
]
st.toast(f"💌 {random.choice(frases_toasts)}", icon="🎀")

# --- FUNÇÕES AUXILIARES ---
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

BAIRROS_BELEM = {
    "Umarizal": ["umarizal", "doca", "domingos marreiros"],
    "Marco": ["marco", "almirante barroso", "romulo maiorana", "duque"],
    "São Brás": ["sao bras", "jose bonifacio", "gov jose malcher"],
    "Nazaré": ["nazare", "magalhaes barata", "generalissimo"],
    "Batista Campos": ["batista campos", "serzedelo correa", "padre eutiquio"],
    "Pedreira": ["pedreira", "pedro miranda", "antonio everdosa"],
    "Ananindeua": ["ananindeua", "br-316", "cidade nova", "coqueiro"],
}

def detectar_bairro(texto_completo: str) -> str:
    t = normalizar_texto(texto_completo)
    for bairro, aliases in BAIRROS_BELEM.items():
        for alias in aliases:
            if alias in t:
                return bairro
    return "Belém (Centro/Geral)"

PALAVRAS_CHAVE = [
    "uti", "centro cirurgico", "urgencia", "emergencia", "pediatria",
    "neonatal", "hemodialise", "oncologia", "pronto socorro", "coren",
    "tecnico de enfermagem", "enfermeiro", "enfermeira", "assistencial",
    "home care", "clinica medica", "triagem", "medicacao", "curativo",
    "auditoria", "saude da familia"
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

def gerar_carta_apresentacao(vaga: Job, perfil_kws: list) -> str:
    areas = ", ".join(perfil_kws).upper() if perfil_kws else "Enfermagem Clínica"
    return f"""Prezada equipe de Recrutamento e Seleção do {vaga.hospital_or_company},

Venho por meio deste manifestar meu forte interesse na vaga de {vaga.title}.

Possuo sólida formação técnica e dedicação à prática assistencial humanizada, ética e segura. O meu perfil alinha-se às competências necessárias para a função, com interesse e conhecimentos consolidados nas áreas de {areas}, além do registro ativo junto ao COREN-PA.

Acompanho o trabalho do {vaga.hospital_or_company} e tenho grande motivação em integrar o corpo assistencial da instituição, contribuindo ativamente para a excelência do cuidado ao paciente e o bom fluxo da unidade.

Agradeço a atenção e coloco-me à inteira disposição para entrevista e avaliação curricular detalhada.

Atenciosamente,
Candidata à Vaga de Enfermagem | Belém - PA"""

# --- ESTILIZAÇÃO CSS COM TEXTO NÍTIDO NOS EXPANDERS ---
st.markdown("""
<style>
/* Estilização para embranquecer campos de texto (input) */
    div[data-baseweb="input"] > div {
        background-color: #FFFFFF !important;
        border: 2px solid #FFCCD7 !important;
        border-radius: 12px !important;
        color: #333333 !important;
    }
    div[data-baseweb="input"] input {
        color: #4A1525 !important;
        font-weight: 600 !important;
        background-color: transparent !important;
    }
    div[data-baseweb="input"] input::placeholder {
        color: #A06D7C !important;
    }

    /* Estilização para transformar os botões pretos em botões claros/rosados */
    .stButton > button,
    div[data-testid="stFormSubmitButton"] > button {
        background: linear-gradient(135deg, #FF7597, #E91E63) !important;
        color: #FFFFFF !important;
        border: none !important;
        border-radius: 20px !important;
        font-weight: 700 !important;
        padding: 8px 20px !important;
        box-shadow: 0 3px 8px rgba(233, 30, 99, 0.25) !important;
        transition: all 0.2s ease-in-out !important;
    }

    .stButton > button:hover,
    div[data-testid="stFormSubmitButton"] > button:hover {
        background: linear-gradient(135deg, #FF527B, #C2185B) !important;
        transform: translateY(-1px) !important;
        box-shadow: 0 5px 12px rgba(233, 30, 99, 0.35) !important;
    }

    /* Remove o fundo preto do pequeno bloco de código ou reticências */
    code {
        background-color: #FFE6EE !important;
        color: #C2185B !important;
        font-weight: bold !important;
        border-radius: 6px !important;
        padding: 2px 6px !important;
    }
    
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

    .job-card {
        background: #FFFFFF;
        border: 2px solid #FFCCD7;
        border-radius: 18px;
        padding: 22px;
        margin-bottom: 20px;
        box-shadow: 0 4px 14px rgba(255, 182, 193, 0.28);
        transition: transform 0.2s ease, border-color 0.2s ease;
    }
    .job-card:hover {
        transform: translateY(-2px);
        border-color: #FF85A2;
        box-shadow: 0 6px 18px rgba(255, 154, 179, 0.35);
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
    .badge-bairro {
        background-color: #E8EAF6;
        color: #283593;
        padding: 4px 12px;
        border-radius: 14px;
        font-size: 0.82rem;
        font-weight: 700;
        margin-right: 6px;
    }
    .badge-piso {
        background-color: #E8F5E9;
        color: #2E7D32;
        border: 1px solid #A5D6A7;
        padding: 4px 10px;
        border-radius: 14px;
        font-size: 0.82rem;
        font-weight: bold;
        display: inline-block;
        margin-right: 6px;
    }
    .badge-match {
        background: linear-gradient(135deg, #FF1493, #D81B60);
        color: white !important;
        padding: 5px 14px;
        border-radius: 14px;
        font-size: 0.85rem;
        font-weight: bold;
        display: inline-block;
        margin-bottom: 8px;
    }
    .love-box {
        background-color: #FFF0F5;
        border-left: 4px solid #FF69B4;
        border-radius: 8px;
        padding: 10px 14px;
        margin: 12px 0;
        color: #9C27B0;
        font-size: 0.88rem;
        font-style: italic;
    }
    .apply-btn {
        display: inline-block;
        background: linear-gradient(135deg, #FF69B4, #FF1493);
        color: white !important;
        padding: 8px 18px;
        border-radius: 18px;
        text-decoration: none !important;
        font-weight: bold;
        font-size: 0.88rem;
    }
    .map-btn {
        display: inline-block;
        background: #FCE4EC;
        color: #C2185B !important;
        padding: 8px 14px;
        border-radius: 18px;
        text-decoration: none !important;
        font-weight: 600;
        font-size: 0.88rem;
        border: 1px solid #F8BBD0;
        margin-left: 6px;
    }

    [data-testid="stExpander"] {
        background-color: #FFFFFF !important;
        border: 2px solid #FF80A0 !important;
        border-radius: 14px !important;
        margin-bottom: 14px;
    }
    [data-testid="stExpander"] summary {
        background-color: #FFE6EE !important;
        padding: 12px 16px !important;
        color: #C2185B !important;
        font-weight: 700 !important;
    }
    [data-testid="stExpander"] summary * {
        color: #C2185B !important;
        font-weight: 700 !important;
    }
    [data-testid="stExpander"] [data-testid="stExpanderDetails"],
    [data-testid="stExpander"] [data-testid="stExpanderDetails"] * {
        color: #4A1525 !important;
        font-weight: 500 !important;
    }
    [data-testid="stExpander"] [data-testid="stExpanderDetails"] strong,
    [data-testid="stExpander"] [data-testid="stExpanderDetails"] b {
        color: #9C1343 !important;
        font-weight: 700 !important;
    }
    button[data-baseweb="tab"] {
        font-weight: 700 !important;
        color: #AD1457 !important;
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
    st.markdown("<h1 style='color: #C2185B; margin-bottom: 0;'>Portal de Vagas de Enfermagem 💕</h1>", unsafe_allow_html=True)
    st.markdown("<p style='color: #880E4F; font-size: 1.05rem;'>As melhores oportunidades de Belém e Ananindeua reunidas com amor para você 🌸</p>", unsafe_allow_html=True)

st.divider()

# --- MENSAGEM FOFA NO TOPO ---
st.markdown("""
<div style="
    background: linear-gradient(90deg, #FFE4EC, #FFF0F5);
    border: 1px dashed #FF69B4;
    border-radius: 12px;
    padding: 10px 16px;
    text-align: center;
    color: #C2185B;
    font-size: 0.95rem;
    font-weight: 600;
    margin-bottom: 18px;
    box-shadow: 0 2px 8px rgba(255,105,180,0.15);
">
    🐾 <i>eu te amo ou eu te lobo &lt;3</i> ✨
</div>
""", unsafe_allow_html=True)

# --- BARRA LATERAL ---
st.sidebar.markdown("### 🎀 Filtros & Pesquisa")
busca_livre = st.sidebar.text_input("🔍 Busca rápida", placeholder="Ex: Ophir, UTI, Unimed, Pediatria...")
filtro_esp = st.sidebar.selectbox(
    "Especialidade",
    ["Todas", "Enfermagem Geral", "Técnico de Enfermagem", "UTI", "Centro Cirúrgico", "Urgência/Emergência", "Pediatria"]
)
filtro_turno = st.sidebar.selectbox(
    "Escala / Turno",
    ["Todos", "12x36", "Diurno", "Noturno", "A combinar"]
)

st.sidebar.markdown("---")
biscoitos = [
    "O seu foco e cuidado com a saúde salvam vidas todos os dias! 🩺",
    "Respira fundo: a sua vaga ideal em Belém já está a caminho! 🌸",
    "Você é mais forte, inteligente e capaz do que imagina. Tenho muito orgulho! 💕",
    "Quem tem a sua dedicação vai longe em qualquer hospital! ✨",
]
st.sidebar.markdown("##### 🥠 Biscoito do Dia p/ Você")
st.sidebar.info(random.choice(biscoitos))

if st.sidebar.button("🔄 Atualizar Vagas da Web Agora"):
    with st.spinner("Buscando oportunidades fresquinhas em Belém..."):
        scraper = ScraperHospitaisBelem()
        novas = scraper.coletar_todas()
        qtd = 0
        with Session(engine) as session:
            for v in novas:
                if not session.exec(select(Job).where(Job.url_apply == v["url_apply"])).first():
                    session.add(Job(**v))
                    qtd += 1
            session.commit()
        st.sidebar.success(f"{qtd} novas vagas salvas!")
        st.rerun()

# --- CONFIGURAÇÕES EXPANSORES SUPERIORES ---
col_exp1, col_exp2, col_exp3 = st.columns(3)

with col_exp1:
    with st.expander("📲 Como Instalar no Celular (PWA)"):
        st.markdown("""
        **No telemóvel:**
        1. Abra este site no Safari (iPhone) ou Chrome (Android).
        2. Clique no botão de compartilhar/opções (`...` ou seta).
        3. Escolha **'Adicionar ao ecrã principal'**.
        4. O app fica com o ícone fofo da Hello Kitty como um app nativo!
        """)

with col_exp2:
    with st.expander("🔔 Ativar Alertas no Telemóvel"):
        if st.button("Ativar Notificações 📲"):
            components.html("""
            <script>
                if (Notification.permission === "granted") {
                    new Notification("Vagas Enfermagem Belém 💕", {
                        body: "Notificações ativadas com sucesso, meu bem! ✨",
                        icon: "https://upload.wikimedia.org/wikipedia/en/0/05/Hello_kitty_character_portrait.png"
                    });
                } else {
                    Notification.requestPermission();
                }
            </script>
            """, height=0)
            st.success("Permissão solicitada no navegador!")

with col_exp3:
    with st.expander("💌 Receber Alertas por E-mail"):
        with st.form("form_email_sub"):
            em = st.text_input("Seu E-mail", placeholder="exemplo@gmail.com")
            if st.form_submit_button("Cadastrar"):
                if "@" in em:
                    with Session(engine) as s:
                        if not s.exec(select(UserSubscription).where(UserSubscription.email == em)).first():
                            s.add(UserSubscription(email=em))
                            s.commit()
                    st.success("E-mail registrado!")
                else:
                    st.warning("E-mail inválido.")

# --- ÁREA DO CURRÍCULO E KEYWORDS ---
with st.expander("📄 Anexar Meu Currículo para Match Inteligente"):
    col_c1, col_c2 = st.columns([1, 1])
    with col_c1:
        pdf_in = st.file_uploader("Currículo em PDF", type=["pdf"])
    with col_c2:
        txt_in = st.text_area("Ou liste suas especialidades (ex: UTI, Pediatria, Centro Cirúrgico)", height=90)
    
    if st.button("Salvar Perfil e Calcular Afinidades ✨"):
        tudo = ""
        if pdf_in:
            tudo += extrair_texto_pdf(pdf_in.getvalue()) + " "
        if txt_in:
            tudo += txt_in + " "
        kws = extrair_keywords(tudo)
        if kws:
            with Session(engine) as s:
                perf = s.exec(select(UserProfile)).first()
                if not perf:
                    perf = UserProfile(resume_text=tudo, skills_keywords=",".join(kws))
                else:
                    perf.skills_keywords = ",".join(kws)
                s.add(perf)
                s.commit()
            st.success(f"Perfil atualizado! Foco em: {', '.join(kws).upper()}")
            st.rerun()

user_keywords = []
with Session(engine) as session:
    p = session.exec(select(UserProfile)).first()
    if p and p.skills_keywords:
        user_keywords = [k.strip() for k in p.skills_keywords.split(",") if k.strip()]

# --- CONSULTA DAS VAGAS ---
with Session(engine) as session:
    q = select(Job)
    if busca_livre:
        t = f"%{busca_livre.strip()}%"
        q = q.where(
            (Job.title.ilike(t)) | 
            (Job.hospital_or_company.ilike(t)) | 
            (Job.description.ilike(t))
        )
    if filtro_esp != "Todas":
        q = q.where(Job.specialty == filtro_esp)
    if filtro_turno != "Todos":
        q = q.where(Job.shift_type == filtro_turno)
        
    vagas_db = session.exec(q.order_by(Job.created_at.desc())).all()

# --- SISTEMA DE ABAS ---
tab_vagas, tab_candidaturas, tab_dicas = st.tabs(["🌸 Vagas Disponíveis", "📋 Minhas Candidaturas", "💡 Guia Salarial & Apoio"])

# ================= TAB 1: VAGAS =================
with tab_vagas:
    rankeadas = [(v, calcular_match(v, user_keywords)) for v in vagas_db]
    if user_keywords:
        rankeadas.sort(key=lambda x: x[1], reverse=True)
        st.markdown(f"🌟 *Vagas ordenadas pelo seu perfil:* `{' | '.join(user_keywords).upper()}`")
    
    st.markdown(f"<h4 style='color: #AD1457;'>🩺 Oportunidades Encontradas: <b>{len(rankeadas)}</b></h4>", unsafe_allow_html=True)
    
    if not rankeadas:
        st.info("Nenhuma oportunidade encontrada com esses filtros.")
    else:
        for vaga, score in rankeadas:
            link = vaga.url_apply if vaga.url_apply.startswith("http") else f"https://{vaga.url_apply}"
            bairro = detectar_bairro(f"{vaga.location} {vaga.hospital_or_company} {vaga.description}")
            gmaps_url = f"https://www.google.com/maps/search/{urllib.parse.quote(f'{vaga.hospital_or_company} {bairro} Belém')}"
            
            badge_match = f'<span class="badge-match">✨ Compatibilidade: {score}%</span><br>' if score > 0 else ""
            badge_piso = '<span class="badge-piso">⚖️ Piso Salarial Nacional Assegurado</span>'

            st.markdown(f"""
            <div class="job-card">
                {badge_match}
                <div class="job-title">💖 {vaga.title}</div>
                <div class="job-meta">
                    🏥 <b>{vaga.hospital_or_company}</b> &nbsp;•&nbsp; 📍 {vaga.location} &nbsp;•&nbsp;
                    <b>Status:</b> 🏷️ {getattr(vaga, 'status', 'Disponível') or 'Disponível'}
                </div>
                <div>
                    <span class="badge-bairro">📍 {bairro}</span>
                    <span class="badge">🏷️ {vaga.specialty}</span>
                    <span class="badge">⏰ {vaga.shift_type}</span>
                    {badge_piso}
                </div>
                <p style="color: #4A4A4A; margin-top: 10px; font-size: 0.94rem; line-height: 1.45;">
                    {vaga.description}
                </p>
                <div class="love-box">
                    💌 Amor, fiz com carinho esse app pra você, sinal do meu amor pra você. Me avise se tiver algum erro!
                </div>
                <a href="{link}" target="_blank" class="apply-btn">Acessar Vaga 🔗</a>
                <a href="{gmaps_url}" target="_blank" class="map-btn">📍 Rota no Google Maps</a>
            </div>
            """, unsafe_allow_html=True)

            c_bt1, c_bt2, c_bt3, c_bt4 = st.columns([1.5, 1.8, 1.8, 2.5])
            with c_bt1:
                if st.button("❤️ Favoritar", key=f"f_{vaga.id}"):
                    with Session(engine) as s:
                        obj = s.get(Job, vaga.id)
                        obj.status = "Favorita"
                        s.add(obj)
                        s.commit()
                    st.rerun()
            with c_bt2:
                if st.button("📩 Candidatada", key=f"c_{vaga.id}"):
                    with Session(engine) as s:
                        obj = s.get(Job, vaga.id)
                        obj.status = "Candidatada"
                        s.add(obj)
                        s.commit()
                    st.rerun()
            with c_bt3:
                if st.button("🎉 Entrevista!", key=f"e_{vaga.id}"):
                    with Session(engine) as s:
                        obj = s.get(Job, vaga.id)
                        obj.status = "Entrevista"
                        s.add(obj)
                        s.commit()
                    st.rerun()
            with c_bt4:
                with st.popover("✨ Gerar Carta de Apresentação"):
                    st.caption("Pronto para copiar e colar no Gupy ou enviar ao RH:")
                    carta = gerar_carta_apresentacao(vaga, user_keywords)
                    st.text_area("Texto Gerado:", carta, height=180, key=f"txt_{vaga.id}")

# ================= TAB 2: MINHAS CANDIDATURAS =================
with tab_candidaturas:
    st.markdown("### 📋 Painel de Candidaturas e Favoritas")
    with Session(engine) as session:
        todas = session.exec(select(Job)).all()
        acompanhadas = [
            v for v in todas 
            if getattr(v, "status", None) in ["Favorita", "Candidatada", "Entrevista", "Aguardando"]
        ]

    if not acompanhadas:
        st.info("Nenhuma vaga marcada ainda. Clique em '❤️ Favoritar' ou '📩 Candidatada' nos cartões da aba de vagas!")
    else:
        for cand in acompanhadas:
            with st.container():
                st.markdown(f"**💖 {cand.title}** no **{cand.hospital_or_company}** — Bairro: *{detectar_bairro(cand.hospital_or_company)}*")
                col_st1, col_st2, col_st3 = st.columns([2, 3, 2])
                with col_st1:
                    status_atuais = ["Favorita", "Candidatada", "Aguardando", "Entrevista"]
                    idx_status = status_atuais.index(cand.status) if cand.status in status_atuais else 0
                    novo_st = st.selectbox(
                        "Status:",
                        status_atuais,
                        index=idx_status,
                        key=f"sel_st_{cand.id}"
                    )
                    if novo_st != cand.status:
                        with Session(engine) as s:
                            o = s.get(Job, cand.id)
                            o.status = novo_st
                            s.add(o)
                            s.commit()
                        st.rerun()
                with col_st2:
                    st.caption(f"Cadastrada em: {cand.created_at.strftime('%d/%m/%Y às %H:%M')}")
                with col_st3:
                    if st.button("Remover da Lista", key=f"rm_{cand.id}"):
                        with Session(engine) as s:
                            o = s.get(Job, cand.id)
                            o.status = "Disponível"
                            s.add(o)
                            s.commit()
                        st.rerun()
                st.divider()

# ================= TAB 3: DICAS & PISO SALARIAL =================
with tab_dicas:
    st.markdown("### 💡 Guia de Apoio & Piso Salarial Nacional")
    col_p1, col_p2 = st.columns(2)
    with col_p1:
        st.markdown("""
        #### ⚖️ Valores de Referência do Piso:
        * **Enfermeiro(a):** R$ 4.750,00 (base 44h semanais)
        * **Técnico(a) de Enfermagem:** R$ 3.325,00 (70% do piso)
        * **Auxiliar de Enfermagem:** R$ 2.375,00 (50% do piso)
        
        *Obs: Em regimes 12x36 ou 30h/semanais, o cálculo é feito proporcionalmente às horas contratadas.*
        """)
    with col_p2:
        st.markdown("""
        #### 🏥 Dica de Deslocamento em Belém:
        * **Polos Hospitalares:** A maior concentração de hospitais está nos eixos **Umarizal / Nazaré / Marco** (Av. Almirante Barroso).
        * **Troca de Plantão 12x36:** Se entrar às 07:00 ou 19:00, calcule 30 min adicionais para o trânsito da Almirante Barroso e BR-316.
        """)

# --- CADASTRO MANUAL DE VAGAS ---
with st.expander("🌸 Adicionar Nova Vaga Manualmente"):
    with st.form("form_add_vaga", clear_on_submit=True):
        t_man = st.text_input("Título da Oportunidade")
        h_man = st.text_input("Hospital ou Clínica")
        l_man = st.text_input("Localização / Bairro", value="Belém - PA")
        e_man = st.selectbox("Área", ["Enfermagem Geral", "Técnico de Enfermagem", "UTI", "Centro Cirúrgico", "Urgência/Emergência", "Pediatria"])
        esc_man = st.selectbox("Escala", ["12x36", "Diurno", "Noturno", "A combinar"])
        u_man = st.text_input("Link da Vaga / Inscrição")
        d_man = st.text_area("Descrição das Funções")
        if st.form_submit_button("Salvar Vaga 💕"):
            if t_man and u_man:
                with Session(engine) as s:
                    s.add(Job(
                        title=t_man,
                        hospital_or_company=h_man or "Belém",
                        location=l_man,
                        specialty=e_man,
                        shift_type=esc_man,
                        url_apply=u_man,
                        description=d_man,
                        source="Cadastro Manual",
                        status="Disponível"
                    ))
                    s.commit()
                st.success("Vaga registrada com sucesso!")
                st.rerun()
            else:
                st.warning("Preencha ao menos o Título e o Link.")

# --- RODAPÉ COM EASTER EGG AFETIVO ---
st.divider()
col_rod1, col_rod2 = st.columns([6, 1])
with col_rod1:
    st.markdown("<div style='color: #AD1457; font-size: 0.85rem;'>Feito sob medida com todo o amor do mundo para a futura melhor enfermeira de Belém 💕 🐾</div>", unsafe_allow_html=True)
with col_rod2:
    with st.popover("🎀 Clique aqui"):
        st.markdown("""
        **Para o meu amor:**
        Não importa quanto tempo leve ou quão cansativo seja o caminho:
        estou sempre aqui torcendo por você.
        
        *eu te amo ou eu te lobo <3*
        """)
