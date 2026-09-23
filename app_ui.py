import io
import re
import unicodedata
import streamlit as st
import streamlit.components.v1 as components
from models import Job, UserProfile, UserSubscription
from pypdf import PdfReader
from sqlmodel import Session, create_engine, select

sqlite_url = "sqlite:///vagas_enfermagem.db"
engine = create_engine(sqlite_url, echo=False)

st.set_page_config(
    page_title="Vagas Enfermagem Belém 💕", page_icon="🎀", layout="wide"
)

# --- FUNÇÕES AUXILIARES DE PROCESSAMENTO E MATCH ---


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
      extraido = pagina.extract_text()
      if extraido:
        texto += extraido + " "
    return texto
  except Exception as e:
    return ""


PALAVRAS_CHAVE_ENFERMAGEM = [
    "uti",
    "centro cirurgico",
    "urgencia",
    "emergencia",
    "pediatria",
    "neonatal",
    "hemodialise",
    "oncologia",
    "pronto socorro",
    "coren",
    "tecnico de enfermagem",
    "enfermeiro",
    "enfermeira",
    "assistencial",
    "home care",
    "clinica medica",
    "triagem",
    "medicacao",
    "curativo",
    "auditoria",
    "saude da familia",
    "cc",
    "bloco cirurgico",
]


def extrair_keywords(texto: str) -> list:
  t_norm = normalizar_texto(texto)
  encontradas = set()
  for kw in PALAVRAS_CHAVE_ENFERMAGEM:
    if re.search(r"\b" + re.escape(kw) + r"\b", t_norm):
      encontradas.add(kw)
  return list(encontradas)


def calcular_match(vaga: Job, perfil_keywords: list) -> int:
  if not perfil_keywords:
    return 0
  texto_vaga = normalizar_texto(
      f"{vaga.title} {vaga.description} {vaga.specialty} {vaga.hospital_or_company}"
  )
  acertos = 0
  for kw in perfil_keywords:
    if kw in texto_vaga:
      acertos += 1
  score = int((acertos / max(len(perfil_keywords), 1)) * 100)
  return min(score * 2, 100)  # Ponderação para valorizar matches parciais


# --- ESTILIZAÇÃO CSS (TEMA ROSA / HELLO KITTY / ALTO CONTRASTE) ---
st.markdown(
    """
<style>
    .stApp {
        background-color: #FFF6F8;
    }
    
    [data-testid="stSidebar"] {
        background-color: #FF85A2 !important;
        border-right: 2px solid #FF5C8A;
    }

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

    /* Cartão da vaga */
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
        margin: 14px 0 16px 0;
        color: #9C27B0;
        font-size: 0.88rem;
        font-style: italic;
    }

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
    }

    /* Estilo do expander */
    [data-testid="stExpander"] {
        background-color: #FFFFFF !important;
        border: 2px solid #FF80A0 !important;
        border-radius: 14px !important;
        box-shadow: 0 4px 10px rgba(255, 105, 180, 0.15) !important;
        margin-bottom: 16px;
    }
    [data-testid="stExpander"] summary {
        background-color: #FFE6EE !important;
        padding: 12px 16px !important;
    }
    [data-testid="stExpander"] summary * {
        color: #C2185B !important;
        font-weight: 700 !important;
        fill: #C2185B !important;
        font-size: 1rem !important;
    }
</style>
""",
    unsafe_allow_html=True,
)

# --- CABEÇALHO ---
col_img, col_title = st.columns([1, 7])
with col_img:
  st.markdown(
      """
    <div style="text-align: center; margin-top: 5px;">
        <img src="https://upload.wikimedia.org/wikipedia/en/0/05/Hello_kitty_character_portrait.png" 
             style="width: 82px; height: auto; border-radius: 12px; filter: drop-shadow(0 4px 6px rgba(255,105,180,0.3));">
    </div>
    """,
      unsafe_allow_html=True,
  )

with col_title:
  st.markdown(
      "<h1 style='color: #C2185B; margin-bottom: 0;'>Portal de Vagas de"
      " Enfermagem 💕</h1>",
      unsafe_allow_html=True,
  )
  st.markdown(
      "<p style='color: #880E4F; font-size: 1.05rem;'>Feito com amor para você"
      " encontrar a sua vaga dos sonhos em Belém 🌸</p>",
      unsafe_allow_html=True,
  )

