# Concepto UX/UI — Aplicación Agéntica de Tenis de Mesa

## 1. Visión del producto

La aplicación debe evolucionar desde una aplicación de **gestión de datos de tenis de mesa** hacia un **compañero de rendimiento basado en IA**.

### Principio central

> **The Coach is the UI**

El usuario no debería navegar por módulos para descubrir qué tiene que hacer. El agente debe:

**Observar → Analizar → Interpretar → Recomendar → Actuar → Medir → Aprender → Volver a recomendar**

Los datos existentes —objetivos, partidos, rivales, ejercicios, entrenamientos, métricas, torneos, notas, etc.— pasan a ser el **contexto que utiliza el agente**, no el centro de la experiencia.

---

# 2. Principios UX

## 2.1. Menos navegación, más intención

Evitar una home con tarjetas para:

- Objetivos
- Resultados
- Oponentes
- Ejercicios
- Notas
- Calendario
- Torneos
- Noticias

En su lugar:

- **Hoy**
- **Entrenar**
- **Progreso**
- **Coach IA**

El resto debe aparecer contextualmente.

---

## 2.2. El agente debe ser proactivo

No limitarse a responder preguntas.

Ejemplo:

> "He analizado tus últimos 6 partidos. Tu principal oportunidad de mejora está en la recepción de saque corto."

El agente puede entonces:

1. Explicar el problema.
2. Relacionarlo con los objetivos.
3. Proponer ejercicios.
4. Modificar la siguiente sesión.
5. Medir el resultado.
6. Actualizar el análisis del jugador.

---

## 2.3. Reducir al mínimo la introducción de datos

Utilizar:

- Voz.
- Texto libre.
- Selecciones rápidas.
- Conversaciones.
- Datos existentes.
- Automatización.
- Análisis de vídeo/partidos cuando esté disponible.

Ejemplo:

> Usuario: "Perdí 3-2. Estuve bien con mi saque pero no podía devolver su saque corto de derecha."

El agente transforma la conversación en datos estructurados:

```text
Resultado: 2–3
Oponente: Javier
Saque: positivo
Recepción: problema detectado
Situación: saque corto de derecha
Observación: dificultad en recepción
```

El agente pregunta únicamente lo necesario:

> "¿Crees que fue principalmente un problema técnico o de decisión?"

---

# 3. Conceptos visuales

Se han definido tres direcciones visuales que pueden implementarse como variantes del mismo producto.

---

# CONCEPTO 1 — AI COACH 360

## Idea

El producto se percibe como un **entrenador inteligente siempre presente**.

### Personalidad

- Premium
- Deportivo
- Tecnológico
- Inmersivo
- Proactivo

### Estética

- Dark mode como experiencia principal.
- Fondo oscuro.
- Acentos cyan/teal.
- Gradientes sutiles.
- Efectos de luz alrededor del agente.
- Gráficos de rendimiento minimalistas.
- Animaciones suaves.
- Avatar/representación visual del Coach IA.

### Elemento diferencial

El agente aparece como una entidad reconocible dentro de la aplicación.

No debe parecer un chatbot genérico.

---

## Home jugador

Contenido prioritario:

```text
Buenos días, Alex

              78
        PERFORMANCE

        ↑ 6% esta semana

----------------------------

✦ TU COACH

"He analizado tus últimos partidos.
Hoy quiero trabajar contigo
la recepción."

[ VER ANÁLISIS ]

----------------------------

19:00
Entrenamiento · 35 min

Recepción + transición

[ EMPEZAR ]
```

### Objetivo

El jugador debe entender en menos de 5 segundos:

1. Cómo está.
2. Qué debería hacer.
3. Por qué.
4. Cómo empezar.

---

## Dashboard web

La versión web puede ampliar la experiencia:

```text
Sidebar
  Inicio
  Mi progreso
  Entrenamientos
  Partidos
  Oponentes
  Ejercicios
  Calendario
  Torneos
  Noticias
  Notas

Main
  Performance
  AI Insights
  Próxima sesión
  Último partido
  Próximos eventos
```

El sidebar debe ser secundario frente al contenido generado por el Coach.

---

## Coach IA

Debe permitir:

- Analizar partido.
- Analizar progreso.
- Preparar entrenamiento.
- Preparar torneo.
- Analizar oponente.
- Recomendar ejercicios.
- Crear/modificar sesiones.
- Actualizar objetivos.
- Resolver dudas.
- Registrar información mediante conversación.

