# test_api.py
from google import genai
from google.genai import types
from decouple import config

api_key = config("GEMINI_API_KEY")
client = genai.Client(api_key=api_key)

print("=== Test Generate with Gemini 2.5 Flash ===")
try:
    response = client.models.generate_content(
        model="models/gemini-2.5-flash",
        contents="Say hello in Python code",
        config=types.GenerateContentConfig(
            temperature=0.7,
        )
    )
    print("✅ Success!")
    print(f"Response: {response.text}")
except Exception as e:
    print(f"❌ Error: {e}")