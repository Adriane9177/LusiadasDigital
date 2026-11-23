"""Põe a fila de revisão em dia com os XMLs, preservando as decisões."""
from collections import Counter

from django.db import transaction

from .models import Decisao, Diferenca
from .versos import ErroDeLeitura, classificar, ler_versos, texto_plano

S = Diferenca.Situacao
CAMPOS_DE_IMAGEM = ("facs_a", "facs_b", "altura_a", "altura_b")


def _estado(d, campos):
    return tuple(getattr(d, c) for c in campos)


@transaction.atomic
def sincronizar(arquivo_a, arquivo_b):
    versos_a, versos_b = ler_versos(arquivo_a), ler_versos(arquivo_b)
    if set(versos_a) != set(versos_b):
        so_um = sorted(set(versos_a) ^ set(versos_b))
        raise ErroDeLeitura(f"Versos presentes em só uma das versões: {so_um[:10]}")

    existentes = {(d.canto, d.estrofe, d.verso): d for d in Diferenca.objects.all()}
    conta = Counter()
    novas = []
    for chave in sorted(versos_a):
        a, b = versos_a[chave], versos_b[chave]
        ta, tb = texto_plano(a.xml), texto_plano(b.xml)
        difere = ta != tb
        d = existentes.get(chave)
        if d is None:
            if difere:
                novas.append(Diferenca(
                    canto=chave[0], estrofe=chave[1], verso=chave[2], tipo=classificar(ta, tb),
                    xml_a=a.xml, xml_b=b.xml, facs_a=a.facs, facs_b=b.facs,
                    altura_a=a.altura, altura_b=b.altura))
            continue

        conteudo = ("xml_a", "xml_b", "ativa", "situacao")
        antes, antes_img = _estado(d, conteudo), _estado(d, CAMPOS_DE_IMAGEM)
        d.facs_a, d.facs_b, d.altura_a, d.altura_b = a.facs, b.facs, a.altura, b.altura

        # a decisão vale enquanto cada lado for o texto revisado ou a correção dele
        vale = a.xml in {d.xml_a, d.texto_final_a} and b.xml in {d.xml_b, d.texto_final_b}
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
    Diferenca.objects.bulk_create(novas)
    conta["novas"] = len(novas)
    conta["pendentes"] = Diferenca.objects.filter(ativa=True, situacao=S.PENDENTE).count()
    return conta