### Ejemplo

> "He detectado una caída en tu recepción de saque corto."

```text
48% → 61%

Últimos 8 partidos

14 de 23 situaciones
terminaron en pérdida del punto.

Mi recomendación:

Recepción → Primer ataque

[ ENTRENAR ESTO ]
```

---

# CONCEPTO 2 — FOCUS FLOW

## Idea

La aplicación se convierte en una experiencia **minimalista, conversacional y orientada a tareas**.

El usuario no navega por funcionalidades.

Habla con el Coach y selecciona acciones.

### Personalidad

- Minimalista.
- Moderna.
- Premium.
- Conversacional.
- Menos información visual.
- Mayor protagonismo de IA.

### Estética

- Dark mode.
- Negro/grafito.
- Acentos violetas.
- Glow alrededor del agente.
- Grandes espacios vacíos.
- Pocas cards.
- Tipografía grande.
- Interacciones muy sencillas.

---

## Home

```text
Hola, Alex 👋

¿En qué puedo ayudarte hoy?

[ Quiero entrenar ]
  Recomienda una sesión

[ Analiza mi progreso ]
  ¿Cómo voy esta semana?

[ Registrar un partido ]
  Añadir resultado rápidamente

[ Tengo una pregunta ]
  Consulta al Coach

       🎙️
   Escribe o habla...
```

---

## Flujo conversacional

Ejemplo:

```text
Usuario:
Analiza mi último partido.

Coach:
Claro. Ya tengo el análisis
del partido contra Carlos.

Resultado:
3–1

Principales conclusiones:

✓ Buen servicio
✓ Buen primer ataque
⚠ Problemas en recepción

[ Ver informe completo ]
```

El agente puede continuar:

> "Además, he detectado que el problema aparece especialmente contra saque corto."

---

## Ventaja

Este concepto es el que mejor representa una aplicación **AI-native**.

El número de pantallas puede reducirse considerablemente porque muchas operaciones se realizan mediante conversación.

---

## Web

En desktop se puede combinar:

```text
Sidebar

Chat con Coach IA

              +              Performance
                              rápida
                             
                              Próximo evento
```

La conversación es el eje central y los datos aparecen como contexto.

---

# CONCEPTO 3 — GAME PLAN

## Idea

Convertir la evolución del jugador en un **viaje visual de progreso**.

Inspiración conceptual:

- Gamificación.
- Videojuegos.
- Progresión.
- Misiones.
- Niveles.
- Logros.
- Desafíos.

### Personalidad

- Dinámica.
- Motivacional.
- Juvenil.
- Competitiva.
- Visual.
- Más emocional.

### Estética

- Dark mode.
- Negro/grafito.
- Acentos naranja.
- Elementos dorados.
- Avatares.
- Insignias.
- Barras de progreso.
- Misiones.
- Camino visual de evolución.

---

# Home

```text
Alex

Nivel 12

2.350 / 3.000 XP

----------------------------

MISIÓN DIARIA

Mejora tu recepción

0 / 5 ejercicios

150 XP

[ EMPEZAR ]

----------------------------

TU CAMINO

✓ Técnica

→ Táctica

○ Físico

🔒 Mental
```

---

## Player Journey

El jugador tiene un mapa de desarrollo:

```text
                 🏆
                 │
              Táctica
                 │
                 ●
                /
          Transición
             ●
            /
        Recepción
           ●
          /
        Saque
           ●
```

El agente determina qué nodo debe abordar el jugador.

---

## Misiones

Ejemplos:

```text
Mejora tu servicio
6 / 10 sesiones
200 XP

Juega 3 partidos
2 / 3
150 XP

Asiste a 4 entrenamientos
3 / 4
100 XP
```

Las recompensas deben representar progreso deportivo real cuando sea posible.

No abusar de puntos arbitrarios.

---

# 4. Concepto recomendado

La dirección recomendada es una combinación de los tres conceptos.

## Base

**AI COACH 360**

Para crear la identidad de producto y convertir al agente en protagonista.

## Interacción

**FOCUS FLOW**

Para reducir navegación y permitir interacción natural.

## Progresión

**GAME PLAN**

Para representar visualmente la evolución del jugador y mejorar motivación.

---

# 5. Arquitectura UX recomendada

## Navegación jugador

