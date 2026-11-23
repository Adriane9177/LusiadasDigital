import subprocess
import tempfile
from collections import Counter
from datetime import timedelta
from pathlib import Path
from unittest import mock
from unicodedata import normalize

from django.conf import settings
from django.contrib.auth.models import User
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .models import Decisao, Diferenca
from .sincronizar import sincronizar
from .versos import ErroDeLeitura, classificar, de_edicao, ler_trechos, para_edicao, realcar, substituir

TEXTOS = settings.BASE_DIR / "LusiadasTextos"


def documento(*versos):
    linhas = "\r\n".join(f"<l>{v}</l>" for v in versos)
    return ('<TEI xmlns="http://www.tei-c.org/ns/1.0"><text><body><pb facs="../imagens/pX/V1.jpg"/>'
            f'<div type="canto" n="1"><lg n="1">{linhas}</lg></div></body></text></TEI>')


class Base(TestCase):
    def setUp(self):
        pasta = tempfile.TemporaryDirectory()
        self.addCleanup(pasta.cleanup)
        self.a, self.b = Path(pasta.name) / "a.xml", Path(pasta.name) / "b.xml"
        self.escrever(['um Ba-<lb break="no"/>rão &amp; dois', "igual", "sol,"],
                      ["um varam &amp; dois", "igual", "sol"])
        sincronizar(self.a, self.b)
        self.d1, self.d2 = Diferenca.objects.all()
        self.ana = User.objects.create_user("ana", password="x")
        self.rui = User.objects.create_user("rui", password="x")
        self.client.force_login(self.ana)

    def escrever(self, va, vb):
        self.a.write_text(documento(*va), encoding="utf-8", newline="")
        self.b.write_text(documento(*vb), encoding="utf-8-sig", newline="")

    def decidir(self, d, acao, **dados):
        d.refresh_from_db()
        campos = {"acao": acao, "edicao": d.edicao, "comentario": "",
                  "texto_a": para_edicao(d.xml_a), "texto_b": para_edicao(d.xml_b), **dados}
        r = self.client.post(reverse("revisao:revisar", args=[d.pk]), campos)
        d.refresh_from_db()
        return r


class VersosTests(Base):
    def test_arquivos_do_projeto(self):
        for nome in ("LusiadasEsquerda.xml", "LusiadasDireita.xml"):
            versos = ler_trechos(TEXTOS / nome)
            self.assertEqual(Counter(c[0] for c in versos), {
                "verso": 8816, "head": 11, "header": 371, "catch": 364, "sig": 93})
            for v in versos.values():
                self.assertTrue(v.facs)
                self.assertTrue(0 < v.altura < 1)
                # abrir no campo e salvar sem mexer não altera o verso
                self.assertEqual(de_edicao(para_edicao(v.xml)).split(), normalize("NFC", v.xml).split())

    def test_leitura_preserva_o_xml_e_ignora_comentarios(self):
        self.assertEqual(self.d1.xml_a, 'um Ba-<lb break="no"/>rão &amp; dois')
        self.a.write_text(documento("x").replace("<lg", "<!-- <l>fantasma</l> --><lg"), encoding="utf-8")
        self.assertEqual(len(ler_trechos(self.a)), 1)

    def test_xml_invalido_nao_passa(self):
        self.a.write_text(documento("x")[:-3], encoding="utf-8")
        with self.assertRaises(ErroDeLeitura):
            ler_trechos(self.a)

    def test_classificacao(self):
        for a, b, tipo in [("Lusi-tana", "Lusitana", "hifen"), ("sol,", "sol", "pontuacao"),
                           ("Sol", "sol", "caixa"), ("fé", "fe", "acento"), ("forão", "foram", "letras")]:
            self.assertEqual(classificar(a, b), tipo)

    def test_realce(self):
        pa, pb = realcar("o sol,", "o sol")
        self.assertEqual(pa, [("o sol", False), (",", True)])
        self.assertEqual(pb, [("o sol", False)])

    def test_correcao_digitada(self):
        self.assertEqual(de_edicao("a & b"), "a &amp; b")
        self.assertEqual(de_edicao("a &amp; b"), "a &amp; b")
        for ruim in ("", "<lb/>", "a < b", "<script>x</script>", "a</l><l>b", "a<!-- x -->", "a\x00b"):
            with self.assertRaises(ValueError, msg=ruim):
                de_edicao(ruim)


