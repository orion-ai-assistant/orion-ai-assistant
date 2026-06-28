import requests
import json
import sys
import re
from colorama import init, Fore, Style

# Renkli terminal çıktısı için başlatma
init(autoreset=True)

def orion_chat_session():
    url = "http://localhost:8200/v1/chat/completions"
    
    # Sohbet geçmişini tutmak için liste
    messages = [
        {"role": "system", "content": "Sen Orion AI asistanısın. Düşüncelerini paylaşan, yardımsever ve profesyonel bir yapay zekasın."}
    ]

    print(f"{Fore.CYAN}=== ORION AI SOHBET MODU (GELİŞMİŞ DÜŞÜNME AKTİF) ==={Style.RESET_ALL}")
    print(f"{Fore.YELLOW}Çıkmak için 'exit' veya 'quit' yazabilirsiniz.\n{Style.RESET_ALL}")

    while True:
        # Kullanıcıdan girdi al
        user_input = input(f"{Fore.GREEN}Siz: {Style.RESET_ALL}")
        
        if user_input.lower() in ['exit', 'quit', 'çıkış']:
            print(f"\n{Fore.CYAN}Görüşmek üzere!{Style.RESET_ALL}")
            break

        if not user_input.strip():
            continue

        # Kullanıcı mesajını geçmişe ekle
        messages.append({"role": "user", "content": user_input})
        
        payload = {
            "model": "gemma-4-e2b-it",
            "messages": messages,
            "temperature": 0.7, 
            "stream": True,
            "max_tokens": 4096
        }

        full_response = ""
        has_started_answer = False
        is_thinking = False

        try:
            # Sunucuya istek atıyoruz
            response = requests.post(url, json=payload, stream=True, timeout=None)
            
            # Satır satır gelen veriyi okuyoruz
            for line in response.iter_lines():
                if line:
                    decoded_line = line.decode('utf-8')
                    if decoded_line.startswith("data: "):
                        content_json = decoded_line[6:]
                        
                        if content_json.strip() == "[DONE]":
                            break
                            
                        try:
                            data = json.loads(content_json)
                            if 'choices' in data and len(data['choices']) > 0:
                                delta = data['choices'][0].get('delta', {})
                                
                                # 1. API seviyesinde reasoning_content (DeepSeek vb.)
                                reasoning = delta.get('reasoning_content', '')
                                if reasoning:
                                    if not is_thinking:
                                        print(f"\n{Fore.BLACK}{Style.BRIGHT}💭 Düşünülüyor...{Style.RESET_ALL}")
                                        is_thinking = True
                                    print(f"{Fore.BLACK}{Style.BRIGHT}{reasoning}", end="", flush=True)
                                    continue # Reasoning geldiyse content kısmına bakma

                                # 2. Metin içindeki düşünce etiketlerini yakalama (<thought>...</thought>)
                                text = delta.get('content', '')
                                if text:
                                    # Başlangıç etiketi kontrolü
                                    if "<thought>" in text:
                                        print(f"\n{Fore.BLACK}{Style.BRIGHT}💭 Düşünülüyor...{Style.RESET_ALL}")
                                        is_thinking = True
                                        text = text.replace("<thought>", "")
                                    
                                    # Bitiş etiketi kontrolü
                                    if "</thought>" in text:
                                        is_thinking = False
                                        parts = text.split("</thought>")
                                        # Etiket öncesi düşüncedir
                                        if parts[0]:
                                            print(f"{Fore.BLACK}{Style.BRIGHT}{parts[0]}", end="", flush=True)
                                        # Etiket sonrası normal cevaptır
                                        text = parts[1] if len(parts) > 1 else ""
                                        has_started_answer = False # Cevap başlığını tekrar tetiklemek için
                                    
                                    if is_thinking:
                                        # Düşünme aşamasındaysak gri yaz
                                        print(f"{Fore.BLACK}{Style.BRIGHT}{text}", end="", flush=True)
                                    else:
                                        # Normal cevap aşamasındaysak
                                        if not has_started_answer and text.strip():
                                            print(f"\n{Fore.MAGENTA}Orion: {Style.RESET_ALL}", end="", flush=True)
                                            has_started_answer = True
                                        
                                        if text:
                                            print(f"{Fore.WHITE}{text}", end="", flush=True)
                                            full_response += text
                                    
                        except (json.JSONDecodeError, KeyError, IndexError):
                            continue

            print("\n") # Mesaj bitince alt satıra geç
            
            # Asistanın cevabını geçmişe ekle
            messages.append({"role": "assistant", "content": full_response})

        except requests.exceptions.ConnectionError:
            print(f"\n{Fore.RED}Hata: llama-server'a bağlanılamadı.")
            break
        except Exception as e:
            print(f"\n{Fore.RED}Beklenmeyen bir hata oluştu: {e}")
            break

if __name__ == "__main__":
    orion_chat_session()