from sqlalchemy import create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
import os

database_dir = os.getenv("DATABASE_DIR", './db')

database_filename = 'test.db'

database_path = os.path.join(database_dir, database_filename)


DATABASE_URL = f'sqlite:///{database_path}'

engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
