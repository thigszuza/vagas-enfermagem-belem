import unicodedata
import requests
from bs4 import BeautifulSoup

def normalizar_texto(texto: str) -> str:
    if not texto:
        return ""
    nfkd = unicodedata.normalize('NFKD', texto)
    return "".join([c for c in nfkd if not unicodedata.combining(c)]).lower()

def extrair_turno(texto: str) -> str:
    t = normalizar_texto(texto)
    if "12x36" in t:
        return "12x36"
    if "noturno" in t:
        return "Noturno"
    if "diurno" in t or "manha" in t or "tarde" in t:
        return "Diurno"
    return "A combinar"

def extrair_especialidade(texto: str) -> str:
    t = normalizar_texto(texto)
    if "uti" in t:
        return "UTI"
    if "cirurgico" in t or "cc" in t:
        return "Centro Cirúrgico"
    if "urgencia" in t or "emergencia" in t or "pronto socorro" in t:
        return "Urgência/Emergência"
    if "pediatria" in t or "neonatal" in t:
        return "Pediatria"
    if "tecnico" in t:
        return "Técnico de Enfermagem"
    return "Enfermagem Geral"

class ScraperHospitaisBelem:
    def __init__(self):
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        }

    def scrape_gupy_empresa(self, subdomain: str, nome_empresa: str):
        vagas = []
        termos = ["enfermagem", "enfermeiro", "enfermeira", "tecnico-de-enfermagem"]
        
        for termo in termos:
            url = f"https://{subdomain}.gupy.io/api/v1/jobs?jobName={termo}&limit=50"
            try:
                resp = requests.get(url, headers=self.headers, timeout=10)
                if resp.status_code == 200:
                    dados = resp.json().get("data", [])
                    for item in dados:
                        cidade = normalizar_texto(item.get("city", ""))
                        titulo = item.get("name", "")
                        t_norm = normalizar_texto(titulo)

                        # Filtra pela região metropolitana de Belém ou vagas sem cidade definida
                        if "belem" in cidade or "ananindeua" in cidade or not cidade:
                            if "enferm" in t_norm:
                                desc = item.get("description", "")
                                vagas.append({
                                    "title": titulo,
                                    "hospital_or_company": nome_empresa,
                                    "location": f"{item.get('city') or 'Belém'} - PA",
                                    "shift_type": extrair_turno(f"{titulo} {desc}"),
                                    "specialty": extrair_especialidade(f"{titulo} {desc}"),
                                    "salary": None,
                                    "description": BeautifulSoup(desc, "html.parser").get_text()[:280] + "...",
                                    "url_apply": item.get("jobUrl"),
                                    "source": f"Gupy ({nome_empresa})"
                                })
            except Exception:
                continue
        return vagas

    def scrape_indeed_rss(self):
        vagas = []
        queries = ["enfermeira", "enfermeiro", "tecnico de enfermagem"]
        for q in queries:
            url = "https://br.indeed.com/rss"
            params = {"q": q, "l": "Belém, PA", "sort": "date"}
            try:
                resp = requests.get(url, headers=self.headers, params=params, timeout=12)
                if resp.status_code == 200:
                    soup = BeautifulSoup(resp.content, "xml")
                    for item in soup.find_all("item"):
                        titulo = item.find("title").get_text(strip=True) if item.find("title") else ""
                        link = item.find("link").get_text(strip=True) if item.find("link") else ""
                        desc = item.find("description").get_text(strip=True) if item.find("description") else ""
                        fonte = item.find("source").get_text(strip=True) if item.find("source") else "Hospital / Clínica em Belém"

                        vagas.append({
                            "title": titulo,
                            "hospital_or_company": fonte,
                            "location": "Belém - PA",
                            "shift_type": extrair_turno(f"{titulo} {desc}"),
                            "specialty": extrair_especialidade(f"{titulo} {desc}"),
                            "salary": None,
                            "description": BeautifulSoup(desc, "html.parser").get_text()[:280] + "...",
                            "url_apply": link,
                            "source": "Indeed"
                        })
            except Exception:
                continue
        return vagas

    def coletar_todas(self):
        todas = []
        # Principais redes de saúde com presença no Pará
        empresas_gupy = [
            ("rededor", "Rede D'Or / Porto Dias"),
            ("hapvida", "Hapvida NotreDame"),
            ("ebserh", "EBSERH / HUJBB"),
            ("institutosaudeevida", "Hospital Regional / Saúde Pública"),
            ("amil", "Amil Saúde"),
            ("dasa", "Dasa / Diagnósticos")
        ]

        for sub, nome in empresas_gupy:
            todas.extend(self.scrape_gupy_empresa(sub, nome))

        # Adiciona o feed do Indeed para Belém
        todas.extend(self.scrape_indeed_rss())

        # Desduplicação de URLs
        unicas = {v["url_apply"]: v for v in todas if v.get("url_apply")}
        return list(unicas.values())
    