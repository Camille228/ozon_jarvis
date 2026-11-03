import os
from dotenv import load_dotenv

# Загружаем переменные из файла .env (если он есть)
load_dotenv()

# Читаем ключи. Если их нет, они будут равны None (пустоте).
OZON_CLIENT_ID = os.getenv("OZON_CLIENT_ID")
OZON_API_KEY = os.getenv("OZON_API_KEY")