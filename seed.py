def seed(db_path):
    raise NotImplementedError


if __name__ == '__main__':
    from db import DEFAULT_DB
    seed(DEFAULT_DB)