st.divider()

# --- ATIVAÇÃO DE NOTIFICAÇÃO BROWSER (POP-UP NO DISPOSITIVO) ---
st.markdown("##### 🔔 Alertas Rápidos no Seu Navegador")
col_notif1, col_notif2 = st.columns([2, 5])
with col_notif1:
  if st.button("Ativar Notificações no Celular / PC 📲"):
    components.html(
        """
        <script>
            if (!("Notification" in window)) {
                alert("Seu navegador não suporta notificações de tela.");
            } else if (Notification.permission === "granted") {
                new Notification("Vagas Enfermagem Belém 💕", {
                    body: "Notificações ativadas! Sempre que abrir o app, novas vagas serão avisadas aqui.",
                    icon: "https://upload.wikimedia.org/wikipedia/en/0/05/Hello_kitty_character_portrait.png"
                });
            } else {
                Notification.requestPermission().then(function (permission) {
                    if (permission === "granted") {
                        new Notification("Vagas Enfermagem Belém 💕", {
                            body: "Notificações ativadas com sucesso, meu bem! ✨",
                            icon: "https://upload.wikimedia.org/wikipedia/en/0/05/Hello_kitty_character_portrait.png"
                        });
                    }
                });
            }
        </script>
        """,
        height=0,
    )
    st.success(
        "Permissão solicitada! Confirme no alerta do seu navegador para"
        " receber."
    )

with col_notif2:
  st.caption(
      "Clique para permitir alertas de novas vagas diretamente na barra de"
      " notificações do seu telefone ou computador."
  )

# --- EXPANDER: CONFIGURAR RECEBIMENTO POR E-MAIL ---
with st.expander(
    "💌 Receber alertas de vagas automaticamente no seu E-mail"
):
  st.markdown(
      "Cadastre o seu e-mail para que o robô envie avisos automáticos toda vez"
      " que um hospital de Belém abrir oportunidades!"
  )
  with st.form("form_inscricao_email"):
    email_cad = st.text_input("Seu E-mail", placeholder="exemplo@gmail.com")
    btn_sub = st.form_submit_button("Cadastrar E-mail 💕")
    if btn_sub:
      if "@" in email_cad and "." in email_cad:
        with Session(engine) as session:
          ja_existe = session.exec(
              select(UserSubscription).where(
                  UserSubscription.email == email_cad.strip()
              )
          ).first()
          if not ja_existe:
            sub = UserSubscription(email=email_cad.strip(), active=True)
            session.add(sub)
            session.commit()
            st.success(f"E-mail {email_cad} cadastrado com sucesso! 💕")
          else:
            st.info("Esse e-mail já está cadastrado para receber as novidades!")
      else:
        st.warning("Por favor, digite um e-mail válido.")

# --- EXPANDER: PERSONALIZAR POR CURRÍCULO (MATCH INTELIGENTE) ---
with st.expander("📄 Anexar Meu Currículo / Habilidades para Vagas Perfeitas"):
  st.markdown(
      "Faça upload do seu currículo em **PDF** ou descreva suas experiências"
      " para que as vagas mais compatíveis com você fiquem no topo!"
  )
  col_arq, col_texto = st.columns([1, 1])

  with col_arq:
    pdf_up = st.file_uploader("Upload do Currículo (PDF)", type=["pdf"])

  with col_texto:
    texto_manual = st.text_area(
        "Ou liste suas áreas de interesse (ex: UTI, Pediatria, Centro"
        " Cirúrgico, Técnico)",
        height=100,
    )

  if st.button("Salvar Perfil e Calcular Matches ✨"):
    texto_completo = ""
    if pdf_up:
      texto_completo += extrair_texto_pdf(pdf_up.getvalue()) + " "
    if texto_manual:
      texto_completo += texto_manual + " "

    kws = extrair_keywords(texto_completo)
    if kws:
      with Session(engine) as session:
        perfil = session.exec(select(UserProfile)).first()
        if not perfil:
          perfil = UserProfile(
              resume_text=texto_completo, skills_keywords=",".join(kws)
          )
        else:
          perfil.resume_text = texto_completo
          perfil.skills_keywords = ",".join(kws)
        session.add(perfil)
        session.commit()
      st.success(
          f"Perfil atualizado com sucesso! Identificamos afinidades em:"
          f" {', '.join(kws).upper()}"
      )
      st.rerun()
    else:
      st.info(
          "Não conseguimos extrair áreas específicas. Experimente escrever"
          " termos como 'UTI', 'Técnico', 'Pediatria' ou 'Cirúrgico' no campo de"
          " texto."
      )