class SincronizarTests(Base):
    def test_repetir_nao_muda_nada(self):
        conta = sincronizar(self.a, self.b)
        self.assertEqual((conta["novas"], conta["atualizadas"]), (0, 0))

    def test_decisao_preservada_antes_e_depois_de_aplicar_a_correcao(self):
        self.decidir(self.d1, "erro", texto_b="um barão & dois")
        sincronizar(self.a, self.b)
        self.d1.refresh_from_db()
        self.assertEqual(self.d1.situacao, "erro")
        self.escrever([self.d1.xml_a, "igual", "sol,"], [self.d1.correcao_b, "igual", "sol"])
        sincronizar(self.a, self.b)
        self.d1.refresh_from_db()
        self.assertEqual((self.d1.situacao, self.d1.correcao_b), ("erro", "um barão &amp; dois"))

    def test_texto_mudado_depois_reabre(self):
        self.decidir(self.d1, "real")
        self.escrever(["outro", "igual", "sol,"], [self.d1.xml_b, "igual", "sol"])
        self.assertEqual(sincronizar(self.a, self.b)["reabertas"], 1)
        self.d1.refresh_from_db()
        self.assertEqual((self.d1.situacao, self.d1.xml_a, self.d1.revisor), ("pendente", "outro", None))
        self.assertEqual(Decisao.objects.count(), 2)

    def test_diferenca_que_sumiu_sai_da_fila(self):
        self.escrever([self.d1.xml_a, "igual", "sol"], [self.d1.xml_b, "igual", "sol"])
        self.assertEqual(sincronizar(self.a, self.b)["pendentes"], 1)
        self.d2.refresh_from_db()
        self.assertFalse(self.d2.ativa)


    def test_correcao_refeita_depois_de_aplicada_continua_valendo(self):
        self.decidir(self.d1, "erro", texto_b="um barão & dois")
        self.escrever([self.d1.xml_a, "igual", "sol,"], [self.d1.correcao_b, "igual", "sol"])
        self.decidir(self.d1, "erro", texto_b="um varão & dois")
        self.assertEqual(sincronizar(self.a, self.b)["reabertas"], 0)

    def test_titulos_e_fw_entram_na_fila(self):
        extra = '<head>Canto {}</head><fw type="header" place="top">C.</fw><lg'
        for arq, x in ((self.a, "primeiro"), (self.b, "Primeiro")):
            s = arq.read_text(encoding="utf-8").replace("<lg", extra.format(x))
            arq.write_text(s.replace("C.<", "D.<") if arq == self.b else s, encoding="utf-8")
        self.assertEqual(sincronizar(self.a, self.b)["novas"], 2)
        self.assertEqual([d.elemento for d in Diferenca.objects.all()], ["head", "header", "verso", "verso"])
        head = Diferenca.objects.first()
        self.assertEqual(str(head), "Canto 1, título (V1)")
        self.assertEqual(self.client.get(reverse("revisao:revisar", args=[head.pk])).status_code, 200)
        r = self.client.get(reverse("revisao:proxima"), {"elemento": "header"})
        self.assertIn("elemento=header", r["Location"])


class SubstituirTests(Base):
    def test_so_muda_o_pedido(self):
        for arq in (self.a, self.b):   # CRLF, com e sem BOM
            bruto = arq.read_bytes()
            chave = ("verso", 1, 1, 2, 0, 0)
            novo = substituir(bruto, "x", {chave: "outro &amp; maior"})
            self.assertEqual(novo, bruto.replace(b"<l>igual</l>", b"<l>outro &amp; maior</l>"))

    def test_troca_malformada_nao_passa(self):
        with self.assertRaises(ErroDeLeitura):
            substituir(self.a.read_bytes(), "x", {("verso", 1, 1, 2, 0, 0): "a</l><l>b"})


