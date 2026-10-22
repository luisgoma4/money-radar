# Rediseño del repositorio: del radar a un ecosistema con máquinas

> **Para la siguiente sesión.** Este documento recoge lo que el usuario pidió el 22 de octubre de 2026. Todavía no hay nada implementado.
>
> Es un **rediseño de todo el repositorio** y hay que plantearlo con calma. El primer paso es responder las preguntas de la sección 6 y acordar los criterios de aceptación; después se escribe un plan por fases. **No se toca código antes de eso.**
>
> El repo es **público**: nada de este rediseño puede llevar nombres de personas ni datos personales al repo (ver la sección 6.4).

## 1. Qué quiere el usuario (en sus palabras, ordenado)

1. **Reorganizar el repo.** El ecosistema central (el grafo de entidades, caras, productos, socios, financiadores y convocatorias) debe ser el núcleo sobre el que funcionen **máquinas**. El buscador de financiación (el radar actual) es solo una de ellas. Las demás:
   - **Creador de documentación**: dossieres, memorias, fichas de proyecto, cartas y presentaciones generadas desde el grafo.
   - **Identificador de sinergias**: encuentra pares o grupos de nodos (caras, productos, socios, personas) que ganarían juntos.
   - **Medidor de fuerza causal**: mide qué nodos mueven más el ecosistema, para **desencadenar avalanchas de actividad** (el empujón mínimo con mayor efecto en cascada).
   - **Diseñador de proyectos comunes**: propone proyectos entre varias caras y socios, con roles, recursos y convocatorias.
   - **Creador de servicios**: convierte capacidades del ecosistema en servicios vendibles (oferta, precio, cliente).
   - **Gestor de marketing**: plan de comunicación, calendario y piezas por cara y por proyecto.
2. **Espacio de "personas".** El grafo no tiene personas. Cada nodo se vinculará a sus **propietarios y colaboradores**, y se generará un **grafo de red humana** paralelo al de proyectos, actores y **agentes**.
3. **Oráculo.** Debe identificar **las siguientes acciones** para movilizar el ecosistema, usando la nueva estructura (dos grafos más las máquinas) y el material que las máquinas generen para decidir.

## 2. Punto de partida (estado a 22-oct-2026, commit e6d9ade)

- **Radar.** Funciona de punta a punta: `ROWS` en `build.py`, `money.db`, `strategy.json`, `planner.py`, el dashboard en GitHub Pages, el oráculo local por voz y la rutina diaria en la nube.
- **Grafo del ecosistema.** Unos 135 nodos de 7 caras: Cloudy, SEMF, Causality, BranchOut, Fundación, CosmoVentures y El Gran DaKo. Es un DAG causal con pesos de 0 a 1 y un "diamante" de 6 esferas del mundo.
- **Capa causal.** Ya existe en `causal.py`: caminos de Wright, mediadores y confusores. Es la semilla del **medidor de fuerza causal**.
- **Notas privadas por cara.** Están en `proyectos/<cara>/`, fuera de git: los proyectos de BranchOut del Drive, el programa 2026–27 de SEMF y el plan de rediseño de la vista del grafo inspirado en themap (`proyectos/rediseno/plan_grafo_themap.md`). Es el modelo de lo que será privado.
- **Vista del grafo.** Bloqueada desde el 12-oct a la espera de un rediseño. Hay un plan en `proyectos/rediseno/plan_grafo_themap.md`:
  - layout fijo de "rueda de caras" calculado en el build;
  - plegado por caras;
  - "centrar aquí" con causas a la izquierda y efectos a la derecha;
  - etiquetas por importancia;
  - quitar las librerías de fuerzas.

  Ese rediseño debe encajar ahora en el rediseño mayor: dos grafos y máquinas.

## 2b. Deuda abierta que el rediseño debe resolver

- **Vista del grafo**: bloqueada desde el 12-oct (`reviews.json`, revisión de 0f1a587).
- **Versión en inglés**: interfaz, oráculo, textos de estrategia y documentación para colaboradores extranjeros (`reviews.json`, revisión de 082b3c1; preguntas 44–48).

