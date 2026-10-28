# 🛠️ Guía de instalación y despliegue — Nexus Trader

Esta guía te lleva de un clon recién hecho a un sistema corriendo. Si solo lees una cosa, lee **[Plataforma y MT5 — léelo primero](#-plataforma-y-mt5--léelo-primero)**.

> 🇬🇧 Prefer English? See the [English setup guide](SETUP.md).

---

## ⚡ Resumen (todo en 6 comandos)

```bash
git clone https://github.com/MirandaCR/Nexus-Trading-System-MT5.git
cd Nexus-Trading-System-MT5
python -m venv .venv && .venv\Scripts\activate          # Windows
pip install -r requirements.txt
cd dashboard && npm install && cd ..
copy .env.example .env                                    # luego edita .env (ver abajo)
docker run -d --name nexus-redis -p 6379:6379 redis:7-alpine
python run.py
```

Después abre **http://localhost:3100**. `run.py` corre un pre-flight y no arranca si falta algo crítico — el error te dice exactamente qué.

---

## 🖥️ Plataforma y MT5 — léelo primero

Nexus se conecta al mercado a través del **paquete de Python `MetaTrader5`, que solo corre en Windows.** Eso condiciona todo:

| Estás en… | Qué funciona |
|-----------|--------------|
| **Windows** (recomendado) | Todo — datos MT5 en vivo, ejecución de órdenes, el pipeline completo |
| **macOS / Linux** | Todo **excepto** los datos y la ejecución MT5 en vivo. Los 9 servicios, el dashboard, el ML y los agentes arrancan igual, pero `nexus-data`/`nexus-executor` no pueden llegar a MT5. Sirve para leer el código y la UI, no para operar. |

Para una corrida real necesitas el **terminal de escritorio de MetaTrader 5 instalado, abierto, logueado en una cuenta demo y con *Algo Trading* activado.** Más sobre esto en el [Paso 4](#paso-4--metatrader-5-cuenta-demo).

> Este repo **no incluye credenciales** y apunta a una cuenta **demo**. Es un proyecto de investigación/portafolio — no es asesoría financiera ni una máquina de imprimir dinero. Los instrumentos apalancados pueden vaciar una cuenta.

---

## ✅ Requisitos

| Herramienta | Versión | Notas |
|-------------|---------|-------|
| **Python** | **3.11** | 3.11 es el objetivo probado. (`pandas-ta` necesita ≥3.12 en PyPI, así que el repo usa `pandas-ta-classic` como fallback compatible con 3.11.) |
| **Node.js** | **18+** | Para el dashboard en Next.js. |
| **Docker Desktop** | reciente | Solo se usa para correr **Redis**. O apunta a cualquier Redis en `:6379`. |
| **MetaTrader 5** | última | El terminal de escritorio, logueado en una cuenta **demo** (Windows). |
| **API key de DeepSeek / OpenAI / Gemini** | opcional | Activa los agentes GenAI. El ensemble cuantitativo de ML corre **sin** ninguna key. |

---

## Paso 1 — Clonar

```bash
git clone https://github.com/MirandaCR/Nexus-Trading-System-MT5.git
cd Nexus-Trading-System-MT5
```

## Paso 2 — Entorno de Python + dependencias

```bash
python -m venv .venv
# Windows:
.venv\Scripts\activate
# macOS/Linux:
# source .venv/bin/activate

pip install -r requirements.txt
```

## Paso 3 — Dependencias del dashboard

```bash
cd dashboard
npm install
cd ..
```

## Paso 4 — MetaTrader 5 (cuenta demo)

1. Instala el terminal de escritorio de **MetaTrader 5** desde tu bróker (XM, etc.).
2. Abre una cuenta **demo** e inicia sesión.
3. Activa el trading automático: **Herramientas → Opciones → Asesores Expertos → "Permitir trading algorítmico"**, y asegúrate de que el botón **Algo Trading** de la barra esté en verde.
4. Anota tres cosas para tu `.env`:
   - **Login** (número de cuenta)
   - **Password** (la contraseña demo)
   - **Server** (p. ej. `XMGlobal-MT5 5` — exactamente como aparece en el terminal)
   - Opcionalmente la **ruta** a `terminal64.exe`.

> ⚠️ **La trampa más común:** el `MT5_LOGIN` / `MT5_SERVER` de tu `.env` deben coincidir con **la cuenta en la que el terminal está logueado.** Un desajuste (p. ej. `.env` apunta a otra cuenta) produce `MT5 initialize failed: [-6] Authorization failed`. Mantenlos sincronizados.

## Paso 5 — Redis

Elige una:

```bash
# Opción A — un contenedor
docker run -d --name nexus-redis -p 6379:6379 redis:7-alpine
```

```bash
# Opción B — el compose del proyecto
docker compose up -d redis
```

Verifica que responda:

```bash
docker exec nexus-redis redis-cli ping    # -> PONG
```

## Paso 6 — Configurar `.env`

Copia la plantilla y pon **tus** valores. Todo está documentado dentro de `.env.example`.

```bash
copy .env.example .env      # Windows   (cp .env.example .env en macOS/Linux)
```

Lo esencial:

| Variable | Qué poner |
|----------|-----------|
| `MT5_LOGIN` | Tu número de cuenta demo |
| `MT5_PASSWORD` | Tu contraseña demo |
| `MT5_SERVER` | El servidor de tu bróker, exactamente como aparece (p. ej. `XMGlobal-MT5 5`) |
| `MT5_PATH` | *(opcional)* ruta completa a `terminal64.exe` |
| `STREAM_SYMBOLS` | Símbolos a operar, p. ej. `XAUUSD,BTCUSD` |
| `NEXUS_LLM_PROVIDER` | `deepseek` (default) · `openai` · `gemini` · `local` |
| `DEEPSEEK_API_KEY` / `OPENAI_API_KEY` / `GEMINI_API_KEY` | key del proveedor elegido (solo una; déjala vacía para correr solo-cuantitativo) |
| `NEXUS_FIXED_LOTS` | lote fijo por símbolo, p. ej. `BTCUSD=0.02` |
| `NEXUS_LOG_JSON` | `false` para logs legibles en consola, `true` para JSON |

> 🔒 **Nunca subas tu `.env`.** Ya está en `.gitignore`. Solo se versiona `.env.example` (con placeholders vacíos).

## Paso 7 — Correrlo

```bash
python run.py
```

`run.py` es el supervisor: corre un **pre-flight**, luego arranca los **9 servicios + el dashboard**, inyecta las URLs inter-servicio y reinicia automáticamente lo que se caiga.

El pre-flight verifica, y **bloquea el arranque** si falla algo crítico:

- ✅ `.env` presente y credenciales MT5 puestas
- ✅ **Redis** accesible en `:6379`
- ✅ Conectividad **MT5** (balance + símbolos)
- ✅ Carpeta de modelos ML presente
- ⚠️ advierte (sin bloquear) si *AutoTrading* está apagado o estás fuera de sesión

Salta el pre-flight solo si sabes por qué: `python run.py --skip-preflight`.

## Paso 8 — Abrir el dashboard

Entra a **http://localhost:3100**.

- La primera carga compila on-demand (Next.js dev) — dale unos segundos.
- Revisa que **System Health** (pestaña Risk) muestre los servicios ONLINE.
- La pestaña **Overview** debe mostrar el gráfico en vivo y el reloj de sesión; **Agents** muestra el grafo del pipeline.

---

## 🧠 Elegir el proveedor de IA

Los agentes GenAI (debate + árbitro + brief diario) usan **DeepSeek** por defecto. Dos formas de cambiarlo:

- **En `.env`** (aplica al próximo arranque): pon `NEXUS_LLM_PROVIDER=openai` (o `gemini` / `local`) y la key correspondiente.
- **En vivo desde el dashboard**: *pestaña Risk → Trade Settings → dropdown "Proveedor de IA"* — cambia el proveedor sin reiniciar.

Los tres proveedores cloud usan endpoints compatibles con OpenAI, así que cambiar no requiere dependencias extra. `local` apunta a un servidor Ollama (`NEXUS_LOCAL_LLM_URL`).

---

## 🩺 Solución de problemas

| Síntoma | Causa → Solución |
|---------|------------------|
| Pre-flight `BLOQUEADO` · `Error 10061 connecting to 127.0.0.1:6379` | Redis no está corriendo → arranca Docker Desktop, luego el contenedor `nexus-redis` (Paso 5). |
| `MT5 initialize failed: [-6] Authorization failed` | El login/server del `.env` no coinciden con la cuenta en la que está logueado el terminal → alinéalos (Paso 4). |
| `MT5 initialize failed: [-10001]` | El terminal MT5 no está abierto / no logueado → ábrelo e inicia sesión en la demo. |
| El pre-flight advierte *AutoTrading disabled* | Pulsa el botón verde **Algo Trading** en MT5. |
| `nexus-ml` parpadea a `000` un rato tras arrancar | Normal — está entrenando el ensemble de bootstrap (pesado en CPU). Se recupera al terminar. |
| El dashboard `http://localhost:3100` carga lento/en blanco la primera vez | Next.js dev compila rutas on-demand; recarga tras unos segundos. |
| El puerto de un servicio está ocupado | Hay algo en `9000–9008` o `3100` → deténlo, o cambia los puertos en `.env`. |
| No hay decisiones GenAI, agentes quietos | Falta/está mal la API key para `NEXUS_LLM_PROVIDER` → el sistema igual opera con el ensemble cuantitativo; agrega una key para activar los agentes. |

---

## 🗂️ Qué corre dónde

| Servicio | Puerto | Rol |
|----------|--------|-----|
| nexus-api | 9000 | gateway / BFF + WebSockets |
| nexus-data | 9001 | ingesta MT5 (ticks / OHLCV) |
| nexus-features | 9002 | features multi-timeframe + cross-asset |
| nexus-ml | 9003 | ensemble por símbolo + entrenamiento |
| nexus-agents | 9004 | agentes LangGraph + research |
| nexus-news | 9005 | calendario económico + sentimiento |
| nexus-risk | 9006 | circuit breakers + anti-tilt |
| nexus-executor | 9007 | ejecución MT5 + kill-switch |
| nexus-monitor | 9008 | salud + telemetría |
| dashboard | 3100 | sala de control Next.js |
| Redis | 6379 | event streams + caché |

---

## 🛡️ Seguridad y licencia

- **Demo primero.** Nunca apuntes esto a una cuenta real que no estés dispuesto a perder.
- Es un proyecto de **investigación/portafolio**, licenciado **AGPL-3.0** — si corres una versión modificada como servicio, debes publicar tu código fuente.
- ¿Dudas o problemas? Abre un [issue en GitHub](https://github.com/MirandaCR/Nexus-Trading-System-MT5/issues).
