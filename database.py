from sqlalchemy import create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker

import os

database_dir = os.getenv("DATABASE_DIR", './db')

if database_dir:
    # ディレクトリが存在しない場合は作成
    if not os.path.exists(database_dir):
        os.makedirs(database_dir, exist_ok=True)
        print(f"ディレクトリ '{database_dir}' を作成しました。")
    else:
        print(f"ディレクトリ '{database_dir}' は既に存在します。")


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