## 3. Arquitectura que hay que decidir (borrador para discutir, no aprobado)

```
núcleo/              modelo del ecosistema: nodos, relaciones, estados, pesos (hoy strategy.json + planner.py)
  ├─ grafo de actores   caras, entidades, productos, socios, financiadores, convocatorias, hitos, esferas
  ├─ grafo humano       personas y agentes, vinculados a nodos del grafo de actores (PRIVADO)
  └─ capa causal        pesos, caminos, mediadores, fuerza, avalanchas
máquinas/            cada una lee el núcleo y escribe salidas versionadas
  ├─ radar (financiación)   lo que hoy es build.py + monitor.py + winners.py
  ├─ documentación
  ├─ sinergias
  ├─ fuerza causal / avalanchas
  ├─ proyectos comunes
  ├─ servicios
  └─ marketing
oráculo/             lee núcleo + salidas de las máquinas → próximas acciones (con confirmación)
vistas/              dashboard, mapa del ecosistema, red humana (públicas solo las partes públicas)
privado/             (git-ignored) personas, contactos, borradores, presupuestos
```

Principios a validar:
- **Contrato común de las máquinas**: entrada (el núcleo), salida (un artefacto con fecha), validación al estilo de `monitor.py` y estilo de ejecución (`python3 -I`, solo librería estándar).
- **Lo público y lo privado separados por diseño**, no por disciplina.
- Las reglas de siempre siguen valiendo: no borrar nunca (se archiva); el DAG causal; los textos del grafo en ASCII; nada de inventar importes ni fechas; solo una persona presenta solicitudes.

## 4. Grafo humano (borrador)

- **Nodos:** persona y agente (agentes de Claude o automatizaciones, p. ej. el arquitecto causal, el oráculo o la rutina diaria).
- **Relaciones con el grafo de actores:** propietario, colaborador, responsable, contacto, mentor, cliente...
- **Relaciones entre personas:** colabora con, presenta a, confía en, aprende de...
- **Para qué sirve:** quién puede mover qué nodo, cuellos de botella (una persona sostiene demasiado), huecos (nodos sin dueño), sinergias entre personas, compromisos de la ceremonia (hoy se registran como "rol | tarea | fecha").
- **Privacidad:** el repo es público. Las personas se guardan en privado. En público solo se muestran **roles** (como hoy con el PI del CSIC o el coordinador de la UNED) o datos agregados.

## 5. Oráculo: de "qué hago ahora" a "qué movimiento moviliza más"

Hoy `planner.what_now` y `oracle()` ordenan hitos por valor desbloqueado y proponen la siguiente convocatoria. Lo que se busca:
- **Acciones ordenadas por efecto en cascada** (fuerza causal) y por **quién puede hacerlas** (grafo humano: disponibilidad, rol, confianza).
- **Material para decidir**, generado por las máquinas: dossier de una sinergia, borrador de un proyecto común, ficha de servicio, plan de marketing.
- **Siempre con confirmación** (como ahora, con token de un solo uso), y los compromisos registrados como rol | tarea | fecha.

## 6. Preguntas para el usuario (responder antes de planificar)

### 6.1 Pendientes de esta sesión (no respondidas)

**Vista del grafo** (del plan basado en themap, `proyectos/rediseno/plan_grafo_themap.md`, sección 8):
1. **Financiadores:** ¿junto a las convocatorias que convocan, junto a su esfera más fuerte o una mezcla (propuesta 70/30)?
2. **3D:** ¿rehacer El Espacio con la misma geometría, congelarlo hasta aprobar el mapa o retirarlo? ¿Se eliminan `force-graph` y `3d-force-graph` de `vendor/`?
3. **Sectores:** ¿vale "una porción por cara, más Hitos, más Contexto y socios", con la Fundación en el centro? ¿Hay un orden fijo de las caras?
4. **Arrastrar nodos:** ¿mapa fijo (recomendado) o arrastre con retorno a su sitio?
5. **Columnas:** ¿se mantiene, o la sustituye "centrar aquí" (causas a la izquierda, efectos a la derecha)?
6. **Vista general:** ¿las convocatorias como puntos sin nombre, o siempre con nombre las de grado A?
7. **Etiquetas cortas:** ¿un campo `short` en `strategy.json` y `ROWS`, o derivarlas automáticamente?
8. **Vista previa** `grafo_preview.svg`: ¿se publica en Pages o se queda en local?
9. **Móvil:** ¿debe funcionar igual de bien que en escritorio o basta con que no se rompa?

