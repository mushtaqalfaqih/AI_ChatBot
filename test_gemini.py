from config import settings
from chatbot import GeminiProvider, LLMRequest, Message
try:
    req = LLMRequest(system="You are a helpful assistant.", messages=[Message(role="user", content="Hello")])
    prov = GeminiProvider()
    print("Provider created")
    print("Response:", prov.generate(req))
except Exception as e:
    import traceback
    traceback.print_exc()
