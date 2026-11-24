from flask import Flask


class APIError(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def create_app(db_path=None):
    app = Flask(__name__)
    raise NotImplementedError
    return app
