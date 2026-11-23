from django.conf import settings
from django.db import models


class Diferenca(models.Model):
    """Um verso cujo texto difere entre as duas edições de 1572.

    Os textos são o conteúdo XML do <l> (com <lb/>, &amp; etc.), tal como está
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

    canto = models.PositiveSmallIntegerField()
    estrofe = models.PositiveSmallIntegerField()
    verso = models.PositiveSmallIntegerField()
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
        ordering = ["canto", "estrofe", "verso"]
        constraints = [
            models.UniqueConstraint(fields=["canto", "estrofe", "verso"], name="diferenca_verso_unico"),
        ]

    def __str__(self):
        return f"Canto {self.canto}, estrofe {self.estrofe}, verso {self.verso}"

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
