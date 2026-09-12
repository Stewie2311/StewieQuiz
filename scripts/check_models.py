"""
Liệt kê các model Gemini mà API key hiện tại dùng được.

Công cụ chẩn đoán, chạy tay khi cần:
    py -3.11 scripts/check_models.py

Dùng khi tính năng sinh câu hỏi báo lỗi model — chạy cái này để biết tên model
trong GEMINI_MODELS (routes/exam.py) còn hợp lệ hay đã bị Google khai tử.
"""
import os
import requests
from dotenv import load_dotenv

load_dotenv()

API_KEY = os.getenv('GEMINI_API_KEY')

url  = f"https://generativelanguage.googleapis.com/v1beta/models?key={API_KEY}"
resp = requests.get(url)
data = resp.json()

print("=" * 60)
print("CÁC MODEL AVAILABLE:")
print("=" * 60)
for m in data.get('models', []):
    methods = m.get('supportedGenerationMethods', [])
    if 'generateContent' in methods:
        print(m['name'])
