-- =============================================================================
--  PASEO ARANJUEZ -- esquema de la base de datos
-- =============================================================================
--  Este archivo es la REFERENCIA LEGIBLE del modelo de datos. La aplicacion
--  crea las tablas con `flask --app app init-db` (db.create_all() sobre los
--  modelos de SQLAlchemy en models/), que es el fuente de verdad.
--
--  Si cambias una columna, cambiala acá Y en el modelo, o el proximo
--  `db.create_all()` va a generar algo distinto de este documento.
--
--  Cubre los dos retos del hackaton:
--    * Paseo Points  -> users, business, rewards, coupons, transactions
--    * PaseoYa       -> products, orders, order_items
--  Y el puente entre ambos: orders.coupon_id y transactions.order_id.
--
--  ORDEN DE CREACION: users -> business -> products -> rewards -> coupons
--                     -> orders -> order_items -> transactions
--  (cada tabla solo referencia las que ya existen; este archivo corre entero
--   de arriba abajo sin error)
--
--  CORRECCIONES respecto del esquema original del hackaton:
--    1. Las FK apuntaban a una tabla `merchants` que nunca se creo. La tabla
--       real es `business`, y todas las FK ahora apuntan ahi.
--    2. El `ALTER TABLE transactions` estaba antes del `CREATE TABLE
--       transactions`, asi que el script no podia correrse de arriba abajo.
--       El CHECK de `type` ahora se declara dentro del CREATE TABLE.
--    3. Se elimino `password_hash TEXT NOT NULL`: el login es 100% Google, sin
--       contrasenas, y un NOT NULL habria roto todos los INSERT.
--    4. Se agrego `users.google_sub`: es la clave de identidad real del codigo.
--    5. Los roles son USER / SELLER / ADMIN, que son los que viajan en el JWT
--       y usan las pruebas.
--    6. Los defaults de id y de codigos se generan en Python (ver
--       models/base.py), no con pgcrypto, para que los mismos modelos
--       funcionen tambien sobre SQLite en la suite de tests.
-- =============================================================================

CREATE EXTENSION IF NOT EXISTS pgcrypto;   -- solo por gen_random_uuid(); el app no lo usa


