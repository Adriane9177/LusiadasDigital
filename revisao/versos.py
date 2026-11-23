"""Leitura dos versos, títulos e <fw> direto do texto dos arquivos XML, e comparação entre eles.

Os arquivos são percorridos como texto (e não só como árvore) para preservar o
conteúdo de cada elemento exatamente como foi escrito. O resultado é conferido com
o lxml: se os dois não concordarem, a leitura falha em vez de seguir errada.
"""
import difflib
import re
import unicodedata
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from lxml import etree

from .models import Diferenca

_MARCAS = re.compile(
    r"<div\b(?P<div>[^>]*)>|<lg\b(?P<lg>[^>]*)>|<pb\b(?P<pb>[^>]*)>"
    r"|<l(?:\s[^>]*)?>(?P<l>.*?)</l>"
    r"|<(?P<tag>head|fw)(?P<at>\s[^>]*)?>(?P<c>.*?)</(?P=tag)>", re.DOTALL)

# marcas aceitas num texto corrigido
MARCAS_ACEITAS = {"lb", "name", "sic", "w", "supplied", "unclear", "gap", "hi",
                  "choice", "orig", "reg", "corr", "abbr", "expan"}

# faixa da imagem ocupada pelos versos (as páginas são bem regulares)
TOPO, BASE = 0.11, 0.88


# altura dos elementos de margem, e a chave que identifica cada trecho:
# (elemento, canto, estrofe, verso, folha, ordem)
ALTURAS = {"header": 0.07, "catch": 0.91, "sig": 0.91}


class ErroDeLeitura(Exception):
    pass


@dataclass
class Trecho:
    xml: str
    facs: str
    altura: float
    posicao: int
    inicio: int     # onde o conteúdo está no texto do arquivo
    fim: int


def _atributo(atributos, nome):
    m = re.search(rf'\b{nome}\s*=\s*["\']([^"\']*)', atributos or "")
    return m.group(1) if m else ""


def _fragmento(xml):
    return etree.fromstring(f"<l>{xml}</l>", etree.XMLParser(resolve_entities=False))


def _texto(elemento):
    return " ".join("".join(elemento.itertext()).split())


def texto_plano(xml):
    """Texto do trecho sem marcação (mesmo critério do comparacao.py)."""
    return _texto(_fragmento(xml))


def ler_trechos(caminho):
    """{chave: Trecho} dos versos, títulos e <fw> do poema, na ordem do arquivo."""
    return ler_trechos_de(Path(caminho).read_bytes(), Path(caminho).name)


def ler_trechos_de(bruto, nome):
    # um elemento dentro de comentário seria lido como texto; trocar por espaços
    # do mesmo tamanho mantém as posições
    texto = re.sub(r"<!--.*?-->", lambda m: " " * len(m.group()), bruto.decode("utf-8"), flags=re.DOTALL)

    lidos = []           # (chave, marca, conteúdo, página, linha na página ou altura)
    linhas = {}          # página -> total de linhas
    facs = {0: ""}
    ordens = Counter()
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
        elif m.group("tag") is not None:
            if not canto:
                continue
            tipo = "head" if m.group("tag") == "head" else _atributo(m.group("at"), "type")
            if tipo not in Diferenca.Elemento.values:
                raise ErroDeLeitura(f'{nome}: <fw> com type="{tipo}" desconhecido na folha {pagina}.')
            ordens[pagina, tipo] += 1
            altura = ALTURAS.get(tipo)
            linha = max(linhas.get(pagina, 0) - 1, 0)
            lidos.append(((tipo, canto, 0, 0, pagina, ordens[pagina, tipo]), m, "c", pagina, linha, altura))
        else:
            verso += 1
            linha = linhas.get(pagina, 0)
            linhas[pagina] = linha + 1 + m.group("l").count("<lb")
            lidos.append((("verso", canto, estrofe, verso, 0, 0), m, "l", pagina, linha, None))

    resultado = {}
    for posicao, (chave, m, grupo, pag, linha, altura) in enumerate(lidos, start=1):
        if chave in resultado:
            raise ErroDeLeitura(f"{nome}: trecho repetido {chave}.")
        if altura is None:
            altura = TOPO + (BASE - TOPO) * linha / max(linhas[pag] - 1, 1)
        inicio, fim = m.span(grupo)
        resultado[chave] = Trecho(
            m.group(grupo).replace("\r\n", "\n"), facs[pag], altura, posicao, inicio, fim)

    # conferência com o lxml
    try:
        raiz = etree.fromstring(bruto, etree.XMLParser(remove_comments=True))
        lido = [texto_plano(t.xml) for t in resultado.values()]
    except etree.XMLSyntaxError as erro:
        raise ErroDeLeitura(f"{nome}: XML inválido ({erro}).")
    esperado = [_texto(el) for div in raiz.iter("{*}div") if div.get("type") == "canto"
                for el in div.iter("{*}l", "{*}head", "{*}fw")]
    if lido != esperado:
        raise ErroDeLeitura(f"{nome}: a leitura do arquivo diverge da árvore XML.")
    return resultado


def substituir(bruto, nome, trocas):
    """Troca o conteúdo dos trechos {chave: xml novo}, sem tocar no resto do arquivo."""
    texto = bruto.decode("utf-8")
    trechos = ler_trechos_de(bruto, nome)
    for chave in sorted(trocas, key=lambda c: trechos[c].inicio, reverse=True):
        t = trechos[chave]
        texto = texto[:t.inicio] + trocas[chave] + texto[t.fim:]
    novo = texto.encode("utf-8")
    # nada além do pedido pode ter mudado
    esperado = {c: trocas.get(c, t.xml) for c, t in trechos.items()}
    if {c: t.xml for c, t in ler_trechos_de(novo, nome).items()} != esperado:
        raise ErroDeLeitura(f"{nome}: a substituição alterou trechos indevidos.")
    return novo


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
    """Campo -> conteúdo XML do elemento. Levanta ValueError se for inválido."""
    xml = unicodedata.normalize("NFC", " ".join(digitado.split()))
    xml = re.sub(r"&(?!(?:amp|lt|gt|quot|apos);)", "&amp;", xml)
    try:
        raiz = _fragmento(xml)
    except (etree.XMLSyntaxError, ValueError):
        raise ValueError("A marcação não é um XML válido. Confira os sinais < e >.")
    for el in raiz.iterdescendants():
        if not isinstance(el.tag, str) or el.tag not in MARCAS_ACEITAS:
            raise ValueError("Há uma marca não aceita no texto.")
    if not _texto(raiz):
        raise ValueError("O texto não pode ficar vazio.")
    return xml
