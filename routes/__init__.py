from flask import url_for as flask_url_for
from werkzeug.routing import BuildError

from routes.auth import auth_bp
from routes.admin import admin_bp
from routes.defects import defects_bp
from routes.assets import assets_bp
from routes.settings import settings_bp
from routes.errors import errors_bp
from routes.workspace import workspace_bp


def smart_url_for(endpoint, **values):
    """Fallback-aware url_for ensuring legacy bare endpoints resolve seamlessly."""
    try:
        return flask_url_for(endpoint, **values)
    except BuildError:
        for prefix in ("defects", "auth", "admin", "assets", "settings", "workspace"):
            try:
                return flask_url_for(f"{prefix}.{endpoint}", **values)
            except BuildError:
                pass
        raise


def register_blueprints(app):
    """Register all application blueprints and setup URL alias resolution."""
    app.register_blueprint(auth_bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(defects_bp)
    app.register_blueprint(assets_bp)
    app.register_blueprint(settings_bp)
    app.register_blueprint(errors_bp)
    app.register_blueprint(workspace_bp)

    # Alias bare endpoints to support existing template url_for('endpoint_name') seamlessly
    for rule in list(app.url_map.iter_rules()):
        if "." in rule.endpoint:
            bare_endpoint = rule.endpoint.split(".", 1)[1]
            if bare_endpoint not in app.view_functions:
                app.add_url_rule(
                    rule.rule,
                    endpoint=bare_endpoint,
                    view_func=app.view_functions[rule.endpoint],
                    methods=rule.methods,
                )

    # Provide smart fallback url_for in Jinja templates
    app.jinja_env.globals["url_for"] = smart_url_for
