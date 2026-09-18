# Radar Turístico del Caribe

Dashboard de llegadas de turistas y gasto turístico del Caribe, Cancún y Los Cabos,
construido **solo con fuentes oficiales**: bancos centrales, ministerios y autoridades
de turismo, institutos de estadística y autoridades de aviación civil.

**Dashboard publicado:** https://claude.ai/artifact/XcZREJ1HQUnZKLaTSu5bY1

## Qué contiene

- **16 destinos**: República Dominicana, Cancún, Los Cabos, Cartagena, Jamaica, Bahamas,
  Puerto Rico, Cuba, Aruba, Curazao, Barbados, Santa Lucía, Turcas y Caicos, Islas Caimán,
  Costa Rica y Panamá.
- **Llegadas mensuales** desde 2019 con la definición oficial de cada país.
- **Gasto turístico**: cuenta «Viajes» de la balanza de pagos o la estimación oficial
  equivalente, más gasto medio y estadía donde se publiquen.
- **Conectividad aérea**: pasajeros, asientos y vuelos desde Estados Unidos (US DOT T-100),
  operaciones por aeropuerto de la JAC (RD) y la AFAC (México).
- **República Dominicana con detalle propio**: 11 polos turísticos (llegadas por aeropuerto,
  ocupación, habitaciones, hoteles, % de huéspedes extranjeros) y mercados de origen por país.

