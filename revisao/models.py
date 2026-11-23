from pathlib import PurePosixPath

from django.conf import settings
from django.db import models


class Diferenca(models.Model):
    """Um trecho (verso, título, cabeço...) que difere entre as duas edições de 1572.

    Os textos são o conteúdo XML do elemento (com <lb/>, &amp; etc.), tal como está
    nos arquivos. Não depende das tabelas de leitura: reimportar os textos do
    site não apaga as decisões.
    """

    class Situacao(models.TextChoices):
        PENDENTE = "pendente", "Pendente"
        REAL = "real", "Diferença real"
        ERRO = "erro", "Erro de transcrição"
        DUVIDA = "duvida", "Dúvida"

    class Tipo(models.TextChoices):
        LETRAS = "letras", "Letras"
        ACENTO = "acento", "Acentos e til"
        CAIXA = "caixa", "Maiúsculas e minúsculas"
        PONTUACAO = "pontuacao", "Pontuação"
        HIFEN = "hifen", "Hífen ou espaço"

    class Elemento(models.TextChoices):
        VERSO = "verso", "Verso"
        TITULO = "head", "Título"
        CABECO = "header", "Cabeço"
        RECLAMO = "catch", "Reclamo"
        ASSINATURA = "sig", "Assinatura"

    elemento = models.CharField(max_length=10, choices=Elemento.choices, default=Elemento.VERSO)
    canto = models.PositiveSmallIntegerField()
    # versos: estrofe e verso; demais elementos: folha (nº da página no arquivo) e ordem nela
    estrofe = models.PositiveSmallIntegerField(default=0)
    verso = models.PositiveSmallIntegerField(default=0)
    folha = models.PositiveSmallIntegerField(default=0)
    ordem = models.PositiveSmallIntegerField(default=0)
    posicao = models.PositiveIntegerField(default=0, db_index=True, help_text="Ordem de leitura no arquivo.")
    tipo = models.CharField(max_length=10, choices=Tipo.choices)

    xml_a = models.TextField("A (pelicano à esquerda)")
    xml_b = models.TextField("B (pelicano à direita)")
    facs_a = models.CharField(max_length=200, blank=True)
    facs_b = models.CharField(max_length=200, blank=True)
    # altura aproximada do verso na imagem (0 = topo, 1 = base)
    altura_a = models.FloatField(default=0.5)
    altura_b = models.FloatField(default=0.5)

    ativa = models.BooleanField(
        default=True, help_text="Falso quando o verso deixou de diferir nos XMLs sem ter sido decidido.")
    situacao = models.CharField(max_length=10, choices=Situacao.choices, default=Situacao.PENDENTE)
    correcao_a = models.TextField(blank=True, help_text="Vazio = a transcrição de A está certa.")
    correcao_b = models.TextField(blank=True, help_text="Vazio = a transcrição de B está certa.")
    comentario = models.TextField("comentário", blank=True)
    revisor = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    revisado_em = models.DateTimeField(null=True, blank=True)

    reservado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    reservado_em = models.DateTimeField(null=True, blank=True)
    # sobe a cada mudança; impede que uma tela antiga sobrescreva algo mais novo
    edicao = models.PositiveIntegerField(default=0)

    class Meta:
        verbose_name = "diferença"
        ordering = ["posicao", "canto", "estrofe", "verso"]
        constraints = [
            models.UniqueConstraint(
                fields=["elemento", "canto", "estrofe", "verso", "folha", "ordem"], name="diferenca_unica"),
        ]

    def __str__(self):
        if self.elemento == self.Elemento.VERSO:
            return f"Canto {self.canto}, estrofe {self.estrofe}, verso {self.verso}"
        imagem = PurePosixPath(self.facs_a).stem or f"folha {self.folha}"
        return f"Canto {self.canto}, {self.get_elemento_display().lower()} ({imagem})"

    @property
    def chave(self):
        return (self.elemento, self.canto, self.estrofe, self.verso, self.folha, self.ordem)

    def conhecidos(self, lado):
        """Textos que este lado pode ter no XML sem invalidar a decisão.

        São o texto revisado e as correções dadas a ele, inclusive as que já
        foram aplicadas e depois mudadas, desde a última reabertura.
        """
        textos = {getattr(self, f"xml_{lado}"), getattr(self, f"texto_final_{lado}")}
        for h in self.historico.all():  # da mais nova para a mais antiga
            if h.situacao == self.Situacao.PENDENTE:
                break
            textos.add(getattr(h, f"correcao_{lado}"))
        return textos - {""}

    @property
    def texto_final_a(self):
        return self.correcao_a or self.xml_a

    @property
    def texto_final_b(self):
        return self.correcao_b or self.xml_b


class Decisao(models.Model):
    """Histórico: uma linha por decisão tomada (ou reabertura), nunca apagada."""

    diferenca = models.ForeignKey(Diferenca, on_delete=models.CASCADE, related_name="historico")
    quem = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    quando = models.DateTimeField(auto_now_add=True)
    situacao = models.CharField(max_length=10, choices=Diferenca.Situacao.choices)
    xml_a = models.TextField()
    xml_b = models.TextField()
    correcao_a = models.TextField(blank=True)
    correcao_b = models.TextField(blank=True)
    comentario = models.TextField(blank=True)

    class Meta:
        verbose_name = "decisão"
        verbose_name_plural = "decisões"
        ordering = ["-quando", "-pk"]
