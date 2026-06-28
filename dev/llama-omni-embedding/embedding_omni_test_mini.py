import requests
import base64

def get_image_base64(path):
    with open(path, "rb") as f:
        return base64.b64encode(f.read()).decode('utf-8')

url = "http://localhost:8081/embedding"
image_str = get_image_base64("img.png")

payload = {
    "image_data": [{"data": image_str, "id": 0}]
}

response = requests.post(url, json=payload)
print(response.json()) # Eğer 'embedding' içinde uzun bir sayı listesi geliyorsa çalışıyor demektir.