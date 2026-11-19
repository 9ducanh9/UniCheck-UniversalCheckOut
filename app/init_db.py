from .config import Settings
from .db import Base, database

if __name__ == '__main__':
    engine,_ = database(Settings().database_url)
    Base.metadata.create_all(engine)
    engine.dispose()
    print('Initial schema created. This command does not migrate existing tables.')
