# Paseo Aranjuez — backend

API Flask para **PaseoYa** (marketplace de comercios del paseo) y **PaseoPoints**
(le fidelización por compras). Un solo backend sirve a los tres perfiles: cliente,
comercio y administrador.

- **PaseoYa**: catálogo público, carrito en el navegador y pedido de retiro
  presencial. No hay pagos online, no hay domicilio, no hay courier.
- **PaseoPoints**: cada compra genera puntos; los puntos se canjean por
  descuentos en el mismo paseo.

---

## 1. Arranque rápido

```bash
python -m venv .venv
.venv\Scripts\activate            # Windows
# source .venv/bin/activate       # Linux/macOS

pip install -r requirements.txt
copy .env.example .env            # Windows: copy / Linux: cp
# editar .env con tus credenciales

flask --app app init-db           # crear tablas
flask --app app seed-demo         # datos de demostración
flask --app app run               # http://127.0.0.1:5000
```

Verificación de que todo funciona:

```bash
python -m pytest                    # 99 tests sobre SQLite en memoria
python tools/smoke_postgres.py      # 27 checks contra PostgreSQL real
```

---

## 2. Variables de entorno

Todo se lee de `.env`. La app **falla al arrancar** si falta `DATABASE_URL`, en
lugar de connectarse a una base de datos desconocida.

| Variable | Obligatoria | Descripción |
|---|---|---|
| `DATABASE_URL` | sí | `postgresql+psycopg2://usuario:clave@host:5432/paseo_aranjuez`. SQLite también funciona para probar. |
| `SECRET_KEY` | sí | Firma el JWT y las Flask sessions. **Cambiarla invalida todas las sesiones.** |
| `JWT_SECRET_KEY` | no | Solo si se quiere una clave distinta para JWT. |
| `JWT_EXPIRES_HOURS` | no | Default `24`. |
| `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` | para login real | Credenciales de Google OAuth. |
| `GOOGLE_REDIRECT_URI` | no | Default `http://127.0.0.1:5000/auth/google/callback`. |
| `FRONTEND_URL` | no | Para CORS. Default `http://localhost:5173`. |
| `CORS_ORIGINS` | no | CSV de orígenes permitidos. |
| `SESSION_COOKIE_SECURE` | no | Default `1` en producción. **Poner `0` en local**, si no el cookie de sesión no vuelve por HTTP. |
| `RATELIMIT_STORAGE_URI` | no | Default `memory://`. Ver §4. |
| `FLASK_DEBUG` | no | Default `0`. |
| `FLASK_ENV` | no | `production` desactiva `flask dev-token`. |
| `TESTING` | no | Lo activa la suite de tests. |

`flask dev-token` está pensado **solo para desarrollo**: firma un token para un
usuario que ya existe, saltándose la verificación de Google. Sirve para probar
la API sin configurar OAuth, y se niega a correr si `FLASK_ENV=production`.

---

## 3. Modelo de datos

8 tablas, definidas en [`schema.sql`](schema.sql) y en `models/`. El SQL es la
referencia legible; los modelos son la fuente de verdad de la app.

```
users ──1:1── business ──1:n── products
  │                 │
  │                 └──< orders >── order_items >── products
  │                          │
  │                          └──< coupons >── rewards ──┐
  │                                                    │
  └──< transactions >───────────────────────────────────┘
        (append-only)
```

| Tabla | Notas |
|---|---|
| `users` | `google_sub` es la identidad. `password_hash` existe pero **nunca se usa**: el login es solo Google. |
| `business` | 1:1 con `users`. `points_per_bs` es `NUMERIC`, no `float`. |
| `products` | Precio en Bs. `CHECK (stock >= 0)` y `CHECK (price_bs >= 0)`. |
| `orders` | Un solo comercio por pedido. Guarda snapshot del total y de los puntos ganados. |
| `order_items` | Snapshot de nombre y precio unitario del momento de la compra. |
| `rewards` | `business_id` nulo = patrocinada por Paseo, válida en cualquier comercio. |
| `coupons` | Snapshot de los términos de la recompensa: editar la recompensa no altera un cupón ya emitido. |
| `transactions` | **Append-only.** Es la fuente de verdad del saldo. |

### El saldo se deriva, no se guarda

