from django.contrib import admin
from django.urls import path

from common.node import is_edge
from core.api import api

urlpatterns = [
    path('api/', api.urls),
]

# El admin de Django es para el staff de Servilion en la nube. En la red de la
# planta no se expone: todo lo que se opera ahí pasa por la API.
if not is_edge():
    urlpatterns.append(path('admin/', admin.site.urls))
