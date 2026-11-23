from django.contrib.auth import views as auth
from django.urls import path

from . import views

app_name = "revisao"

urlpatterns = [
    path("", views.painel, name="painel"),
    path("proxima/", views.proxima, name="proxima"),
    path("<int:pk>/", views.revisar, name="revisar"),
    path("entrar/", auth.LoginView.as_view(template_name="revisao/entrar.html"), name="entrar"),
    path("sair/", auth.LogoutView.as_view(next_page="revisao:entrar"), name="sair"),
]
