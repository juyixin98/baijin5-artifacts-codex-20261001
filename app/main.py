"""服务入口:uvicorn app.main:app"""
from .api import create_app
from .config import Settings

app = create_app(Settings.from_env())
