from functools import wraps


def hash_password(password):
    raise NotImplementedError


def check_password(password_hash, password):
    raise NotImplementedError


def create_token(user_id):
    raise NotImplementedError


def revoke_token(token):
    raise NotImplementedError


def user_for_token(token):
    raise NotImplementedError


def login_required(view):
    @wraps(view)
    def wrapper(*args, **kwargs):
        raise NotImplementedError
    return wrapper


def admin_required(view):
    @wraps(view)
    def wrapper(*args, **kwargs):
        raise NotImplementedError
    return wrapper