class EnviarTests(Base):
    """Fluxo completo contra um repositório Git local; só a API do GitHub é simulada."""

    def git(self, *args, cwd=None):
        return subprocess.run(["git", *args], cwd=cwd or self.clone, check=True,
                              capture_output=True, text=True, encoding="utf-8").stdout.strip()

    def setUp(self):
        super().setUp()
        pasta = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(pasta.cleanup)
        raiz = Path(pasta.name)
        self.origem, self.clone = raiz / "origem.git", raiz / "clone"
        self.git("init", "-q", "--bare", "-b", "develop", str(self.origem), cwd=raiz)
        self.git("clone", "-q", str(self.origem), str(self.clone), cwd=raiz)
        for k, v in (("user.name", "t"), ("user.email", "t@example.org"), ("core.autocrlf", "false")):
            self.git("config", k, v)
        self.git("checkout", "-q", "-B", "develop")
        (self.clone / "LusiadasTextos").mkdir()
        self.publicar()
        ligado = override_settings(REVISAO_REPO=str(self.clone), REVISAO_TOKEN="t", REVISAO_GITHUB="dono/repo",
                                   REVISAO_BASE="develop", REVISAO_BRANCH="correcoes-revisao")
        ligado.enable()
        self.addCleanup(ligado.disable)
        self.chamadas = []
        api = mock.patch("revisao.management.commands.enviar_correcoes.chamar_api", self.api)
        api.start()
        self.addCleanup(api.stop)

    def api(self, metodo, caminho, dados=None):
        self.chamadas.append((metodo, dados))
        return [] if metodo == "GET" else {"html_url": "http://pr"}

    def publicar(self):
        textos = self.clone / "LusiadasTextos"
        (textos / "LusiadasEsquerda.xml").write_bytes(self.a.read_bytes())
        (textos / "LusiadasDireita.xml").write_bytes(self.b.read_bytes())
        self.git("add", ".")
        self.git("commit", "-q", "-m", "textos")
        self.git("push", "-q", "origin", "HEAD:develop")

    def enviar(self):
        self.chamadas.clear()
        call_command("enviar_correcoes", verbosity=0)

    def test_fluxo(self):
        self.enviar()
        self.assertEqual(self.chamadas, [])   # nada decidido, nada enviado
        self.decidir(self.d1, "erro", texto_b="um barão & dois")
        self.decidir(self.d2, "real")
        self.enviar()
        self.assertEqual([c[0] for c in self.chamadas], ["GET", "POST"])
        self.assertIn("um barão", self.chamadas[1][1]["body"])
        self.assertNotIn("ana", self.chamadas[1][1]["body"].split())
        texto = self.git("show", "origin/correcoes-revisao:LusiadasTextos/LusiadasDireita.xml")
        self.assertIn("<l>um barão &amp; dois</l>", texto)
        self.assertIn("real", self.git("show", "origin/correcoes-revisao:LusiadasTextos/revisao_decisoes.csv"))
        antes = self.git("rev-parse", "origin/correcoes-revisao")
        self.enviar()                         # repetir não cria outro commit
        self.assertEqual(self.git("rev-parse", "origin/correcoes-revisao"), antes)
        # depois do merge, não há mais o que enviar
        self.git("push", "-q", "origin", "origin/correcoes-revisao:develop")
        self.enviar()
        self.assertEqual(self.chamadas, [])
        self.d1.refresh_from_db()
        self.assertEqual(self.d1.situacao, "erro")

    def test_nao_sobrescreve_commit_manual_na_branch(self):
        self.decidir(self.d1, "erro", texto_b="um barão & dois")
        self.enviar()
        self.git("checkout", "-q", "-B", "manual", "origin/correcoes-revisao")
        self.git("commit", "-q", "--allow-empty", "-m", "manual")
        self.git("push", "-q", "origin", "HEAD:correcoes-revisao")
        with self.assertRaisesMessage(CommandError, "commits feitos à mão"):
            self.enviar()

    def test_texto_mudado_em_develop_reabre_em_vez_de_sobrescrever(self):
        self.decidir(self.d1, "erro", texto_b="um barão & dois")
        self.escrever([self.d1.xml_a, "igual", "sol,"], ["mudado", "igual", "sol"])
        self.publicar()
        self.enviar()
        self.d1.refresh_from_db()
        self.assertEqual((self.d1.situacao, self.chamadas), ("pendente", []))


