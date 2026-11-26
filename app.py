from flask import Flask, jsonify
from werkzeug.exceptions import HTTPException

import db

class APIError(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def create_app(db_path=None):
    app = Flask(__name__)
    if db_path is None:
        db_path = db.DEFAULT_DB
    app.config["DB_PATH"] = str(db_path)

    db.init_app(app)

    from api.auth_routes import bp as auth_bp
    from api.exchange_routes import bp as exchanges_bp
    from api.inventory_routes import bp as inventory_bp
    from api.notification_routes import bp as notifications_bp

    for bp in (auth_bp, inventory_bp, exchanges_bp, notifications_bp):
        app.register_blueprint(bp)

    @app.errorhandler(APIError)
    def handle_api_error(e):
        return jsonify(error=str(e)), e.status

    @app.errorhandler(HTTPException)
    def handle_http_error(e):
        return jsonify(error=e.description), e.code

    @app.errorhandler(Exception)
    def handle_unexpected(e):
        app.logger.exception("Unhandled error")
        return jsonify(error="internal server error"), 500

    return app