**Datos de las caras:**

10. **SEMF:** ¿dónde está registrada (¿Valencia?) y dicen sus estatutos que su fin principal es la promoción cultural o artística? Decide si puede optar a la ayuda del Ajuntament de València (`valencia-proyectos-culturales-2027`).
11. **El Gran DaKo:** lo hemos dado por existente con domicilio en Madrid capital. ¿Es autónomo o SL? ¿Cuál es su epígrafe fiscal?

### 6.2 Reorganización del repo y núcleo
12. ¿Un solo repo con módulos (`núcleo/`, `máquinas/`, `oráculo/`, `vistas/`) o varios repos (núcleo privado y radar público)?
13. ¿Sigue siendo público el repo? ¿Qué partes deben seguir viéndose en GitHub Pages: el radar, el mapa de actores, la red humana (nunca con nombres)...?
14. El núcleo hoy es `strategy.json` (editado a mano) más SQLite. ¿Seguimos con JSON + SQLite y solo librería estándar, o se acepta otra base (p. ej. SQLite como fuente única, o un servicio con auth)?
15. ¿El radar se convierte en "una máquina más" con el mismo contrato, o se queda como está y las demás máquinas se le acoplan?
16. Nombre del sistema y de sus partes (¿"Ecosistema Delfina León"? ¿"money-radar" pasa a ser solo el nombre del radar?).
17. ¿La rutina diaria en la nube ejecutará otras máquinas además del radar? Si es así, ¿cuáles y con qué frecuencia?
18. ¿Qué entra en el núcleo además de lo actual: eventos (la idea pendiente de la pestaña de eventos), clientes, servicios, contactos de prensa...?

### 6.3 Las máquinas
19. Orden de prioridad de las 6 máquinas nuevas (documentación, sinergias, fuerza causal, proyectos comunes, servicios, marketing). ¿Cuál es la primera?
20. Para cada máquina: ¿qué decisión concreta te ayuda a tomar, y qué artefacto esperas (formato, extensión, idioma)?
21. **Documentación:** ¿qué documentos (dossier por cara, memoria de una convocatoria, ficha de proyecto, carta de apoyo, presentación)? ¿En qué formatos (Markdown, .docx, Google Docs, artefacto)? ¿Se guardan en privado en `solicitudes/` y `proyectos/`?
22. **Sinergias:** ¿entre qué tipos de nodo (caras, productos, socios, personas, convocatorias)? ¿Qué cuenta como sinergia: complementariedad de capacidades, cliente común, convocatoria común, espacio común...? ¿Cómo se valida una sinergia propuesta: en la ceremonia, por el arquitecto causal...?
23. **Fuerza causal y avalanchas:** ¿qué es "activar" un nodo (un hito hecho, una solicitud presentada, una persona disponible, un euro conseguido)? ¿La avalancha se mide sobre el DAG con los pesos actuales (propagación de Wright, umbrales en cascada) o hace falta un modelo nuevo? ¿Con qué horizonte temporal?
24. **Proyectos comunes:** ¿qué datos mínimos tiene un proyecto común (caras, socios, personas y roles, recursos, calendario, convocatorias objetivo)? ¿Se convierte en un nodo del grafo al aprobarse?
25. **Servicios:** ¿para quién (empresas, ayuntamientos, universidades, familias)? ¿Necesita precio y modelo de negocio? ¿Se publica como catálogo (enlazado con la web de la empresa)?
26. **Marketing:** ¿qué canales (web de cada cara, redes, newsletter, prensa)? ¿Genera piezas, solo calendario y plan, o mide resultados? ¿Hay acceso a cuentas o analíticas, o solo propone?
27. ¿Las máquinas las ejecuta Claude (skills o agentes), código determinista o una mezcla? ¿Qué parte debe funcionar sin Claude?