class TelaTests(Base):
    def test_exige_login(self):
        self.client.logout()
        for url in (reverse("revisao:painel"), reverse("revisao:proxima"),
                    reverse("revisao:revisar", args=[self.d1.pk])):
            r = self.client.get(url)
            self.assertEqual(r.status_code, 302)
            self.assertIn(reverse("revisao:entrar"), r["Location"])
        self.assertEqual(self.client.get(reverse("revisao:entrar")).status_code, 200)

    def test_tela_e_painel(self):
        r = self.client.get(reverse("revisao:revisar", args=[self.d1.pk]))
        self.assertContains(r, "/media/facsimile/pX/V1.jpg")
        self.assertContains(r, "<mark>")
        self.assertContains(r, 'data-altura="0.')  # ponto decimal, apesar do pt-br
        self.assertContains(self.client.get(reverse("revisao:painel")), "0 de 2 trechos")

    def test_duas_pessoas_recebem_versos_diferentes(self):
        self.assertIn(f"/{self.d1.pk}/", self.client.get(reverse("revisao:proxima"))["Location"])
        self.client.force_login(self.rui)
        self.assertIn(f"/{self.d2.pk}/", self.client.get(reverse("revisao:proxima"))["Location"])
        Diferenca.objects.filter(pk=self.d1.pk).update(reservado_em=timezone.now() - timedelta(hours=1))
        self.assertIn(f"/{self.d1.pk}/", self.client.get(reverse("revisao:proxima"))["Location"])

    def test_pular_filtro_e_fim_da_fila(self):
        url = reverse("revisao:proxima")
        self.assertIn(f"/{self.d2.pk}/", self.client.get(url, {"depois": self.d1.pk})["Location"])
        self.assertIn(f"/{self.d1.pk}/", self.client.get(url, {"depois": self.d2.pk})["Location"])
        self.assertIn(f"/{self.d2.pk}/?tipo=pontuacao", self.client.get(url, {"tipo": "pontuacao"})["Location"])
        self.assertEqual(self.client.get(url, {"canto": "9"})["Location"], reverse("revisao:painel"))

    def test_decisoes(self):
        self.assertEqual(self.decidir(self.d1, "erro").status_code, 400)
        self.assertEqual(self.decidir(self.d1, "duvida").status_code, 400)
        self.assertEqual(self.decidir(self.d1, "real", texto_a="outra coisa").status_code, 400)
        self.assertEqual(self.d1.situacao, "pendente")
        self.assertEqual(self.decidir(self.d1, "real").status_code, 302)
        self.assertEqual((self.d1.situacao, self.d1.revisor, self.d1.correcao_a), ("real", self.ana, ""))
        self.decidir(self.d1, "erro", texto_a="um Barão & dois")
        self.assertEqual((self.d1.correcao_a, self.d1.correcao_b), ("um Barão &amp; dois", ""))
        self.assertEqual(Decisao.objects.count(), 2)

    def test_tela_desatualizada_nao_sobrescreve(self):
        self.decidir(self.d1, "real")
        r = self.decidir(self.d1, "erro", texto_a="x", edicao=0)
        self.assertEqual(r.status_code, 400)
        self.assertContains(r, 'name="edicao" value="0"', status_code=400)
        self.assertEqual(self.d1.situacao, "real")
