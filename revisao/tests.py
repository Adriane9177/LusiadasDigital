import tempfile
from datetime import timedelta
from pathlib import Path
from unicodedata import normalize

from django.conf import settings
from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from .models import Decisao, Diferenca
from .sincronizar import sincronizar
from .versos import ErroDeLeitura, classificar, de_edicao, ler_versos, para_edicao, realcar

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
            versos = ler_versos(TEXTOS / nome)
            self.assertEqual(len(versos), 8816)
            for v in versos.values():
                self.assertTrue(v.facs)
                self.assertTrue(0 < v.altura < 1)
                # abrir no campo e salvar sem mexer não altera o verso
                self.assertEqual(de_edicao(para_edicao(v.xml)).split(), normalize("NFC", v.xml).split())

    def test_leitura_preserva_o_xml_e_ignora_comentarios(self):
        self.assertEqual(self.d1.xml_a, 'um Ba-<lb break="no"/>rão &amp; dois')
        self.a.write_text(documento("x").replace("<lg", "<!-- <l>fantasma</l> --><lg"), encoding="utf-8")
        self.assertEqual(len(ler_versos(self.a)), 1)

    def test_xml_invalido_nao_passa(self):
        self.a.write_text(documento("x")[:-3], encoding="utf-8")
        with self.assertRaises(ErroDeLeitura):
            ler_versos(self.a)

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
        self.assertContains(self.client.get(reverse("revisao:painel")), "0 de 2 versos")

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