Máximo cuatro elementos principales:

```text
HOY
ENTRENAR
PROGRESO
COACH ✦
```

### Hoy

Todo lo relevante en este momento:

- Estado actual.
- Insight del Coach.
- Próximo entrenamiento.
- Próximo torneo.
- Recomendación.
- Alertas relevantes.

### Entrenar

- Sesión actual.
- Programas.
- Ejercicios.
- Historial.
- Sesiones recomendadas por IA.

### Progreso

- Evolución.
- Técnica.
- Táctica.
- Física.
- Mental.
- Partidos.
- Rivales.
- Objetivos.
- Tendencias.

### Coach

- Chat.
- Voz.
- Análisis.
- Recomendaciones.
- Acciones.

---

# 6. Objetivos

"Objetivos" no debería ser necesariamente una sección principal.

El usuario puede decir:

> "Quiero mejorar mi revés."

El agente crea:

```text
Objetivo:
Consistencia de revés

Nivel actual:
6.4 / 10

Objetivo:
8 / 10

Horizonte:
4 semanas

Plan:
3 sesiones / semana
```

El objetivo pasa a formar parte del contexto del Coach.

---

# 7. Ejercicios

La biblioteca de ejercicios sigue existiendo, pero se accede principalmente mediante contexto.

Ejemplo:

> Usuario:
> Quiero trabajar recepción.

Coach:

```text
Te propongo:

1. Short Serve → Backhand Flick
   8 min

2. Random Short / Long
   10 min

3. Receive + 3rd Ball
   12 min

Total:
30 min

[ AÑADIR A MI ENTRENAMIENTO ]
```

---

# 8. Entrenamiento generado por IA

El Coach debe poder generar una sesión automáticamente.

Input:

```text
Jugador
Objetivos
Resultados
Últimos entrenamientos
Nivel
Disponibilidad
Próximo torneo
Ejercicios disponibles
Carga reciente
```

Output:

```text
Sesión recomendada

Duración: 45 min

Objetivo:
Recepción + transición

1. Calentamiento
5 min

2. Recepción
10 min

3. Recepción + primer ataque
15 min

4. Situación aleatoria
10 min

5. Evaluación
5 min
```

Acciones:

- [Empezar]
- [Modificar]
- [Guardar]
- [Explicar por qué]

---

# 9. Post-entrenamiento

La sesión debe terminar con una interacción muy corta.

```text
ENTRENAMIENTO COMPLETADO

42 min

✓ Recepción +7%
✓ Primer ataque +4%
⚠ Consistencia bajo presión

Coach:

"Hoy ejecutaste correctamente
el patrón 18 de 23 veces.

En la próxima sesión aumentaremos
la variabilidad."

[ CONTINUAR PLAN ]
```

El sistema actualiza automáticamente:

- Resultados.
- Métricas.
- Progreso.
- Objetivos.
- Carga.
- Recomendaciones futuras.

---

# 10. Gestión de partidos

## Antes

El Coach analiza al rival:

```text
He analizado a tu oponente.

3 patrones importantes:

1. Saque corto al revés
2. Ataque al codo
3. Menor efectividad ante juego defensivo

PLAN DE PARTIDO

[ Ver estrategia ]
```

## Durante / después

El jugador puede registrar mediante voz:

> "Perdí 3-2. El problema fue su saque corto."

La IA estructura automáticamente la información.

---

# 11. Gestión de torneos

El torneo debe activar automáticamente un **Tournament Mode**.

## 14 días antes

> He creado tu plan de preparación.

## 2 días antes

> Estas son las tres situaciones que quiero que recuerdes.

## Día del torneo

Interfaz simplificada:

```text
TORNEO

Próximo partido

Javier M.

PLAN RÁPIDO

1. Saque corto al revés
2. Atacar el codo
3. No forzar primer ataque

[ VER ESTRATEGIA ]
```

## Después

> "Cuéntame cómo fue."

El Coach registra el aprendizaje.

---

# 12. Perfil del jugador

No utilizar una ficha administrativa tradicional como pantalla principal.

En su lugar:

```text
ALEX

82
PERFORMANCE

Técnica       79
Táctica       84
Física        88
Mental        76
```

### Coach Insight

> "Tu juego está evolucionando más rápido en táctica que en técnica."

### Focus

```text
1. Recepción
2. Primer ataque
3. Juego contra defensivos
```

