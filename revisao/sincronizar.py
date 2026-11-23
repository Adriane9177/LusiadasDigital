"""Põe a fila de revisão em dia com os XMLs, preservando as decisões."""
from collections import Counter

from django.db import transaction

from .models import Decisao, Diferenca
from .versos import ErroDeLeitura, classificar, ler_trechos, texto_plano

S = Diferenca.Situacao
CAMPOS_DE_IMAGEM = ("facs_a", "facs_b", "altura_a", "altura_b", "posicao")


def _estado(d, campos):
    return tuple(getattr(d, c) for c in campos)


@transaction.atomic
def sincronizar(arquivo_a, arquivo_b):
    versos_a, versos_b = ler_trechos(arquivo_a), ler_trechos(arquivo_b)
    if set(versos_a) != set(versos_b):
        so_um = sorted(set(versos_a) ^ set(versos_b))
        raise ErroDeLeitura(f"Trechos presentes em só uma das versões: {so_um[:10]}")

    existentes = {d.chave: d for d in Diferenca.objects.prefetch_related("historico")}
    conta = Counter()
    novas = []
    for chave in versos_a:
        a, b = versos_a[chave], versos_b[chave]
        ta, tb = texto_plano(a.xml), texto_plano(b.xml)
        difere = ta != tb
        d = existentes.get(chave)
        if d is None:
            if difere:
                novas.append(Diferenca(
                    elemento=chave[0], canto=chave[1], estrofe=chave[2], verso=chave[3],
                    folha=chave[4], ordem=chave[5], posicao=a.posicao, tipo=classificar(ta, tb),
                    xml_a=a.xml, xml_b=b.xml, facs_a=a.facs, facs_b=b.facs,
                    altura_a=a.altura, altura_b=b.altura))
            continue

        conteudo = ("xml_a", "xml_b", "ativa", "situacao")
        antes, antes_img = _estado(d, conteudo), _estado(d, CAMPOS_DE_IMAGEM)
        d.facs_a, d.facs_b, d.altura_a, d.altura_b = a.facs, b.facs, a.altura, b.altura
        d.posicao = a.posicao

        # a decisão vale enquanto cada lado for o texto revisado ou uma correção dele
        vale = a.xml in d.conhecidos("a") and b.xml in d.conhecidos("b")
        if d.situacao == S.PENDENTE or not vale:
            if d.situacao != S.PENDENTE:
                conta["reabertas"] += 1
                Decisao.objects.create(
                    diferenca=d, situacao=S.PENDENTE, xml_a=a.xml, xml_b=b.xml,
                    comentario="Reaberta: o texto mudou no XML depois da revisão.")
                d.situacao = S.PENDENTE
                d.correcao_a = d.correcao_b = ""
                d.revisor = d.revisado_em = None
            d.xml_a, d.xml_b, d.ativa = a.xml, b.xml, difere
            if difere:
                d.tipo = classificar(ta, tb)
        if antes != _estado(d, conteudo):
            d.edicao += 1  # invalida telas abertas com o texto antigo
            conta["atualizadas"] += 1
            d.save()
        elif antes_img != _estado(d, CAMPOS_DE_IMAGEM):
            d.save(update_fields=CAMPOS_DE_IMAGEM)
    for chave, d in existentes.items():
        if chave not in versos_a and d.ativa:   # o trecho deixou de existir nos XMLs
            d.ativa, d.edicao = False, d.edicao + 1
            d.save()
            conta["atualizadas"] += 1
    Diferenca.objects.bulk_create(novas)
    conta["novas"] = len(novas)
    conta["pendentes"] = Diferenca.objects.filter(ativa=True, situacao=S.PENDENTE).count()
    return conta