-- -----------------------------------------------------------------------------
--  USERS
--  Tres poblaciones comparten esta tabla, distinguidas por `role`:
--    USER   -> cliente de Paseo Points
--    SELLER -> dueno de un negocio (siempre 1:1 con `business`)
--    ADMIN  -> staff de Paseo Aranjuez
-- -----------------------------------------------------------------------------
CREATE TABLE users (
    id            UUID PRIMARY KEY,                       -- uuid4 generado en Python
    email         TEXT UNIQUE NOT NULL,                   -- SIEMPRE en minusculas (unicidad)
    -- Identidad real de la cuenta: el login es solo Google OAuth.
    google_sub    TEXT UNIQUE,
    -- Sin uso hoy (no hay login local). Nullable para no romper los INSERT.
    password_hash TEXT,
    name          TEXT NOT NULL,
    phone         TEXT,
    -- Avatar de Google y geolocalizacion del dispositivo
    picture       TEXT,
    latitude      DOUBLE PRECISION,
    longitude     DOUBLE PRECISION,
    role          TEXT NOT NULL DEFAULT 'USER'
                  CHECK (role IN ('USER', 'SELLER', 'ADMIN')),
    -- Nivel de fidelizacion (reto 3.9). Se recalcula en services/points.py
    -- cada vez que cambia el saldo, dentro de la misma transaccion.
    level         TEXT NOT NULL DEFAULT 'BRONZE'
                  CHECK (level IN ('BRONZE', 'SILVER', 'GOLD', 'PLATINUM')),
    -- El codigo que el comercio escanea para acreditar puntos (reto 3.7).
    -- 16 hex de aleatoriedad criptografica: es una credencial, no un id
    -- secuencial, asi que no se puede adivinar ni recorrer.
    qr_code       TEXT UNIQUE NOT NULL,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX ix_users_role ON users(role);
CREATE INDEX ix_users_level ON users(level);


-- -----------------------------------------------------------------------------
--  BUSINESS
--  Un negocio participante. `points_per_bs` es SU propia tasa de canje
--  (reto 3.5: "Bs 1 gastado = 1 punto"), no una config global.
--  `active = false` lo saca del marketplace y le impide acreditar puntos,
--  pero NO borra su historial de pedidos ni sus movimientos.
-- -----------------------------------------------------------------------------
CREATE TABLE business (
    id            UUID PRIMARY KEY,
    -- UNIQUE: una persona no puede tener dos negocios.
    user_id       UUID UNIQUE NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name          TEXT NOT NULL,
    category      TEXT,
    description   TEXT,
    -- Donde esta dentro del mall ("Plaza nivel 2, local 214"): el marketplace
    -- lo muestra para que el cliente sepa a donde caminar al retirar.
    location      TEXT,
    points_per_bs NUMERIC(6,2) NOT NULL DEFAULT 1 CHECK (points_per_bs > 0),
    active        BOOLEAN NOT NULL DEFAULT TRUE,
    -- Envios a domicilio opcionales (configurables por negocio)
    offers_delivery BOOLEAN NOT NULL DEFAULT FALSE,
    delivery_fee_bs NUMERIC(10,2) NOT NULL DEFAULT 0 CHECK (delivery_fee_bs >= 0),
    delivery_info   TEXT,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX ix_business_category ON business(category);
CREATE INDEX ix_business_active ON business(active);


-- -----------------------------------------------------------------------------
--  PRODUCTS -- catalogo de PaseoYa
--  Cada producto pertenece a EXACTAMENTE un negocio: el marketplace es un
--  conjunto de catalogos independientes, no un inventario compartido
--  (reto 5.10). `stock` se descuenta dentro de la transaccion del pedido, asi
--  que dos clientes disputando la ultima unidad no pueden ganar los dos.
-- -----------------------------------------------------------------------------
CREATE TABLE products (
    id          UUID PRIMARY KEY,
    business_id UUID NOT NULL REFERENCES business(id) ON DELETE CASCADE,
    name        TEXT NOT NULL,
    description TEXT,
    -- Desnormalizado por producto para que el marketplace pueda filtrar por
    -- categoria entre todos los negocios sin pasar por business.
    category    TEXT,
    price_bs    NUMERIC(10,2) NOT NULL CHECK (price_bs > 0),
    stock       INT NOT NULL DEFAULT 0 CHECK (stock >= 0),
    active      BOOLEAN NOT NULL DEFAULT TRUE,
    image_url   TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX ix_products_business_active ON products(business_id, active);
CREATE INDEX ix_products_category ON products(category);
-- Soporta el buscador global del reto 5.11 ("Audifonos Bluetooth" en todas las
-- tiendas, para comparar precio, disponibilidad y ubicacion).
CREATE INDEX ix_products_search ON products
    USING gin (to_tsvector('spanish', name || ' ' || COALESCE(description, '')));


-- -----------------------------------------------------------------------------
--  REWARDS -- catalogo de BENEFICIOS que el cliente compra con puntos
--  No son productos: el Paseo no entrega nada fisico ni toca la caja.
--  El cliente recibe un codigo y el negocio aplica el descuento en su propio
--  sistema de ventas.
--  `business_id IS NULL` = cupon patrocinado por la administracion del Paseo.
-- -----------------------------------------------------------------------------
CREATE TABLE rewards (
    id              UUID PRIMARY KEY,
    business_id     UUID REFERENCES business(id) ON DELETE CASCADE,
    title           TEXT NOT NULL,
    description     TEXT,
    discount_type   TEXT NOT NULL CHECK (discount_type IN ('percent', 'fixed')),
    discount_value  NUMERIC(8,2) NOT NULL CHECK (discount_value > 0),
    -- Tope del descuento en Bs. Obligatorio en porcentajes (ver CHECK de
    -- abajo) para que el costo sea sostenible para el negocio.
    -- NULL = sin tope (solo en descuentos fijos).
    max_discount_bs NUMERIC(8,2) CHECK (max_discount_bs IS NULL OR max_discount_bs > 0),
    min_purchase_bs NUMERIC(10,2) NOT NULL DEFAULT 0 CHECK (min_purchase_bs >= 0),
    points_cost     INT NOT NULL CHECK (points_cost > 0),
    valid_days      INT NOT NULL DEFAULT 30 CHECK (valid_days > 0),
    stock           INT CHECK (stock IS NULL OR stock >= 0),   -- NULL = ilimitado
    active          BOOLEAN NOT NULL DEFAULT TRUE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    -- Un porcentaje de mas del 100% no tiene sentido.
    CHECK (discount_type <> 'percent' OR discount_value <= 100),
    -- Un porcentaje sin tope es una pasiva ilimitada para quien lo financia.
    CHECK (discount_type <> 'percent' OR max_discount_bs IS NOT NULL)
);
CREATE INDEX ix_rewards_business_active ON rewards(business_id, active);


-- -----------------------------------------------------------------------------
--  COUPONS -- un canje concreto
--  Guarda una COPIA de las condiciones del reward en el momento de emitirse.
--  Si el negocio edita o retira la recompensa despues, los cupones ya emitidos
--  conservan los terminos con los que se vendieron. Sin este snapshot, subir
--  el precio reescribiria en silencio la historia de todos los cupones
--  pendientes.
--  `reward_id` SIN cascade a proposito: no se puede borrar una recompensa que
--  ya fue canjeada.
-- -----------------------------------------------------------------------------
CREATE TABLE coupons (
    id              UUID PRIMARY KEY,
    customer_id     UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    reward_id       UUID NOT NULL REFERENCES rewards(id),
    code            TEXT UNIQUE NOT NULL,                  -- 8 hex en mayusculas
    -- --- snapshot de las condiciones al momento del canje ---
    discount_type   TEXT NOT NULL CHECK (discount_type IN ('percent', 'fixed')),
    discount_value  NUMERIC(8,2) NOT NULL,
    max_discount_bs NUMERIC(8,2),
    min_purchase_bs NUMERIC(10,2) NOT NULL DEFAULT 0,
    status          TEXT NOT NULL DEFAULT 'active'
                    CHECK (status IN ('active', 'used', 'cancelled')),
    expires_at      TIMESTAMPTZ NOT NULL,
    -- Negocio que valido el canje en el mostrador.
    validated_by    UUID REFERENCES business(id) ON DELETE SET NULL,
    used_at         TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX ix_coupons_customer_created ON coupons(customer_id, created_at DESC);


-- -----------------------------------------------------------------------------
--  ORDERS -- PaseoYa
--  RETIRO PRESENCIAL OBLIGATORIO (reto 5.6): no hay direccion de entrega ni
--  courier. El punto del reto es que la compra online atraiga gente al Paseo.
--  Por eso un pedido es siempre de UN solo negocio: un carrito con dos tiendas
--  se rechaza en el checkout en vez de dividirse, porque dividirlo serían dos
--  retiros y dos pagos en un sistema disenado para un solo viaje.
-- -----------------------------------------------------------------------------
CREATE TABLE orders (
    id             UUID PRIMARY KEY,
    order_number   TEXT UNIQUE NOT NULL,                    -- "PA-000123", legible
    customer_id    UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    business_id    UUID NOT NULL REFERENCES business(id) ON DELETE CASCADE,
    status         TEXT NOT NULL DEFAULT 'received'
                   CHECK (status IN ('received', 'confirmed', 'preparing',
                                     'ready_for_pickup', 'on_the_way', 'customer_arrived',
                                     'delivered', 'cancelled')),
    -- Tipo de entrega: retiro presencial o envio a domicilio
    delivery_type  TEXT NOT NULL DEFAULT 'pickup'
                   CHECK (delivery_type IN ('pickup', 'delivery')),
    delivery_address TEXT,
    delivery_phone TEXT,
    delivery_instructions TEXT,
    delivery_fee_bs NUMERIC(10,2) NOT NULL DEFAULT 0 CHECK (delivery_fee_bs >= 0),
    -- SIEMPRE recalculados en el servidor desde products.price_bs. El cliente
    -- manda ids y cantidades, nunca precios. Esta es la regla de integridad
    -- mas importante del modulo.
    subtotal_bs    NUMERIC(10,2) NOT NULL DEFAULT 0 CHECK (subtotal_bs >= 0),
    discount_bs    NUMERIC(10,2) NOT NULL DEFAULT 0 CHECK (discount_bs >= 0),
    total_bs       NUMERIC(10,2) NOT NULL DEFAULT 0 CHECK (total_bs >= 0),
    -- El descuento nunca puede superar el subtotal que descuenta.
    CHECK (discount_bs <= subtotal_bs),
    -- Puente entre los dos retos: un cupón de Paseo Points aplicado al pedido.
    -- SET NULL para que cancelar un cupon nunca borre el pedido.
    coupon_id      UUID REFERENCES coupons(id) ON DELETE SET NULL,
    -- Vista previa de lo que va a acreditar. El movimiento real en la tabla
    -- `transactions` se crea al ENTREGAR; esto es solo lo que se muestra antes.
    points_earned  INT NOT NULL DEFAULT 0,
    payment_method TEXT,
    -- 6 digitos, renderizado como QR por el frontend (reto 5.9).
    pickup_code    TEXT NOT NULL,
    note           TEXT,
    confirmed_at   TIMESTAMPTZ,
    ready_at       TIMESTAMPTZ,
    arrived_at     TIMESTAMPTZ,
    delivered_at   TIMESTAMPTZ,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX ix_orders_customer_created ON orders(customer_id, created_at DESC);
CREATE INDEX ix_orders_business_status ON orders(business_id, status);


-- -----------------------------------------------------------------------------
--  ORDER_ITEMS
--  Un pedido es un recibo: tiene que seguir siendo legible aunque el negocio
--  renombre o borre el producto. Por eso copia el nombre y el precio unitario.
--  `product_id` es SET NULL justamente para que borrar un producto NO borre
--  la linea del pedido: las columnas snapshot son el registro real.
-- -----------------------------------------------------------------------------
CREATE TABLE order_items (
    id             UUID PRIMARY KEY,
    order_id       UUID NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
    product_id     UUID REFERENCES products(id) ON DELETE SET NULL,
    -- --- snapshot tomado en el checkout ---
    product_name   TEXT NOT NULL,
    unit_price_bs  NUMERIC(10,2) NOT NULL CHECK (unit_price_bs > 0),
    quantity       INT NOT NULL CHECK (quantity > 0),
    subtotal_bs    NUMERIC(10,2) NOT NULL CHECK (subtotal_bs >= 0)
);
CREATE INDEX ix_order_items_order ON order_items(order_id);


-- -----------------------------------------------------------------------------
--  TRANSACTIONS -- el libro de puntos
--  Append-only: cada cambio de saldo es una fila nueva. Nunca se edita ni se
--  borra. El saldo del cliente es SUM(points) de sus filas, y por eso esta
--  tabla no tiene `updated_at`: si la columna existiera, seria una mentira.
--
--  Un solo libro para los dos retos:
--    * order_id IS NULL -> compra registrada escaneando el QR del cliente
--                          (flujo puro de Paseo Points)
--    * order_id presente -> el pedido de PaseoYa fue entregado
--                          (la integracion entre los dos retos)
--
--  Los puntos se acreditan al ENTREGAR, no al pagar, para que un pedido
--  cancelado no deje puntos fantasma.
-- -----------------------------------------------------------------------------
CREATE TABLE transactions (
    id          UUID PRIMARY KEY,
    customer_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    -- NULL en ajustes del admin y en recompensas patrocinadas por el Paseo.
    business_id UUID REFERENCES business(id) ON DELETE SET NULL,
    -- Enlaza el movimiento con el pedido de PaseoYa que lo origino.
    order_id    UUID REFERENCES orders(id) ON DELETE SET NULL,
    type        TEXT NOT NULL
                -- 'earn'   -> puntos por una compra            (points > 0)
                -- 'redeem' -> gasto al canjear un cupon       (points < 0)
                -- 'refund' -> devolucion por cupon cancelado (points > 0)
                -- 'adjust' -> ajuste manual del admin        (puede ser + o -)
                -- 'bonus'  -> bono por negocio nuevo         (points > 0)
                CHECK (type IN ('earn', 'redeem', 'refund', 'adjust', 'bonus')),
    amount_bs   NUMERIC(10,2) CHECK (amount_bs IS NULL OR amount_bs > 0),  -- solo en 'earn'
    points      INT NOT NULL CHECK (points <> 0),   -- con signo; un 0 es ruido
    coupon_id   UUID REFERENCES coupons(id) ON DELETE SET NULL,
    note        TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
-- "Mis movimientos" siempre son los ultimos N de un cliente o de un negocio,
-- asi que el indice empieza por la FK y ordena por tiempo DESC.
CREATE INDEX ix_transactions_customer_created ON transactions(customer_id, created_at DESC);
CREATE INDEX ix_transactions_business_created ON transactions(business_id, created_at DESC);