---

# 13. Entrenador — Coach Center

La experiencia del entrenador debe ser diferente a la del jugador.

## Home

```text
COACH CENTER

28 jugadores
4 grupos
6 sesiones esta semana

--------------------------------

⚠ REQUIEREN ATENCIÓN

Carlos
Recepción ↓

Laura
Carga de entrenamiento ↑

Marc
Progreso estancado

[ REVISAR ]
```

---

# 14. IA para entrenador

El entrenador debe poder preguntar:

> "¿Cómo está evolucionando el grupo U16?"

Respuesta:

```text
Últimas 5 sesiones:

Técnica       +8%
Recepción    +11%
Transición    +4%

He detectado un patrón:

4 de 8 jugadores presentan
problemas después de una
devolución corta.

Recomiendo trabajar:

Recepción → transición 1→2

[ CREAR SESIÓN ]
```

---

# 15. Generación automática de sesiones para grupos

Input:

```text
Grupo
Nivel
Jugadores
Objetivos
Resultados recientes
Asistencia
Problemas detectados
Ejercicios disponibles
Duración
```

Output:

```text
SESIÓN U16

Objetivo:
Transición después de recepción

Duración:
90 min

5 ejercicios

Jugadores con atención especial:
Carlos
Marc
Laura
```

---

# 16. Análisis de jugadores para entrenador

La IA debe generar automáticamente una ficha de rendimiento:

```text
CARLOS

Performance: 74

Técnica       71
Táctica       76
Física        79
Mental        68

Tendencia:
↑ Técnica
→ Táctica
↓ Mental

Principal oportunidad:
Recepción

Recomendación:
2 sesiones específicas
esta semana.
```

---

# 17. Web vs Mobile

## Mobile

Prioridad:

**Coach → Acción → Resultado**

Ideal para:

- Entrenar.
- Competir.
- Registrar partidos.
- Consultar recomendaciones.
- Hablar con el Coach.
- Consultar sesión.

---

## Web

Prioridad:

**Coach → Análisis → Gestión**

Ideal para:

- Entrenadores.
- Análisis profundo.
- Comparación de jugadores.
- Grupos.
- Programas.
- Planificación.
- Calendario.
- Informes.
- Gestión de ejercicios.

---

# 18. Sistema de diseño

## Tema

Dark-first.

### Concepto AI Coach 360

- Fondo: negro / azul muy oscuro.
- Acento: cyan / teal.
- Gradientes suaves.
- Glow.

### Concepto Focus Flow

- Fondo: negro / grafito.
- Acento: violeta.
- Glow alrededor del Coach.

### Concepto Game Plan

- Fondo: negro / grafito.
- Acento: naranja.
- Elementos dorados.
- Progreso visual.

---

# 19. Componentes principales

Crear un Design System reutilizable:

```text
Button
Card
Metric
ProgressBar
ProgressRing
Insight
CoachMessage
CoachAvatar
CoachOrb
Chart
PlayerAvatar
ExerciseCard
TrainingSession
MatchCard
OpponentCard
TournamentCard
Mission
Achievement
JourneyNode
Timeline
Calendar
BottomNavigation
Sidebar
Modal
Sheet
VoiceInput
ChatInput
Notification
```

---

# 20. Componente diferencial: Coach Orb

El agente debería tener un elemento visual reconocible.

Puede ser:

- Orb.
- Avatar.
- Icono dinámico.
- Pulso luminoso.
- Estado animado.

Estados:

```text
IDLE
  Coach disponible

LISTENING
  Escuchando

THINKING
  Analizando

INSIGHT
  Tiene una recomendación

ACTION
  Está ejecutando una tarea

SUCCESS
  Tarea completada
```

Esto crea continuidad visual entre las diferentes partes de la aplicación.

---

# 21. Motor agéntico

La interfaz debe diseñarse pensando desde el principio en un agente capaz de ejecutar acciones.

## Capacidades

```text
analyze_player()
analyze_match()
analyze_opponent()
analyze_progress()
create_goal()
update_goal()
recommend_exercises()
create_training_session()
modify_training_session()
register_match()
update_player_metrics()
create_tournament_plan()
create_group_session()
analyze_training_group()
generate_report()
```

El agente no solamente devuelve texto.

Debe poder **ejecutar operaciones sobre el sistema**.

---

# 22. Modelo mental del agente