`users.points_balance` **no se incrementa**. Es un caché calculado con
`SUM(transactions.points)`, y `users.level` se actualiza junto a él.

La razón es práctica: si el saldo fuera una columna que cada endpoint suma por su
cuenta, un endpoint olvidado se traduce en puntos perdidos o duplicados. Con un
ledger append-only el saldo siempre cuadra y el historial es auditable.

### Niveles

`BRONZE` → `SILVER` → `GOLD` → `PLATINUM`, por saldo acumulado.

---

## 4. Reglas de negocio

Estas son las decisiones que no se deducen del código, y lo que hay detrás de cada
una.

**Los puntos se acreditan al entregar el pedido, no al pagarlo.** Until then the
order could still be cancelled and the ledger would need reversing.

**El redondeo es por compra, nunca sobre la suma.** `floor(33.33 × 0.33) = 10`
puntos; tres compras separadas dan 30, no 32. Redondear hacia arriba al total
permitiría comprar de a Bs 1 para inflar el saldo, y haría que el saldo no
cuadrara con el recibo que el comercio imprimió.

**Un pedido es de un solo comercio.** El carrito se agrupa en el frontend; si
llegaran productos de dos comercios, el backend rechaza.

**El precio lo calcula el servidor.** El cliente manda `product_id` y `quantity`,
nunca precios. Aceptar un precio del cliente sería abrir un campo que cualquier
usuario puede editar.

**Los puntos del canje se devuelven si se cancela el pedido.** Como fila `refund`
nueva, nunca editando la fila original: el estado de cuenta tiene que seguir
sumando. El cupón vuelve a `active` y se puede usar en otro pedido — nunca llegó
a aplicarse. (Si venció mientras tanto, `is_expired` lo bloquea igual.)

**Un usuario no se autoactiva su comercio.** El registro crea el negocio con
`active=False`; solo un ADMIN lo aprueba. El cambio de rol a `SELLER` y el alta
del negocio ocurren en la misma transacción: o el usuario es vendedor con
comercio, o no es nada.

**Un comercio no puede acreditar puntos a su propio dueño.** Usaría el sistema
para fabricarse saldo.

**El rol viene de la base de datos, no del JWT.** El token lleva el rol para
conveniencia, pero cada request lo relee de la BD. Así un cambio de rol surte
efecto inmediatamente y un token con `role: ADMIN` falsificado no sirve: la
firma no valida.

**Sin dirección ni courier.** Los pedidos son de retiro y el cliente llega con un
código de 6 caracteres.

### Rate limiting

Dos endpoints están limitados, porque son los que puede golpear alguien sin
credenciales:

| Endpoint | Límite | Por qué |
|---|---|---|
| `POST /auth/google` | 10/min | Cada llamada consulta a Google y crea o busca un usuario. Es la superficie de replay de credenciales y de quema de cuota. |
| `POST /api/business/scan` | 60/min | Un comercio podría escanear al mismo cliente sin parar para fabricarse saldo. |

Al superarse devuelven **429** con `Retry-After`.

> `RATELIMIT_STORAGE_URI` usa `memory://`, que es memoria del proceso. Con varios
> workers cada uno lleva su propia cuenta y el límite efectivo se multiplica.
> Antes de correr más de un proceso, apunta a Redis.

### Ventana anti-duplicado en el escaneo

El rate limiting **no alcanza** para el abuso de.points: 60 escaneos por minuto
alcanzan para acuñar saldo. Por eso `POST /api/business/scan` además rechaza un
segundo escaneo del **mismo par (cliente, comercio)** dentro de
`SCAN_DUPLICATE_WINDOW_SECONDS` (60 por defecto), con **409** y `Retry-After`.

Tres detalles deliberados:

- **Es por par, no por cliente.** Tres comercios distintos en un minuto es
  compra normal; bloquearlo arreglaría un abuso que exige collusion y
  rompería el caso común.
- **Solo mira filas `EARN`.** Un refund o un ajuste de admin no bloquean la
  siguiente compra real.
- **No aplica a la entrega de pedidos.** Si aplicara, un cliente escaneado en un
  comercio y que recoge su pedido ahí mismo antes de un minuto nunca recebería
  los puntos — que son los que el comercio tiene ganados.

