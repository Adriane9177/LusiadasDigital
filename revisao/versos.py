"""Leitura dos versos direto do texto dos arquivos XML, e comparação entre eles.

Os arquivos são percorridos como texto (e não só como árvore) para preservar o
conteúdo de cada <l> exatamente como foi escrito. O resultado é conferido com
o lxml: se os dois não concordarem, a leitura falha em vez de seguir errada.
"""
import difflib
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path

from lxml import etree

from .models import Diferenca

_MARCAS = re.compile(
    r"<div\b(?P<div>[^>]*)>|<lg\b(?P<lg>[^>]*)>|<pb\b(?P<pb>[^>]*)>"
    r"|<l(?:\s[^>]*)?>(?P<l>.*?)</l>", re.DOTALL)

# marcas aceitas dentro de um verso corrigido
MARCAS_ACEITAS = {"lb", "sic", "w", "supplied", "unclear", "gap", "hi",
                  "choice", "orig", "reg", "corr", "abbr", "expan"}

# faixa da imagem ocupada pelos versos (as páginas são bem regulares)
TOPO, BASE = 0.11, 0.88


class ErroDeLeitura(Exception):
    pass


@dataclass
class Verso:
    xml: str
    facs: str
    altura: float


def _atributo(atributos, nome):
    m = re.search(rf'\b{nome}\s*=\s*["\']([^"\']*)', atributos or "")
    return m.group(1) if m else ""


def _fragmento(xml):
    return etree.fromstring(f"<l>{xml}</l>", etree.XMLParser(resolve_entities=False))


def _texto(elemento):
    return " ".join("".join(elemento.itertext()).split())


def texto_plano(xml):
    """Texto do verso sem marcação (mesmo critério do comparacao.py)."""
    return _texto(_fragmento(xml))


def ler_versos(caminho):
    """{(canto, estrofe, verso): Verso}. O canto é a posição no arquivo."""
    caminho = Path(caminho)
    bruto = caminho.read_bytes()
    texto = bruto.decode("utf-8-sig").replace("\r\n", "\n")
    # um <l> dentro de comentário seria lido como verso
    texto = re.sub(r"<!--.*?-->", "", texto, flags=re.DOTALL)

    lidos = []           # (chave, xml, página, linha na página)
    linhas = {}          # página -> total de linhas
    facs = {0: ""}
    canto = estrofe = verso = pagina = 0
    for m in _MARCAS.finditer(texto):
        if m.group("div") is not None:
            if _atributo(m.group("div"), "type") == "canto":
                canto += 1
                linhas[pagina] = linhas.get(pagina, 0) + 2   # títulos
        elif m.group("pb") is not None:
            pagina += 1
            facs[pagina] = _atributo(m.group("pb"), "facs")
        elif m.group("lg") is not None:
            estrofe, verso = int(_atributo(m.group("lg"), "n")), 0
            if linhas.get(pagina):
                linhas[pagina] += 1                           # espaço entre estrofes
        else:
            verso += 1
            linha = linhas.get(pagina, 0)
            linhas[pagina] = linha + 1 + m.group("l").count("<lb")
            lidos.append(((canto, estrofe, verso), m.group("l"), pagina, linha))

    resultado = {}
    for chave, xml, pag, linha in lidos:
        if chave in resultado:
            raise ErroDeLeitura(f"{caminho.name}: verso repetido {chave}.")
        altura = TOPO + (BASE - TOPO) * linha / max(linhas[pag] - 1, 1)
        resultado[chave] = Verso(xml, facs[pag], altura)

    # conferência com o lxml
    try:
        raiz = etree.fromstring(bruto, etree.XMLParser(remove_comments=True))
        esperado, n_canto = {}, 0
        for div in raiz.iter("{*}div"):
            if div.get("type") != "canto":
                continue
            n_canto += 1
            for lg in div.iter("{*}lg"):
                for i, l in enumerate(lg.findall("{*}l"), start=1):
                    esperado[(n_canto, int(lg.get("n")), i)] = _texto(l)
        lido = {chave: texto_plano(v.xml) for chave, v in resultado.items()}
    except etree.XMLSyntaxError as erro:
        raise ErroDeLeitura(f"{caminho.name}: XML inválido ({erro}).")
    if lido != esperado:
        errados = [c for c in set(lido) | set(esperado) if lido.get(c) != esperado.get(c)]
        raise ErroDeLeitura(f"{caminho.name}: leitura divergente em {sorted(errados)[:5]}.")
    return resultado


# ------------------------------------------------------------------ comparação

def classificar(a, b):
    """Tipo de diferença entre dois textos planos (aproximado, para filtrar)."""
    tipos = Diferenca.Tipo
    if re.sub(r"[-\s]", "", a) == re.sub(r"[-\s]", "", b):
        return tipos.HIFEN
    a, b = (re.sub(r"[\W_]", "", unicodedata.normalize("NFC", t)) for t in (a, b))
    if a == b:
        return tipos.PONTUACAO
    if a.lower() == b.lower():
        return tipos.CAIXA

    def base(t):
        return "".join(c for c in unicodedata.normalize("NFD", t.lower()) if not unicodedata.combining(c))
    return tipos.ACENTO if base(a) == base(b) else tipos.LETRAS


def realcar(xml_a, xml_b):
    """Pares [(trecho, difere?)] de cada lado, comparando palavra a palavra."""
    ta, tb = (re.findall(r"\w+|\s+|[^\w\s]", texto_plano(x)) for x in (xml_a, xml_b))
    pa, pb = [], []
    for op, a1, a2, b1, b2 in difflib.SequenceMatcher(None, ta, tb, autojunk=False).get_opcodes():
        if a2 > a1:
            pa.append(("".join(ta[a1:a2]), op != "equal"))
        if b2 > b1:
            pb.append(("".join(tb[b1:b2]), op != "equal"))
    return pa, pb


# ------------------------------------------------------------------ correções

def para_edicao(xml):
    """Forma mostrada no campo: '&amp;' vira '&'; o resto fica como está."""
    return xml.replace("&amp;", "&")


def de_edicao(digitado):
    """Campo -> conteúdo XML do <l>. Levanta ValueError se for inválido."""
    xml = unicodedata.normalize("NFC", " ".join(digitado.split()))
    xml = re.sub(r"&(?!(?:amp|lt|gt|quot|apos);)", "&amp;", xml)
    try:
        raiz = _fragmento(xml)
    except (etree.XMLSyntaxError, ValueError):
        raise ValueError("A marcação não é um XML válido. Confira os sinais < e >.")
    for el in raiz.iterdescendants():
        if not isinstance(el.tag, str) or el.tag not in MARCAS_ACEITAS:
            raise ValueError("Há uma marca não aceita dentro de um verso.")
    if not _texto(raiz):
        raise ValueError("O verso não pode ficar vazio.")
    return xml