### 6.4 Grafo humano y privacidad
28. ¿Dónde viven los datos de personas: carpeta git-ignored, repo privado aparte, Drive, base local cifrada...? ¿Quién tiene acceso?
29. ¿Qué datos de cada persona (nombre, rol, habilidades, disponibilidad, contacto, intereses, entidad)? ¿Hay consentimiento de cada persona para estar en el grafo?
30. ¿Qué relaciones persona ↔ nodo y persona ↔ persona queremos? ¿Llevan peso como las causales?
31. ¿Los agentes (Claude, la rutina, el oráculo, el arquitecto causal) son nodos del grafo humano, de un tercer grafo o un tipo especial?
32. ¿Qué se muestra en público: solo roles, recuentos, o nada?
33. ¿Hay que cumplir algo formal (RGPD, cláusula informativa a colaboradores) antes de guardar datos de personas?
34. ¿La red humana incluye personas externas (contactos en financiadores, ayuntamientos, socios), o solo el equipo y los colaboradores?

### 6.5 Oráculo
35. ¿Qué es una "acción" para el oráculo: un hito, una tarea con dueño, una solicitud, un contacto que hacer, una pieza de marketing...?
36. ¿Cómo se ordenan las acciones: efecto en cascada, urgencia (plazos), coste o esfuerzo, quién está disponible? ¿Con qué pesos?
37. ¿El oráculo asigna acciones a personas concretas o a roles? En el repo público sería por roles.
38. ¿Debe poder lanzar máquinas (p. ej. "prepárame el dossier de esta sinergia") con confirmación, como hoy lanza acciones?
39. ¿Cada cuánto: en la ceremonia de cada 14 días, a diario con la rutina, o a demanda? ¿Avisa (Telegram, correo, calendario)?
40. ¿Cómo se mide si una acción movilizó el ecosistema (seguimiento del compromiso, cambio en el grafo, dinero conseguido)?

### 6.6 Idioma: versión en inglés (deuda abierta, `reviews.json`, 22-oct-2026)
Hay colaboradores extranjeros: SEMF es multinacional y CosmoVentures está en París. Hoy la interfaz, el oráculo y los textos de estrategia están en español o mezclan idiomas.
44. ¿Qué tiene que existir en inglés: el dashboard, El Espacio, el oráculo de voz, las salidas de las máquinas (dossieres, planes), la documentación (README para colaboradores, REDISENO.md)?
45. ¿Selector ES/EN en todas las vistas (recordado por visitante) o una versión inglesa aparte?
46. ¿Cómo se guardan los textos bilingües del núcleo: campos `label_en` / `detail_en` en `strategy.json`, un archivo de traducciones, o traducción automática revisada? ¿Debe `monitor.py` avisar de las traducciones que falten?
47. ¿El idioma de trabajo de los datos y del repo: el inglés (como las filas del radar) o el español? ¿Y el de las ceremonias y los compromisos?
48. ¿Hacen falta otros idiomas (francés para CosmoVentures, valenciano o catalán para las convocatorias de València)?

### 6.7 Proceso
49. ¿Fases y criterio para cerrar cada una? Propuesta: una revisión tuya en un navegador real al final de cada fase.
50. ¿Qué se congela mientras dura el rediseño: la rutina diaria sigue funcionando, la vista del grafo queda bloqueada...?
51. ¿Plazo o fecha objetivo? ¿Alguna convocatoria cercana que obligue a priorizar una máquina (p. ej. documentación para EIC o NLnet)?

## 7. Siguiente sesión: por dónde empezar

1. Leer este documento, `CLAUDE.md` y `proyectos/rediseno/plan_grafo_themap.md`.
2. Repasar con el usuario las preguntas de la sección 6 (empezando por 6.2, 6.4, 6.6 y la pregunta 19), sin escribir código.
3. Con las respuestas, redactar el plan por fases: arquitectura, contrato de las máquinas, modelo de datos del grafo humano, oráculo y vista del grafo integrada. Incluir criterios de aceptación.
4. Mantener vivo el radar diario mientras tanto.