# --- RECUPERAR KEYWORDS DO PERFIL SALVO ---
user_keywords = []
with Session(engine) as session:
  perfil_salvo = session.exec(select(UserProfile)).first()
  if perfil_salvo and perfil_salvo.skills_keywords:
    user_keywords = [
        k.strip() for k in perfil_salvo.skills_keywords.split(",") if k.strip()
    ]

# --- BARRA LATERAL COM FILTROS ---
st.sidebar.markdown("### 🎀 Filtrar Vagas")
filtro_esp = st.sidebar.selectbox(
    "Especialidade",
    [
        "Todas",
        "Enfermagem Geral",
        "Técnico de Enfermagem",
        "UTI",
        "Centro Cirúrgico",
        "Urgência/Emergência",
        "Pediatria",
    ],
)
filtro_turno = st.sidebar.selectbox(
    "Escala / Turno", ["Todos", "12x36", "Diurno", "Noturno", "A combinar"]
)

# --- CONSULTA E RANKING DAS VAGAS ---
with Session(engine) as session:
  query = select(Job)
  if filtro_esp != "Todas":
    query = query.where(Job.specialty == filtro_esp)
  if filtro_turno != "Todos":
    query = query.where(Job.shift_type == filtro_turno)

  vagas = session.exec(query.order_by(Job.created_at.desc())).all()

# Cálculo de match e ordenação (as vagas com maior afinidade vão para o topo)
vagas_com_score = []
for v in vagas:
  score = calcular_match(v, user_keywords)
  vagas_com_score.append((v, score))

if user_keywords:
  # Ordena por score decrescente
  vagas_com_score.sort(key=lambda x: x[1], reverse=True)

st.markdown(
    f"<h4 style='color: #AD1457;'>🩺 Oportunidades Encontradas:"
    f" <b>{len(vagas_com_score)}</b></h4>",
    unsafe_allow_html=True,
)

if user_keywords:
  st.markdown(
      f"🌟 *Vagas ordenadas de acordo com as afinidades do seu perfil:* `{' | '.join(user_keywords).upper()}`"
  )

if not vagas_com_score:
  st.info("Nenhuma vaga encontrada para os filtros selecionados no momento.")
else:
  for v, score in vagas_com_score:
    link = (
        v.url_apply
        if v.url_apply.startswith("http")
        else f"https://{v.url_apply}"
    )

    badge_match_html = ""
    if score > 0:
      badge_match_html = (
          f'<span class="badge-match">✨ Compatibilidade com seu Perfil:'
          f" {score}%</span><br>"
      )

    st.markdown(
        f"""
        <div class="job-card">
            {badge_match_html}
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
        """,
        unsafe_allow_html=True,
    )

# --- FORMULÁRIO MANUAL DE VAGAS ---
with st.expander("🌸 Adicionar uma nova vaga (Manual)"):
  with st.form("form_vaga_rosa"):
    titulo = st.text_input(
        "Título da Oportunidade", value="Enfermeira Assistencial"
    )
    empresa = st.text_input(
        "Hospital ou Instituição", value="Hospital Ophir Loyola"
    )
    local = st.text_input("Local", value="Belém - PA")
    especialidade = st.selectbox(
        "Área",
        [
            "Enfermagem Geral",
            "Técnico de Enfermagem",
            "UTI",
            "Centro Cirúrgico",
            "Urgência/Emergência",
            "Pediatria",
        ],
    )
    turno = st.selectbox(
        "Escala", ["12x36", "Diurno", "Noturno", "A combinar"]
    )
    url = st.text_input(
        "Link de Inscrição / Detalhes", value="https://exemplo.com"
    )
    descricao = st.text_area(
        "Descrição das Atividades e Requisitos",
        value="Atendimento assistencial e COREN-PA regular.",
    )

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
          source="Cadastro Manual",
      )
      with Session(engine) as session:
        session.add(nova)
        session.commit()
      st.success("Vaga registada com sucesso!")
      st.rerun()
      