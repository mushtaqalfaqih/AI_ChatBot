from config import settings
from google import genai

key = settings.GEMINI_API_KEY.get_secret_value()
client = genai.Client(api_key=key)

try:
    for m in client.models.list():
        print(m.name)
except Exception as e:
    print("Error:", e)
