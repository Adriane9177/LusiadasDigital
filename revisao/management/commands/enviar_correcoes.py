"""Aplica as correções da revisão aos XMLs e mantém um pull request com elas.

Cada execução parte da branch base (develop) atualizada, põe a fila em dia com
ela, aplica TODAS as correções decididas (as já incorporadas não mudam nada) e
atualiza uma única branch e um único PR. Pode ser repetida à vontade.

Usa um clone do Git só para isso (REVISAO_REPO): o conteúdo dele é descartado.
"""
import base64
import csv
import io
import json
import os
import subprocess
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from revisao.models import Diferenca
from revisao.sincronizar import sincronizar
from revisao.versos import ErroDeLeitura, ler_trechos_de, substituir

ARQUIVOS = {"a": "LusiadasTextos/LusiadasEsquerda.xml", "b": "LusiadasTextos/LusiadasDireita.xml"}
REGISTRO = "LusiadasTextos/revisao_decisoes.csv"
AUTOR, EMAIL = "Revisao Lusiadas Digital", "revisao@lusiadasdigital.invalid"
MAX_LINHAS = 200
S = Diferenca.Situacao


def chamar_api(metodo, caminho, dados=None):
    pedido = urllib.request.Request(
        "https://api.github.com" + caminho, method=metodo,
        data=json.dumps(dados).encode() if dados is not None else None,
        headers={"Authorization": f"Bearer {settings.REVISAO_TOKEN}",
                 "Accept": "application/vnd.github+json", "User-Agent": "revisao"})
    try:
        with urllib.request.urlopen(pedido, timeout=30) as resposta:
            return json.load(resposta)
    except urllib.error.HTTPError as erro:
        raise CommandError(f"GitHub respondeu {erro.code}: {erro.read().decode(errors='replace')[:300]}")
    except urllib.error.URLError as erro:
        raise CommandError(f"Não foi possível falar com o GitHub: {erro.reason}")


def git(*args, ok=(0,)):
    ambiente = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
    # o token vai pelo ambiente: não aparece na lista de processos nem no .git/config
    credencial = base64.b64encode(f"x-access-token:{settings.REVISAO_TOKEN}".encode()).decode()
    ambiente.update({"GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "http.https://github.com/.extraheader",
                     "GIT_CONFIG_VALUE_0": f"Authorization: Basic {credencial}"})
    r = subprocess.run(["git", "-c", "core.autocrlf=false", *args], cwd=settings.REVISAO_REPO, env=ambiente,
                       capture_output=True, text=True, encoding="utf-8")
    if r.returncode not in ok:
        raise CommandError(f"git {args[0]} falhou: {r.stderr.strip()}")
    return r


def _celula(texto):
    # em <code>, sem que o texto vire marcação ou quebre a tabela
    for de, para in (("&", "&amp;"), ("<", "&lt;"), (">", "&gt;"), ("|", "&#124;"), ("@", "&#64;")):
        texto = texto.replace(de, para)
    return f"<code>{texto}</code>"


