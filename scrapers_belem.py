import unicodedata
from bs4 import BeautifulSoup
import requests


def normalizar_texto(texto: str) -> str:
  if not texto:
    return ""
  nfkd = unicodedata.normalize("NFKD", texto)
  return "".join([c for c in nfkd if not unicodedata.combining(c)]).lower()


def extrair_turno(texto: str):
  t = normalizar_texto(texto)
  if "12x36" in t:
    return "12x36"
  if "noturno" in t:
    return "Noturno"
  if "diurno" in t:
    return "Diurno"
  return "A combinar"


def extrair_especialidade(texto: str):
  t = normalizar_texto(texto)
  if "uti" in t:
    return "UTI"
  if "cirurgico" in t or "cc" in t:
    return "Centro Cirúrgico"
  if "urgencia" in t or "emergencia" in t or "pronto socorro" in t:
    return "Urgência/Emergência"
  if "pediatria" in t or "neonatal" in t:
    return "Pediatria"
  return "Geral"


class ScraperHospitaisBelem:

  def __init__(self):
    self.headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
            " (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        )
    }

  def scrape_gupy(self, subdomain: str, empresa: str):
    vagas = []
    # Busca com termo direto de enfermagem na API
    url = f"https://{subdomain}.gupy.io/api/v1/jobs?jobName=enfermagem&limit=50"
    try:
      resp = requests.get(url, headers=self.headers, timeout=10)
      if resp.status_code == 200:
        for item in resp.json().get("data", []):
          cidade = normalizar_texto(item.get("city", ""))
          titulo = item.get("name", "")
          t_norm = normalizar_texto(titulo)

          if "belem" in cidade or "ananindeua" in cidade or not cidade:
            desc = item.get("description", "")
            vagas.append({
                "title": titulo,
                "hospital_or_company": empresa,
                "location": f"{item.get('city') or 'Belém'} - PA",
                "shift_type": extrair_turno(f"{titulo} {desc}"),
                "specialty": extrair_especialidade(f"{titulo} {desc}"),
                "salary": None,
                "description": (
                    BeautifulSoup(desc, "html.parser").get_text()[:300] + "..."
                ),
                "url_apply": item.get("jobUrl"),
                "source": f"Gupy ({empresa})",
            })
    except Exception as e:
      print(f"Erro ao recolher de {empresa}: {e}")
    return vagas

  def scrape_indeed_rss(self):
    vagas = []
    url = "https://br.indeed.com/rss"
    params = {"q": "enfermeiro", "l": "Belém, PA", "sort": "date"}
    try:
      resp = requests.get(
          url, headers=self.headers, params=params, timeout=12
      )
      if resp.status_code == 200:
        soup = BeautifulSoup(resp.content, "xml")
        for item in soup.find_all("item"):
          titulo = item.find("title").get_text(strip=True)
          link = item.find("link").get_text(strip=True)
          desc = item.find("description").get_text(strip=True)
          fonte = (
              item.find("source").get_text(strip=True)
              if item.find("source")
              else "Hospital / Clínica Local"
          )

          vagas.append({
              "title": titulo,
              "hospital_or_company": fonte,
              "location": "Belém - PA",
              "shift_type": extrair_turno(f"{titulo} {desc}"),
              "specialty": extrair_especialidade(f"{titulo} {desc}"),
              "salary": None,
              "description": (
                  BeautifulSoup(desc, "html.parser").get_text()[:300] + "..."
              ),
              "url_apply": link,
              "source": "Indeed RSS",
          })
    except Exception as e:
      print(f"Erro no feed Indeed: {e}")
    return vagas

  def coletar_todas(self):
    todas = []
    print("A recolher vagas da Rede D'Or / Porto Dias...")
    todas.extend(self.scrape_gupy("rededor", "Rede D'Or / Porto Dias"))

    print("A recolher vagas da Hapvida...")
    todas.extend(self.scrape_gupy("hapvida", "Hapvida NotreDame"))

    print("A recolher vagas do Indeed (Belém)...")
    todas.extend(self.scrape_indeed_rss())

    # Elimina links repetidos
    unicas = {v["url_apply"]: v for v in todas if v.get("url_apply")}
    return list(unicas.values())

