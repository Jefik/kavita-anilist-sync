import logging
import os

from aiohttp import web

from .engine import Engine
from .store import Store
from .web import create_app

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logging.getLogger("aiohttp.access").setLevel(logging.WARNING)
engine = Engine(Store(os.environ.get("DATA_DIR", "data")), os.environ["KAVITA_URL"])
web.run_app(create_app(engine), host=os.environ.get("HOST", "0.0.0.0"), port=int(os.environ.get("PORT", 8091)), print=None)
