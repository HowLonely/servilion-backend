Tabla guias

Id= ID autoincremental de la basen de datos, no tomar en cuenta en nuevo sistema
empresa= nombre de la empresa, ahora deberia ser la FK del id de empresa
codigo= ??
ot= numero de OT
Nombre - rut = deberia solo estar el id del trabajador
patio-pieza-cargo-turno-telefono = deberia estar asociado a la tabla de trabajador
recepcion = fecha en que se recibe la ropa SUCIA en faena (se pistolea con codigo de barra y ahi se actualiza este dato en FAENA)
rlavanderia = fecha que se recibe la ropa SUCIA en LAVANDERIA (se pistolea con codigo de barra y ahi se actualiza este dato en Antofagasta)
entrega = fecha calculada por el sistema, en realidad deberia ser fecha tentativa de entrega, es segun el turno
prendas = cantidad de prendas, no deberia estar esta columna, deberia haber una intermedia de prendas_x_ot
peso=es el peso total de las prendas, no se pesan de manera individual solo el morral completo.
observacion=al momento de llegar las prendas a lavanderia se revisan y se confirma que estan todas las prendas declaradas por el trabajador
status=?
item= son los items asociados a la ot, esto se deberia gestionar con una tabla de items por ot o ropa por ot, etc.
revisadopor= usuario que revisa al momento de llegar a lavanderia
digitadopor= cuando llega la ropa a la laventeria viene con la OT fisica y con lo declarado por el trabajador, se revisa esta OT y recien se ingresa la OT
pesadopor= persona que pesa el cargo, no es relevante seguir manteniendolo
entregado= esta deberia ser la fecha real de entrega, en el caso de que se utilice la aplicacion de android donde pedira scanear 2 QR, deberia actualizarse ahi,m es cuando se entrega el cargo en la faena en la habitacion correspondiente.
ticket=???
ref= P1238 -: Es un codigo interno que se utiliza como OT pero mas pequeño
control=???
imagen=???

Flujo en programa:

flujo 1: contratos con entrega en habitacion (seguimiento de entrega)
flujo 2: contratos con entrega solo al cliente (sin seguimiento de entrega)

Flujo 1:
1.- el trabajador entrega un cargo con ropa con una OT (siempre que se les entrega ropa limpia, van OT en blanco para que despues las pueda rellenar) en la OT en blanco se debe rellenar con los cargos que van dedntro del morral, esto se hace con una OT fisica y manualmente.

2.- se entrega los cargos a un supervisor en faena (supervisor de la lavanderia en faena) y este se debe encargar de gestionar la llegada de los cargos a antofagasta, aqui no hay nada logico dentro del sistema.

3.- una vez que llegan los cargos a Antofagasta, se digitalizan en sistema, tanto la OT como las prendas, se ingresa la ot y prenda por prenda de la OT pistoleando, corroborando que estan todas las declaradas en la OT fisica aqui igual se ingresa la observacion en caso que falte una prenda etc. aqui tambien el ref se genera automaticamente, me indican que el ref tiene la inicial de la faena o empresa y el numero se resetea siempre a 1000 todas las semanas y ahi va subiendo, este numero es para reemplazar el numero de OT tan largo, este numero ref se pega en las etiquetas de ropa con un papel especial lavable como las etiquetas de ropa, asi que en este proceso se imprime y se ponen en la ropa.

4.- se realiza todo el proceso de lavado, aqui no interfiere el sistema

5.- en el empaque nuevamente del morral ya con la ropa limpia, se pistolea cada prenda que se esta cargando al morral para que finalmente el sistema valide que esta completo el morral a como llego o se declaro en la guia

6.- el morral se transporta a faena donde la recibe el supervisor de nuestra empresa, donde abre el morral verifica y debe marcar que llego el morral correctamente en sistema, ahi se debe registrar en la abse de datos la recepcion de ropa limpia (un nuevo campo que no está en access)

7.- luego para la entrega, aqui es donde toma protagonismo el APK android, las personas que realizan la entrega deberan escanear un QR el cual tendra el numero de OT se debera registrar en cola en la app android sin acceso a internet para luego enviarse al backend