class Command(BaseCommand):
    help = "Aplica as correções da revisão aos XMLs e abre (ou atualiza) o pull request."

    def handle(self, *args, **opcoes):
        repo = Path(settings.REVISAO_REPO) if settings.REVISAO_REPO else None
        if repo is None or not (repo / ".git").exists():
            raise CommandError("Defina REVISAO_REPO com o caminho de um clone do repositório.")
        if not settings.REVISAO_TOKEN:
            raise CommandError("Defina REVISAO_TOKEN.")
        if repo.resolve() == Path(settings.BASE_DIR).resolve():
            raise CommandError("REVISAO_REPO não pode ser a pasta do próprio site.")
        base, branch = f"origin/{settings.REVISAO_BASE}", settings.REVISAO_BRANCH
        if branch in {settings.REVISAO_BASE, "main"}:
            raise CommandError("REVISAO_BRANCH precisa ser uma branch exclusiva.")
        remota = f"origin/{branch}"

        git("fetch", "--prune", "origin")
        existe = git("rev-parse", "-q", "--verify", remota, ok=(0, 1)).returncode == 0
        if existe:
            emails = set(git("log", "--format=%ae", f"{base}..{remota}").stdout.split())
            if emails - {EMAIL}:
                raise CommandError(f"A branch {branch} tem commits feitos à mão; faça o merge ou apague-a antes.")
        git("checkout", "-q", "-f", "-B", branch, base)

        try:
            sincronizar(repo / ARQUIVOS["a"], repo / ARQUIVOS["b"])
            decididas = list(Diferenca.objects.filter(
                situacao__in=[S.REAL, S.ERRO]).prefetch_related("historico"))
            mudancas = self._aplicar(repo, decididas)
        except (ErroDeLeitura, OSError, ValueError) as erro:
            raise CommandError(str(erro))

        if decididas:
            self._registrar(repo, decididas)
            git("add", "--", *ARQUIVOS.values(), REGISTRO)
        if git("diff", "--cached", "--quiet", ok=(0, 1)).returncode == 0:
            self._dizer("Nada novo a enviar.")
            return

        titulo = f"Revisão das transcrições: {len(mudancas)} correção(ões)"
        igual = existe and git("write-tree").stdout.strip() == git(
            "rev-parse", f"{remota}^{{tree}}").stdout.strip()
        if not igual:
            git("-c", f"user.name={AUTOR}", "-c", f"user.email={EMAIL}", "commit", "-q", "-m", titulo)
            # só sobrescreve se a branch remota ainda for a que foi conferida acima
            anterior = git("rev-parse", remota).stdout.strip() if existe else ""
            git("push", "-q", f"--force-with-lease={branch}:{anterior}", "origin", f"HEAD:refs/heads/{branch}")
            git("fetch", "origin")
        self._pull_request(titulo, self._corpo(mudancas, decididas))

    def _dizer(self, texto):
        self.stdout.write(texto)

    def _aplicar(self, repo, decididas):
        mudancas = []
        for lado, caminho in ARQUIVOS.items():
            arquivo = repo / caminho
            bruto = arquivo.read_bytes()
            trechos = ler_trechos_de(bruto, arquivo.name)
            trocas = {}
            for d in decididas:
                trecho = trechos.get(d.chave)
                final = getattr(d, f"texto_final_{lado}")
                if trecho and trecho.xml != final and trecho.xml in d.conhecidos(lado):
                    trocas[d.chave] = final
                    mudancas.append((d, lado.upper(), trecho.xml, final))
            if trocas:
                arquivo.write_bytes(substituir(bruto, arquivo.name, trocas))
        mudancas.sort(key=lambda m: (m[0].posicao, m[1]))
        return mudancas

    def _registrar(self, repo, decididas):
        # sem nomes: o repositório é público (a autoria fica no histórico do site)
        saida = io.StringIO()
        w = csv.writer(saida, delimiter=";", lineterminator="\n")
        w.writerow(["trecho", "situação", "A", "B"])
        for d in decididas:
            w.writerow([str(d), d.situacao, d.texto_final_a, d.texto_final_b])
        (repo / REGISTRO).write_bytes(saida.getvalue().encode("utf-8"))

    def _corpo(self, mudancas, decididas):
        reais = sum(d.situacao == S.REAL for d in decididas)
        linhas = [f"Gerado pela área de revisão. Diferenças reais confirmadas até agora: {reais}.", ""]
        if mudancas:
            linhas += ["| Trecho | Versão | Antes | Depois |", "|---|---|---|---|"]
            linhas += [f"| {d} | {lado} | {_celula(x)} | {_celula(y)} |" for d, lado, x, y in mudancas[:MAX_LINHAS]]
            if len(mudancas) > MAX_LINHAS:
                linhas.append(f"\n… e mais {len(mudancas) - MAX_LINHAS} (veja os arquivos).")
        return "\n".join(linhas)

    def _pull_request(self, titulo, corpo):
        dono = settings.REVISAO_GITHUB.split("/")[0]
        url = f"/repos/{settings.REVISAO_GITHUB}/pulls"
        consulta = urllib.parse.urlencode({
            "state": "open", "head": f"{dono}:{settings.REVISAO_BRANCH}", "base": settings.REVISAO_BASE})
        abertos = chamar_api("GET", f"{url}?{consulta}")
        if abertos:
            pr = chamar_api("PATCH", f"{url}/{abertos[0]['number']}", {"title": titulo, "body": corpo})
        else:
            pr = chamar_api("POST", url, {
                "title": titulo, "body": corpo, "head": settings.REVISAO_BRANCH, "base": settings.REVISAO_BASE})
        self._dizer(f"Pull request: {pr['html_url']}")
