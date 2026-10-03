"""Flask application factory.

`create_app()` wires the extensions, the blueprints and the error handlers onto
a Flask instance. `app` at the bottom is the module-level instance so
`flask run`, `python app.py` and `from app import app` all keep working.

Request lifecycle for an API call:

    before_request  -> load_current_user() puts the caller in g.current_user
    route           -> parses input, calls one service function
    service         -> runs the rule and commits exactly once
    after_request   -> rollback anything left open, so a read-only request
                       that touched the session does not hold a transaction

Order matters: the rollback in after_request runs even when a handler raised,
which is what keeps a failed request from holding row locks.
"""

import click
from flask import Flask

import config
from auth import load_current_user
from extensions import cors, db, limiter

# Importing the package registers every model on db.metadata, without which
# create_all() would build an empty schema and the relationships would not
# resolve.
import models  # noqa: F401


def create_app(config_object=None) -> Flask:
    app = Flask(__name__)

    app.config.update(
        SECRET_KEY=config.SECRET_KEY,
        SQLALCHEMY_DATABASE_URI=config.DATABASE_URL,
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
        SQLALCHEMY_ECHO=config.SQLALCHEMY_ECHO,
        TESTING=config.TESTING,
        DEBUG=config.DEBUG,
        JSON_SORT_KEYS=False,
        MAX_CONTENT_LENGTH=1 * 1024 * 1024,  # 1 MB: this API takes JSON, not uploads
        # Off under TESTING so a fast suite is not throttled by its own fixtures.
        RATELIMIT_ENABLED=not config.TESTING,
        RATELIMIT_STORAGE_URI=config.RATELIMIT_STORAGE_URI,
        RATELIMIT_HEADERS_ENABLED=True,
        SCAN_DUPLICATE_WINDOW_SECONDS=config.SCAN_DUPLICATE_WINDOW_SECONDS,
    )
    if config_object:
        app.config.update(config_object)

    db.init_app(app)
    limiter.init_app(app)

    if config.CORS_ORIGINS:
        # Only when explicitly configured. Same-origin (frontend served by this
        # app or a reverse proxy) needs no CORS headers at all, and leaving
        # CORS off by default means a forgotten env var cannot accidentally open
        # the API to any origin.
        cors.init_app(
            app,
            resources={r"/api/*": {"origins": config.CORS_ORIGINS}},
            supports_credentials=True,
        )

    from api import register_blueprints
    from api.errors import register_error_handlers

    register_blueprints(app)
    register_error_handlers(app)

    app.before_request(load_current_user)

    @app.after_request
    def _end_request(response):
        # Services commit exactly once, so by the time a handler returns there
        # is nothing left to save. Rolling back here is therefore safe and it
        # does two useful things: it releases the connection and any row locks
        # immediately instead of waiting for garbage collection, and it
        # discards a half-written unit of work if a handler returned early
        # without going through a service. It also runs after an unhandled
        # exception, which is when holding a lock would hurt most.
        db.session.rollback()
        return response

    _register_cli(app)
    return app


def _register_cli(app: Flask) -> None:
    @app.cli.command("init-db")
    def init_db():
        """Create any missing tables.

        Safe to run repeatedly. This is a prototype schema: if a model changes
        incompatibly, drop the database and run it again rather than trying to
        migrate by hand. There is no migration tool on purpose -- at this stage
        a clean reseed is faster and less error-prone than a migration history,
        and nothing in this database is worth preserving across a schema change.
        """
        db.create_all()
        click.echo("Tables created: " + ", ".join(sorted(db.metadata.tables)))

    @app.cli.command("drop-db")
    @click.option("--yes", is_flag=True, help="Skip the confirmation prompt.")
    def drop_db(yes):
        """Drop every table. Destructive."""
        if not yes:
            click.confirm("This deletes ALL data. Continue?", abort=True)
        db.drop_all()
        click.echo("All tables dropped.")

    # seed-demo / reset-demo / dev-token
    import seed

    seed.register(app)


app = create_app()


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=config.DEBUG)