El costo es un borde real: dos compras separadas del mismo cliente en el mismo
comercio dentro de la ventana se rechazan. El impacto es "el comercio espera un
minuto", no dinero perdido, que es lo que hace aceptable una bluntly window. Con
`SCAN_DUPLICATE_WINDOW_SECONDS=0` se desactiva.

### Estados de un pedido

```
received → confirmed → preparing → ready_for_pickup → customer_arrived → delivered
                                                          ↓
                                                     cancelled
```

`cancelled` es terminal. `delivered` también, y no se puede cancelar: un pedido
ya entregado se corrige con un ajuste de puntos de ADMIN, no reescribiendo el
historial.

---

## 5. Autenticación y autorización

Login únicamente con Google (`POST /auth/google`). Se valida el ID token contra
Google, se exige `email_verified`, y **el rol nunca se toma de Google** ni del
cuerpo de la petición.

- **401** falta o no se puede verificar el token.
- **403** el rol no alcanza.
- **404** el recurso existe pero no le pertenece al que pregunta — para no
  confirmar que existe.

Un correo que ya existe bajo otro `google_sub` es **409**, no un login nuevo: es
la señal de un intento de vincular dos cuentas.

---

## 6. Endpoints

47 rutas registradas. Los grupos de mayor interés:

**Público** (sin token)

```
GET  /api/products              catálogo, con filtros de comercio y categoría
GET  /api/products/<id>
GET  /api/businesses
GET  /api/categories
GET  /api/rewards               `points` es null sin sesión
```

**Cliente**

```
POST /auth/google                    login
POST /auth/logout
GET  /api/me                         perfil + saldo + nivel + QR
GET  /api/me/points
GET  /api/me/transactions            estado de cuenta
GET  /api/me/qr
GET  /api/me/coupons
POST /api/rewards/<id>/redeem        canjea puntos por cupón
POST /api/orders                     crea el pedido; devuelve {"order": ...}
GET  /api/me/orders
POST /api/me/orders/<id>/arrive      el cliente avisó que llegó
```

**Comercio**

```
POST /api/business/register          alta; requiere JWT de usuario
GET  /api/business/me                el negocio del usuario
POST /api/business/products
PATCH /api/business/products/<id>
POST /api/business/scan              acredita puntos leyendo el QR del cliente
GET  /api/business/orders
PATCH /api/business/orders/<id>/status
POST /api/business/orders/<id>/pickup   entrega, con el código del cliente
POST /api/business/orders/<id>/cancel
POST /api/business/coupons/validate
GET  /api/business/transactions
```

**Administración**

```
GET   /api/admin/stats
GET   /api/admin/users
PATCH /api/admin/users/<id>/role
POST  /api/admin/users/<id>/points    ajuste manual, queda en el ledger
GET   /api/admin/businesses
PATCH /api/admin/businesses/<id>      aprobar (active) o suspender
GET   /api/admin/rewards
POST  /api/admin/rewards              incluye las patrocinadas por Paseo
PATCH /api/admin/rewards/<id>
```

### Formato de error

Uniforme en toda la API:

```json
{ "error": { "code": "validation_error", "message": "...", "details": { } } }
```

| Código | Cuándo |
|---|---|
| 400 | Faltan campos, un enum no existe, un id no es UUID. |
| 401 | Sin token, token vencido o firma inválida. |
| 403 | Rol insuficiente, o negocio inactivo. |
| 404 | No existe, o no es visible para quien pregunta. |
| 409 | Conflicto de estado, stock insuficiente, saldo insuficiente, Google en conflicto. |
| 422 | La regla de negocio lo prohíbe (p. ej. entregar un pedido que no está listo). |

---

## 7. Comandos

```bash
flask --app app init-db            # crear tablas faltantes
flask --app app drop-db            # borrar todo (pide confirmación)
flask --app app seed-demo          # datos de demo, idempotente
flask --app app reset-demo --yes   # drop + create + seed
flask --app app dev-token <email>  # token listo para probar (solo dev)
```

`seed-demo` crea dos comercios con catálogo, tres recompensas y un cliente con
1200 puntos. Es idempotente: se puede repetir sin duplicar nada.

| Correo | Rol |
|---|---|
| `admin@paseo.test` | ADMIN |
| `cliente@paseo.test` | USER, 1200 puntos |
| `cafe@paseo.test` | SELLER → Cafe Aranjuez |
| `tech@paseo.test` | SELLER → Tech Aranjuez |

