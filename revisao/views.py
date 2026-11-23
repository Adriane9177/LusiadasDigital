from datetime import timedelta
from pathlib import PurePosixPath
from urllib.parse import urlencode

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import Count, F, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone

from .models import Decisao, Diferenca
from .versos import de_edicao, para_edicao, realcar

RESERVA = timedelta(minutes=30)
S = Diferenca.Situacao


def _filtros(dados):
    """Filtros válidos vindos da URL ou do formulário."""
    filtros = {}
    if dados.get("canto", "") in {str(n) for n in range(1, 11)}:
        filtros["canto"] = dados["canto"]
    if dados.get("tipo") in Diferenca.Tipo.values:
        filtros["tipo"] = dados["tipo"]
    if dados.get("situacao") == S.DUVIDA:
        filtros["situacao"] = "duvida"
    return filtros


def _com_filtros(url, filtros, **extra):
    consulta = urlencode({**filtros, **extra})
    return f"{url}?{consulta}" if consulta else url


def _livre(usuario):
    limite = timezone.now() - RESERVA
    return Q(reservado_por=None) | Q(reservado_por=usuario) | Q(reservado_em__lt=limite)


def _url_imagem(facs):
    if not facs:
        return ""
    caminho = PurePosixPath(facs)
    return f"{settings.MEDIA_URL}facsimile/{caminho.parent.name}/{caminho.name}"


@login_required
def painel(request):
    contagens = dict(
        total=Count("pk"),
        feitas=Count("pk", filter=Q(situacao__in=[S.REAL, S.ERRO])),
        reais=Count("pk", filter=Q(situacao=S.REAL)),
        erros=Count("pk", filter=Q(situacao=S.ERRO)),
        duvidas=Count("pk", filter=Q(situacao=S.DUVIDA)),
    )
    ativas = Diferenca.objects.filter(ativa=True)
    return render(request, "revisao/painel.html", {
        "titulo": "Revisão das transcrições",
        "total": ativas.aggregate(**contagens),
        "cantos": ativas.order_by("canto").values("canto").annotate(**contagens),
        "tipos": Diferenca.Tipo.choices,
        "numeros": range(1, 11),
        "duvidas": ativas.filter(situacao=S.DUVIDA).select_related("revisor"),
        "minhas": Diferenca.objects.filter(revisor=request.user).order_by("-revisado_em")[:15],
    })


@login_required
def proxima(request):
    """Reserva o próximo verso livre e abre a tela dele."""
    filtros = _filtros(request.GET)
    fila = Diferenca.objects.filter(ativa=True, **{"situacao": S.PENDENTE, **filtros})
    depois = request.GET.get("depois", "")
    atual = Diferenca.objects.filter(pk=depois).first() if depois.isdecimal() else None
    grupos = [fila]
    if atual:
        # a partir do verso atual; chegando ao fim, volta ao começo
        seguintes = Q(canto__gt=atual.canto) | Q(canto=atual.canto, estrofe__gt=atual.estrofe) | Q(
            canto=atual.canto, estrofe=atual.estrofe, verso__gt=atual.verso)
        grupos = [fila.filter(seguintes), fila.exclude(seguintes).exclude(pk=atual.pk)]
        Diferenca.objects.filter(pk=atual.pk, reservado_por=request.user).update(
            reservado_por=None, reservado_em=None)

    for grupo in grupos:
        for pk in grupo.filter(_livre(request.user)).values_list("pk", flat=True)[:20]:
            # o update condicional garante que só uma pessoa pega cada verso
            if fila.filter(_livre(request.user), pk=pk).update(
                    reservado_por=request.user, reservado_em=timezone.now()):
                return redirect(_com_filtros(reverse("revisao:revisar", args=[pk]), filtros))
    messages.info(request, "Não há mais versos disponíveis com esse filtro.")
    return redirect("revisao:painel")


