from django.contrib import admin

from .models import Decisao, Diferenca


class DecisaoInline(admin.TabularInline):
    model = Decisao
    extra = 0
    can_delete = False

    def has_add_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(Diferenca)
class DiferencaAdmin(admin.ModelAdmin):
    """Só consulta: as decisões são tomadas em /revisao/."""
    list_display = ("__str__", "tipo", "situacao", "ativa", "revisor", "revisado_em")
    list_filter = ("situacao", "ativa", "canto", "tipo", "revisor")
    search_fields = ("xml_a", "xml_b", "comentario")
    inlines = [DecisaoInline]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
