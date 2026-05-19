import os
from google import genai
from google.genai import types

def my_tool(a: int, b: int) -> int:
    """Adds two numbers."""
    return a + b

try:
    client = genai.Client(vertexai=True, project=os.getenv("VERTEX_PROJECT", "test"), location=os.getenv("VERTEX_LOCATION", "us-central1"))
    config = types.GenerateContentConfig(
        tools=[my_tool],
        temperature=0.1
    )
    print("Google GenAI supports tools!")
except Exception as e:
    print(f"Error: {e}")