## Cómo actualizarlo

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python run_all.py
```

`run_all.py` ejecuta cada extractor, valida y reconstruye `pipeline/dist/index.html`.
Si un extractor falla, los demás siguen y el JSON anterior de ese destino se conserva.
El resultado queda en `run_report.json`.

Opciones útiles:

```bash
.venv/bin/python run_all.py --only DO,DO_poles,AIR_bts   # solo algunos
.venv/bin/python run_all.py --skip KY                    # omitir el que necesita navegador
.venv/bin/python run_all.py --build-only                 # solo reconstruir el HTML
```

Para publicar el dashboard actualizado hay que subir `pipeline/dist/index.html` al artifact
existente, conservando su URL. La tarea programada en la nube lo hace cada semana.

## Estructura

```
data/<ID>.json           series nacionales por destino (llegadas, gasto, hotelería)
data/air/<ID>.json       conectividad aérea
data/markets/<ID>.json   llegadas por país de residencia
data/poles/DO.json       polos turísticos de República Dominicana
data/projections/history.json  registro de cada proyección publicada (no se edita a mano)
data/scripts/*.py        un extractor por fuente; re-descarga desde la URL oficial
data/raw/                archivos descargados (no se versionan)
pipeline/config.json     qué serie es la principal de cada destino, paridades fijas,
                         rezagos de publicación, universo de cuota de mercado
pipeline/template.html   el dashboard: HTML, CSS y JS, sin dependencias externas
pipeline/build.py        une los JSON + config + mapas y escribe pipeline/dist/index.html
pipeline/make_map*.py    generan los mapas base (Caribe y RD) desde world-atlas
run_all.py               orquestador: extrae, valida y construye
```

## Calendario de publicación de las fuentes

El dashboard muestra el calendario completo con el último dato y la fecha esperada del
siguiente. En resumen:

| Fuente | Frecuencia | Rezago |
|---|---|---|
| Banco Central de RD (llegadas, ocupación) | Mensual | 4–8 semanas |
| Junta de Aviación Civil de RD (operaciones) | Mensual | 2–3 semanas |
| MITUR RD (habitaciones, polos) | Mensual y trimestral | 1–6 semanas |
| DataTur y migración de México | Mensual | 5–7 semanas |
| AFAC México (operaciones) | Mensual | 4 semanas |
| Ministerio de Turismo de Bahamas | Mensual | 6–7 semanas |
| Migración Colombia y MinCIT (Cartagena) | Mensual | 7–8 semanas |
| Aerocivil y DANE (Cartagena) | Mensual | 5–7 semanas |
| US DOT T-100 (conectividad) | Mensual | ~3 meses |
| Bancos centrales (gasto, balanza de pagos) | Trimestral o anual | 3–5 meses |

## Reglas que sigue este repositorio

1. **Solo fuentes oficiales.** Nada de agregadores de pago, prensa ni estimaciones propias.
2. **Ningún valor se inventa ni se interpola.** Si un mes no está publicado, no aparece.
3. **Cada serie se valida** contra los totales que publica la propia fuente antes de entrar.
4. **Los indicadores calculados se marcan como tales** en el dashboard (llegadas por
   habitación, gasto por llegada, cuota de mercado).
5. **Las monedas locales se convierten** a dólares con la paridad fija oficial, indicada en
   la serie.

## Limitaciones conocidas

- **Jamaica** no tiene serie mensual nacional accesible: el sitio de estadísticas del Jamaica
  Tourist Board está bloqueado por firewall. Se usa como sustituto el tráfico aéreo desde
  Estados Unidos del US DOT, señalado como proxy en el dashboard.
- **Curazao** no tiene gasto turístico: el banco central bloquea descargas automatizadas.
- **Islas Caimán** publica las llegadas solo en un tablero Tableau sin descarga; requiere
  navegador headless, y es el extractor más frágil.
- **Los Cabos** no tiene gasto a nivel destino; solo existe el dato nacional de México.
- **Cartagena** tampoco: ni MinCIT, ni DANE, ni Corpoturismo publican gasto por ciudad, así que
  solo está el dato nacional de Colombia (anual). Sus llegadas miden extranjeros no residentes
  por ciudad de destino declarada, todas las vías, así que no son el mismo concepto que las de
  Cancún (turistas extranjeros por aeropuerto); las variaciones sí son comparables.
- **Ningún país publica gasto turístico por zona o polo.** En RD el detalle por polo llega
  hasta ocupación, habitaciones y llegadas por aeropuerto.
- Los niveles de llegadas **no son comparables entre destinos** porque cada país mide algo
  distinto (no residentes por vía aérea, turistas stopover, visitantes totales). Las
  variaciones sí lo son.

## Proyecciones

`pipeline/project.py` calcula proyecciones a 12 meses para RD, Cancún, Los Cabos, Cartagena,
Bahamas y Jamaica, y las guarda junto a los datos en cada corrida, así que toda cifra
proyectada es trazable a la corrida que la generó.

Tres métodos, según la serie:

| Método | Cuándo se usa | Cómo |
|---|---|---|
| Nowcast | hay un indicador oficial que se publica antes (RD: Junta de Aviación Civil) | razón media de los últimos 12 meses solapados |
| Estacional | la serie tiene 48+ meses de historia | participación media de cada mes en su año (3 años) sobre el nivel de los últimos 12 meses, con el crecimiento amortiguado a la mitad y topado en ±15% |
| Encadenada | la serie es corta (Cancún y Los Cabos arrancan en 2023) | se proyecta la serie larga de AFAC y se convierte con la razón histórica |

Las bandas salen del backtest, no de un supuesto: el método se corre hacia atrás en 24
orígenes y se toman los percentiles 10 y 90 de los errores relativos reales por horizonte.
El valor central se corrige por el sesgo medido, con un tope de ±10%. Los meses de pandemia
(2020-03 a 2021-06) se excluyen del cálculo estacional.

Error del método a 1 mes, medido: RD 3.5%, Cancún 6.3%, Los Cabos 7.6%, Cartagena 8.3%, Bahamas 8.7%,
Jamaica 17.6% (marcada como alta incertidumbre por el efecto del huracán Melissa).

En el dashboard la proyección va integrada en la gráfica del destino, siempre diferenciada:
zona sombreada, línea discontinua, banda de error, regla vertical en el último dato oficial
y etiqueta "proy." en la tarjeta de cierre de año. Las proyecciones **no entran** en la tabla
comparativa, el ranking ni la cuota de mercado. Los escenarios (demanda de EE.UU., capacidad
aérea con elasticidad supuesta de 0.6, y choque) se ajustan en la página y no alteran los datos.

### Registro de proyección contra realidad

Cada build guarda en `data/projections/history.json` la proyección que publicó para cada
destino: fecha, último dato real disponible, los 12 meses proyectados con su banda y el
cierre de año. Sólo se agrega un registro cuando la proyección cambia, es decir cuando llega
data oficial nueva; si se reconstruye el mismo día, se reemplaza el registro de ese día.
El archivo se versiona con los datos y **no se edita a mano**: es la memoria de lo que el
dashboard dijo en cada momento.

En la página, la tarjeta "Proyección contra realidad" de cada destino muestra:

- El último mes verificado: lo proyectado, lo publicado después y el error.
- Una gráfica de los últimos 24 meses con el dato oficial, la **simulación** del método a un
  mes (puntos grises: lo que el método habría proyectado con la data disponible en cada
  momento) y las **proyecciones registradas** (puntos de color: lo que efectivamente se
  publicó). Las dos se muestran separadas a propósito: la simulación da contexto desde el
  primer día, pero sólo el registro prueba lo que se dijo.
- La evolución del cierre de año proyectado, una fila por registro.

El registro empezó el 18 de septiembre de 2026. El primer mes verificable es agosto 2026,
cuando lo publiquen las fuentes.
