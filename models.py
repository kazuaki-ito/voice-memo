# models.py
from sqlalchemy import Column, Integer, String, DateTime, ForeignKey
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from datetime import datetime
from database import Base

class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True)
    line_user_id = Column(String, unique=True, index=True)
    display_name = Column(String)
    recordings = relationship("Recording", back_populates="user")

class Recording(Base):
    __tablename__ = "recordings"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"))
    filename = Column(String, unique=True)
    transcription = Column(String)
    created_at = Column(DateTime, default=datetime.utcnow)
    recorded_at = Column(DateTime(timezone=True), server_default=func.now())  # 録音日時を追加
    user = relationship("User", back_populates="recordings")