Estas cuentas **no tienen contraseña** y no entran por el flujo real de Google.
Para recorrer la API sin OAuth: `flask dev-token cliente@paseo.test`.

---

## 8. Pruebas

```bash
python -m pytest              # 99 tests
python -m pytest -k scan     # un subconjunto
```

| Archivo | Cubre |
|---|---|
| `tests/test_auth.py` | Login Google, rol no determinable por el cliente, forged tokens, Google en conflicto, rate limiting. |
| `tests/test_points.py` | Ledger, niveles, redondeo, bloqueo de autoacreditación, ventana anti-duplicado. |
| `tests/test_orders.py` | Ciclo de vida del pedido, stock, cupones, cancelación con refund. |
| `tests/test_business.py` | Registro, aprobación, catálogo, administración. |

La suite corre sobre **SQLite en memoria**, que es rápido y no necesita servidor.
Eso deja un hueco real: `NUMERIC`, los `CHECK`, `FOR UPDATE` y los UUID nativos
solo existen en PostgreSQL. Para eso está `tools/smoke_postgres.py`, que pide 27
checks contra la base real: precisión de `NUMERIC` (33.33 no tiene drift de
float), que los `CHECK` realmente rechacen stock negativo, que
`SELECT ... FOR UPDATE` funcione, la ventana anti-duplicado contra el reloj real,
y el ciclo completo de un pedido hasta la entrega.

Ese script se limpia a sí mismo en un `finally`, así que una corrida que muere a
la mitad no deja fixtures en la base de demo. No es teórico: la primera versión
se caía antes del cleanup y dejó un vendedor, un comercio, un producto y tres
filas `earn` atrás, que después aparecieron en los datos sembrados.

### Pruebas de concurrencia

Dos clientes no pueden gastar los mismos puntos: `redeem` bloquea la fila del
usuario con `FOR UPDATE` antes de leer el saldo. Un login simultáneo con el mismo
`google_sub` no crea dos usuarios, porque hay `UNIQUE` y la segunda inserción
cae en `IntegrityError` y se reintenta como lectura.

---

## 9. Estructura

```
app.py              app factory, ciclo de request, CLI
config.py           configuración desde .env
auth.py             JWT, usuario actual, decoradores de rol
google_auth.py      verificación de identidad Google
enums.py            enums de dominio
roles.py            UserRole y helpers
extensions.py       db, cors, limiter
schema.sql          referencia legible del modelo de datos
seed.py             seed-demo / reset-demo / dev-token
models/             8 tablas SQLAlchemy
services/           lógica de negocio (orders, points, rewards, business, catalog)
api/                blueprints
tests/              suite
tools/              smoke_postgres.py
```

`api/` y `services/` están separados a propósito: las rutas traducen HTTP y
validan; los servicios contienen las reglas y son lo que se puede probar sin
un cliente de prueba. La lógica de negocio **no** debería vivir en un blueprint.

---

## 10. Estado y limitaciones

Es un prototipo de backend, no un producto en producción. Lo que falta:

- **Sin migraciones.** No hay Alembic. Si cambia un modelo de forma incompatible,
  la vía rápida es `flask reset-demo --yes`. Aceptable con datos de demo;
  no con datos reales.
- **`_generate_order_number()` acepta una carrera.** Usa `COUNT(*)+1`, y el
  `UNIQUE` rechaza una colisión con un 409. No hay retry: hacerlo exigiría
  repetir también los decrementos de stock, y el cliente reintenta el pedido.
  Con volumen alto, un secuencia de PostgreSQL lo resuelve.
- **Sin rate limiting distribuido.** Solo `memory://`, que es por proceso (ver §4).
- **`category` es un `String` libre**, no un enum ni una tabla: cada comercio
  escribe lo que quiere. `/api/categories` lo agrega.
- **Sin notificaciones**: nada avisa al cliente que su pedido está listo.
- **Los puntos se acreditan al entregar**, así que un cliente que nunca llega no
  genera saldo. Es intencional, pero es una decisión de negocio, no técnica.
- **Sin refresh tokens.** Un JWT vencido obliga a volver a hacer login con Google.
- **Los pedidos no expiran.** Un pedido `received` abandonado bloquea stock para
  siempre.