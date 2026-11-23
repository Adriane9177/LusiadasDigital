from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from revisao.sincronizar import sincronizar
from revisao.versos import ErroDeLeitura


class Command(BaseCommand):
    help = ("Compara os XMLs das duas edições e põe a fila de revisão em dia. "
            "Pode ser repetido à vontade: as decisões já tomadas são preservadas.")

    def add_arguments(self, parser):
        pasta = settings.BASE_DIR / "LusiadasTextos"
        parser.add_argument("--a", default=pasta / "LusiadasEsquerda.xml")
        parser.add_argument("--b", default=pasta / "LusiadasDireita.xml")

    def handle(self, *args, **opcoes):
        try:
            conta = sincronizar(opcoes["a"], opcoes["b"])
        except (ErroDeLeitura, OSError, ValueError) as erro:
            raise CommandError(str(erro))
        self.stdout.write(self.style.SUCCESS(
            f"{conta['novas']} nova(s), {conta['atualizadas']} atualizada(s), "
            f"{conta['reabertas']} reaberta(s); {conta['pendentes']} pendente(s)."))
