from sqlalchemy import create_engine, Column, Integer, String, ForeignKey, DateTime, Boolean, text
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, relationship
import datetime
import os

# Create a 'data' folder for persistent cloud storage
os.makedirs("data", exist_ok=True)

# Point the database inside the data folder
DATABASE_URL = "sqlite:///./data/pup_repository.db"
engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True)
    username = Column(String, unique=True)
    password = Column(String)
    role = Column(String)

class Event(Base):
    __tablename__ = "events"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, unique=True)
    is_starred = Column(Boolean, default=False)
    files = relationship("File", back_populates="event", cascade="all, delete-orphan")

class File(Base):
    __tablename__ = "files"
    id = Column(Integer, primary_key=True, index=True)
    filename = Column(String)
    file_path = Column(String)
    upload_date = Column(DateTime, default=datetime.datetime.utcnow)
    event_id = Column(Integer, ForeignKey("events.id"))
    event = relationship("Event", back_populates="files")

class CalendarEvent(Base):
    __tablename__ = "calendar_events"
    id = Column(Integer, primary_key=True, index=True)
    date_str = Column(String, unique=True)
    description = Column(String)

def init_db():
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    
    try:
        db.execute(text("SELECT is_starred FROM events LIMIT 1"))
    except Exception:
        db.execute(text("ALTER TABLE events ADD COLUMN is_starred BOOLEAN DEFAULT 0"))
        db.commit()

    if not db.query(User).first():
        db.add_all([
            User(username="admin", password="123", role="admin"),
            User(username="student", password="123", role="student")
        ])
        db.commit()
    db.close()

if __name__ == "__main__":
    init_db()