from sqlalchemy import Column, Integer, String, DateTime, ForeignKey, Text
from sqlalchemy.orm import relationship
from datetime import datetime
from .database import Base

class RobotsAnalysis(Base):
    __tablename__ = "robots_analyses"

    id = Column(Integer, primary_key=True, index=True)
    url = Column(String, index=True)
    timestamp = Column(DateTime, default=datetime.utcnow)
    allowed_count = Column(Integer, default=0)
    disallowed_count = Column(Integer, default=0)
    comments_count = Column(Integer, default=0)
    
    allowed_urls = relationship("AllowedUrl", back_populates="analysis")
    disallowed_urls = relationship("DisallowedUrl", back_populates="analysis")
    comments = relationship("RobotsComment", back_populates="analysis")

class AllowedUrl(Base):
    __tablename__ = "allowed_urls"

    id = Column(Integer, primary_key=True, index=True)
    url_pattern = Column(String)
    user_agent = Column(String)
    analysis_id = Column(Integer, ForeignKey("robots_analyses.id"))
    
    analysis = relationship("RobotsAnalysis", back_populates="allowed_urls")

class DisallowedUrl(Base):
    __tablename__ = "disallowed_urls"

    id = Column(Integer, primary_key=True, index=True)
    url_pattern = Column(String)
    user_agent = Column(String)
    analysis_id = Column(Integer, ForeignKey("robots_analyses.id"))
    
    analysis = relationship("RobotsAnalysis", back_populates="disallowed_urls")

class RobotsComment(Base):
    __tablename__ = "robots_comments"

    id = Column(Integer, primary_key=True, index=True)
    comment = Column(Text)
    analysis_id = Column(Integer, ForeignKey("robots_analyses.id"))
    
    analysis = relationship("RobotsAnalysis", back_populates="comments")