@login_required
def revisar(request, pk):
    d = get_object_or_404(Diferenca.objects.select_related("revisor", "reservado_por"), pk=pk)
    filtros = _filtros(request.POST if request.method == "POST" else request.GET)
    campo_a, campo_b = para_edicao(d.texto_final_a), para_edicao(d.texto_final_b)
    comentario, erro = d.comentario, ""
    edicao = d.edicao

    if request.method == "POST":
        campo_a, campo_b = request.POST.get("texto_a", ""), request.POST.get("texto_b", "")
        comentario = request.POST.get("comentario", "").strip()
        # ao reexibir com erro, mantém a edição enviada: reenviar não fura a trava
        edicao = request.POST.get("edicao", "")
        erro = _decidir(request, d, campo_a, campo_b, comentario)
        if not erro:
            messages.success(request, f"{d}: decisão registrada.")
            return redirect(_com_filtros(reverse("revisao:proxima"), filtros, depois=d.pk))

    trechos_a, trechos_b = realcar(d.xml_a, d.xml_b)
    outra_pessoa = None
    if (d.reservado_por and d.reservado_por != request.user and d.reservado_em
            and d.reservado_em > timezone.now() - RESERVA):
        outra_pessoa = d.reservado_por
    return render(request, "revisao/revisar.html", {
        "titulo": str(d),
        "d": d,
        "edicao": edicao,
        "erro": erro,
        "comentario": comentario,
        "outra_pessoa": outra_pessoa,
        "filtros": filtros,
        "url_pular": _com_filtros(reverse("revisao:proxima"), filtros, depois=d.pk),
        "historico": d.historico.select_related("quem")[:10],
        "lados": [
            {"id": "a", "nome": "A · pelicano à esquerda", "imagem": _url_imagem(d.facs_a),
             "altura": f"{d.altura_a:.4f}", "trechos": trechos_a, "campo": campo_a,
             "original": para_edicao(d.xml_a)},
            {"id": "b", "nome": "B · pelicano à direita", "imagem": _url_imagem(d.facs_b),
             "altura": f"{d.altura_b:.4f}", "trechos": trechos_b, "campo": campo_b,
             "original": para_edicao(d.xml_b)},
        ],
    }, status=400 if erro else 200)


def _decidir(request, d, campo_a, campo_b, comentario):
    """Grava a decisão. Devolve a mensagem de erro, ou "" se deu certo."""
    acao = request.POST.get("acao")
    if acao not in {S.REAL, S.ERRO, S.DUVIDA}:
        return "Escolha uma das opções."
    try:
        xml_a, xml_b = de_edicao(campo_a), de_edicao(campo_b)
    except ValueError as erro:
        return str(erro)
    # compara na forma normalizada: só espaços ou forma Unicode não são correção
    correcao_a = "" if xml_a == de_edicao(para_edicao(d.xml_a)) else xml_a
    correcao_b = "" if xml_b == de_edicao(para_edicao(d.xml_b)) else xml_b
    if acao == S.ERRO and not (correcao_a or correcao_b):
        return "Para registrar um erro de transcrição, corrija o texto de A, de B ou de ambos."
    if acao == S.REAL and (correcao_a or correcao_b):
        return "Você alterou o texto: use “Salvar correção”, ou restaure o texto para confirmar a diferença."
    if acao == S.DUVIDA:
        if not comentario:
            return "Escreva no comentário qual é a dúvida."
        if correcao_a or correcao_b:
            return "Em caso de dúvida o texto não é alterado: restaure-o e ponha a sugestão no comentário."

    edicao = request.POST.get("edicao", "")
    with transaction.atomic():
        gravou = edicao.isdecimal() and Diferenca.objects.filter(pk=d.pk, edicao=int(edicao)).update(
            situacao=acao, correcao_a=correcao_a, correcao_b=correcao_b, comentario=comentario,
            revisor=request.user, revisado_em=timezone.now(), ativa=True,
            reservado_por=None, reservado_em=None, edicao=F("edicao") + 1)
        if not gravou:
            return "Este verso foi alterado enquanto você o revisava. Abra-o de novo antes de decidir."
        Decisao.objects.create(
            diferenca=d, quem=request.user, situacao=acao, xml_a=d.xml_a, xml_b=d.xml_b,
            correcao_a=correcao_a, correcao_b=correcao_b, comentario=comentario)
    return ""