```text
                  USER
                    ↓
              ┌──────────┐
              │ AI COACH │
              └────┬─────┘
                   ↓
          ┌──────────────────┐
          │ PLAYER CONTEXT   │
          ├──────────────────┤
          │ Objectives       │
          │ Matches          │
          │ Opponents        │
          │ Training         │
          │ Exercises        │
          │ Metrics          │
          │ Tournaments      │
          │ Notes            │
          └────────┬─────────┘
                   ↓
               ANALYSIS
                   ↓
              RECOMMENDATION
                   ↓
                 ACTION
                   ↓
                RESULT
                   ↓
                LEARNING
```

---

# 23. Principio clave para el desarrollo

No construir primero todas las pantallas actuales y después añadir IA.

Construir primero el **motor de contexto + agente** y diseñar la UI alrededor de sus capacidades.

La arquitectura debería permitir:

```text
Data Layer
    ↓
Player Context
    ↓
AI / Rules / Analytics
    ↓
Agent
    ↓
Actions / Recommendations
    ↓
Mobile UI
Web UI
```

---

# 24. MVP recomendado

Para la primera versión agéntica, priorizar:

### 1. Today / Coach Home

El agente explica qué debería hacer el jugador.

### 2. Análisis de jugador

Objetivos + partidos + entrenamientos + evolución.

### 3. Análisis de partido

Entrada mínima + interpretación automática.

### 4. Generación de entrenamiento

El agente crea sesiones a partir de los objetivos y problemas detectados.

### 5. Post-entrenamiento

Feedback y actualización automática.

### 6. Preparación de torneo

Análisis del rival + plan de partido.

### 7. Coach conversacional

Texto + voz.

### 8. Coach Center para entrenadores

Análisis de grupos y jugadores + generación de sesiones.

---

# 25. Criterio de éxito UX

La aplicación debería intentar que las tareas habituales pasen de:

```text
5–10 pantallas
+
múltiples formularios
+
introducción manual
```

a:

```text
1 intención
+
1–3 interacciones
+
acción automática del agente
```

Ejemplo:

**Antes**

```text
Calendario
→ Nueva sesión
→ Seleccionar grupo
→ Seleccionar programa
→ Seleccionar ejercicios
→ Añadir objetivos
→ Guardar
```

**Después**

> "Prepara la sesión del U16 para mañana. Quiero trabajar recepción."

**Coach:**

> "He preparado una sesión de 90 minutos basada en los últimos resultados del grupo. Hay 3 jugadores que necesitan ejercicios específicos."

**[Revisar] [Crear sesión]**

---

# 26. Concepto final

La identidad del producto debería resumirse en:

> **No es una aplicación donde gestionas tu tenis de mesa.**
>
> **Es un Coach que gestiona contigo tu evolución en tenis de mesa.**

La combinación recomendada es:

```text
AI COACH 360
      +
FOCUS FLOW
      +
GAME PLAN
```

Con un único principio:

# **OBSERVE → UNDERSTAND → ACT → IMPROVE**

El usuario aporta cada vez menos datos manualmente y recibe cada vez más **contexto, decisiones y acciones útiles** del sistema.

---

# 27. Datos competitivos: qué puede saber el agente

> Añadido el 14/08/2026. Los datos vienen del proyecto `tt-scraper`, que carga
> tres competiciones y dos rànquings en el proyecto Firestore
> `gravity-scrapper-pro`. El detalle de implementación está en
> `tt-scraper/design/functionalities_overview.md`; aquí solo se recoge **qué
> permite hacer** de cara al producto.

Esto es lo que distingue a este compañero de un chatbot con conocimientos de
tenis de mesa: el agente **conoce a los rivales reales del usuario**, con su
ranking, su historial y su forma reciente. No opina sobre el tenis de mesa en
abstracto; habla de la persona que estará al otro lado de la mesa el domingo.

## 27.1 Lo que hay cargado

| | |
|---|---|
| Competiciones | RTBTT (territorial de Barcelona), FCTT (TDM + Copa Catalana), RFETM (liga nacional) |
| Rànquings | FCTT — 3.007 jugadores, 10 revisiones mensuales · RFETM — 4.351 jugadores, 10 meses |
| Partido a partido | FCTT — 50.000 partidos con rival, marcador, rating de ambos y puntos ganados |

**La pieza que lo une todo:** las actas de las tres ligas guardan la licencia de
cada jugador en cada partido individual, y **todas resuelven contra el ranking
correspondiente** (verificado sobre 120 licencias, 100 %). Desde cualquier
partido se salta a la ficha completa del jugador sin casar nombres.

Con una salvedad importante: **la FCTT y la RFETM usan numeraciones distintas**.
Un catalán que juega la liga nacional tiene dos identidades sin ningún campo que
las una. Se puede deducir cruzando los partidos que aparecen en ambas fuentes,
pero **no está hecho** y no debe resolverse casando nombres.

## 27.2 Casos de uso y su viabilidad

### Evolución de victorias y derrotas en el tiempo — ✅

`players/{lic}/match_log/{edición}` da victorias y derrotas mes a mes, y dentro
el detalle de cada partido. Sirve igual para el jugador y para cualquier rival.

En la RFETM, de momento, **solo el agregado mensual** (`pj/pg/pp`): el detalle
partido a partido es la fase 2, todavía sin cargar.

### Ficha del rival: nivel, forma y trayectoria — ✅

Una sola lectura del documento del jugador da nombre, club, categoría, posición
vigente en cada ranking y el resumen de temporada (`record`: partidos, ganados,
perdidos, torneos, mejor victoria). Dos o tres lecturas más dan sus últimos
meses partido a partido.

> Para el agente, esto es lo que convierte "prepara el partido del domingo" en
> algo concreto. Ejemplo real de la base de datos:
> *SAÑA Orlando, 119 partidos esta temporada, 88-31, 9 torneos, mejor victoria
> ante un 2.317. En noviembre perdió 89 puntos con un 12-7.*

### ¿A quién gana de verdad? — ✅

Cada partido guarda el rating **de los dos en ese momento**, así que se puede
responder de verdad a "sus victorias son contra rivales muy inferiores o no":
basta con la distribución del diferencial de rating de sus victorias. Es la
diferencia entre decir "gana mucho" y "gana mucho, pero nunca por encima de su
nivel".

Disponible para jugadores con licencia FCTT. Para los de la RFETM, tras la fase 2.

### Predecir la alineación del rival — ✅ con una condición

Cada encuentro identifica los dos equipos y cada individual lleva la licencia y
el **puesto** (1 a 5). Recorriendo las jornadas del grupo se obtiene quién ha
jugado por ese equipo, cuántas veces y **en qué puesto suele alinearse**; de ahí
se salta a la ficha de cada uno. Son unas 22 lecturas por equipo y temporada.

La condición: hace falta el **calendario futuro**. Hoy todas las jornadas
cargadas están jugadas. La FCTT publica los encuentros pendientes y la RFETM
también, así que entrarán solos al empezar la temporada; **de la RTBTT no**,
porque su fuente son las actas en PDF de lo ya jugado.

### Buscar por nombre y club — ⚠️ pendiente

Firestore no tiene búsqueda de texto. La solución elegida: publicar un
**documento de índice** con los 7.358 jugadores (licencia, nombre, club), que
ocupa 224 KB, cabe en un solo documento y la app descarga una vez. La búsqueda
pasa a ser local: subcadena, sin acentos, tolerante a erratas, instantánea y sin
conexión, a cambio de **2 lecturas**.

Y un **diccionario de alias de club**, porque el mismo club se llama `VILCA` en
el ranking y `CTT VILANOVA DEL CAMI` en las actas. Se deduce por voto
mayoritario de los jugadores de cada equipo (verificado en 7 equipos, 100 % de
acuerdo).

## 27.3 Consecuencias para el diseño

1. **La ficha del rival es una pantalla de primera clase**, no un detalle dentro
   de "Oponentes". Es la que justifica el producto y la que más datos tiene
   detrás.
2. **El agente debe distinguir "no lo sé" de "no compite".** Un jugador sin
   `current` no está sin clasificar por error: no aparece en el ranking del mes.
   Hay 237 jugadores que solo existen por sus partidos, sin ranking.
3. **Todo dato tiene fecha.** Un rating es "del mes de juny", no "el rating". El
   agente debería decirlo, porque un rival puede haber subido 80 puntos desde la
   última vez que se cruzaron.
4. **Dos federaciones, dos rànquings.** Un mismo rival puede tener el 342º
   catalán y no aparecer en el nacional. La UI no debería mezclarlos en un único
   número.