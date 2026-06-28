import sys
import json
from openai import OpenAI
from colorama import init, Fore, Style

init(autoreset=True)

# Orion Router'ın adresi ve Senin Sanal Anahtarın
client = OpenAI(
    base_url="http://localhost:20128/v1",
    api_key="sk-orion--ckpS6QQtoKm8U-S39qUxfsA8yHOeqjI8-3WZY2akvc",
    # Varsayılan provider artık istek bazında extra_headers üzerinden yönetilecek
)

def get_weather(location: str):
    """Sıcaklığı döndüren basit bir dummy fonksiyon."""
    if "istanbul" in location.lower():
        return "15°C, parçalı bulutlu"
    elif "ankara" in location.lower():
        return "10°C, rüzgarlı"
    else:
        return "20°C, güneşli"

tools = [
    {
        "type": "function",
        "function": {
            "name": "get_weather",
            "description": "Belirtilen şehrin hava durumunu getirir.",
            "parameters": {
                "type": "object",
                "properties": {
                    "location": {
                        "type": "string",
                        "description": "Şehir adı, örn: Istanbul, Ankara"
                    }
                },
                "required": ["location"]
            }
        }
    }
]

def orion_chat_session():
    print(f"{Fore.CYAN}=== ORION AI SOHBET MODU (TOOL CALL TEST) ==={Style.RESET_ALL}")
    print(f"{Fore.YELLOW}Çıkmak için 'exit' veya 'quit' yazabilirsiniz.")
    print(f"Provider değiştirmek için: '/provider openai'")
    print(f"Model değiştirmek için: '/model gpt-4o'\n{Style.RESET_ALL}")
    
    current_provider = "gemini"
    current_model = "gemini-3.1-flash-lite"
    
    messages = [
        {"role": "system", "content": "Sen yetenekli bir asistanın. Gerektiğinde hava durumu aracını kullan."}
    ]

    while True:
        try:
            user_input = input(f"{Fore.GREEN}Siz: {Style.RESET_ALL}")
        except (KeyboardInterrupt, EOFError):
            break
            
        if user_input.lower() in ['exit', 'quit', 'çıkış']:
            break

        if user_input.startswith("/provider "):
            current_provider = user_input.split(" ", 1)[1].strip()
            print(f"{Fore.CYAN}Provider değiştirildi: {current_provider}{Style.RESET_ALL}")
            continue

        if user_input.startswith("/model "):
            current_model = user_input.split(" ", 1)[1].strip()
            print(f"{Fore.CYAN}Model değiştirildi: {current_model}{Style.RESET_ALL}")
            continue

        if not user_input.strip():
            continue

        messages.append({"role": "user", "content": user_input})
        
        # Tool call'ları ardışık şekilde işleyebilmek için döngü (örn. asistan tool çağırır, cevap alır, tekrar çağırabilir)
        while True:
            try:
                response = client.chat.completions.create(
                    model=current_model,
                    messages=messages,
                    stream=True,
                    tools=tools,
                    extra_headers={"x-orion-provider": current_provider},
                    extra_body={"thinking_level": "medium"} # Router'a düşünme bütçesi tetikleyicisi gönderiyoruz
                )
                
                full_response = ""
                tool_calls_dict = {}
                is_thinking = False
                has_started_answer = False
                
                for chunk in response:
                    if not chunk.choices:
                        continue
                        
                    delta = chunk.choices[0].delta
                    
                    # 1. Düşünme (Reasoning) içeriğini al
                    reasoning = getattr(delta, "reasoning_content", None)
                    if reasoning is None and hasattr(delta, "model_extra") and delta.model_extra:
                        reasoning = delta.model_extra.get("reasoning_content")
                        
                    if reasoning:
                        if not is_thinking:
                            print(f"\n{Fore.BLACK}{Style.BRIGHT}💭 Düşünülüyor...{Style.RESET_ALL}")
                            is_thinking = True
                        print(f"{Fore.BLACK}{Style.BRIGHT}{reasoning}", end="", flush=True)
                        continue

                    # 2. Normal metin (Content) içeriğini al
                    text = delta.content
                    if text:
                        if not has_started_answer:
                            print(f"\n{Fore.MAGENTA}Orion: {Style.RESET_ALL}", end="", flush=True)
                            has_started_answer = True
                            
                        print(f"{Fore.WHITE}{text}", end="", flush=True)
                        full_response += text

                    # 3. Tool Calls parsing (streaming format)
                    if delta.tool_calls:
                        for tc in delta.tool_calls:
                            idx = tc.index
                            if idx not in tool_calls_dict:
                                tool_calls_dict[idx] = {
                                    "id": tc.id or f"call_{idx}",
                                    "type": "function",
                                    "function": {"name": tc.function.name or "", "arguments": ""}
                                }
                            if getattr(tc.function, "arguments", None):
                                tool_calls_dict[idx]["function"]["arguments"] += tc.function.arguments

                print("\n")
                
                if tool_calls_dict:
                    print(f"{Fore.YELLOW}🛠️ Tool çağrısı yakalandı!{Style.RESET_ALL}")
                    
                    # Mesajlara asistanın tool_call'unu ekle
                    assistant_message = {"role": "assistant", "content": full_response, "tool_calls": list(tool_calls_dict.values())}
                    messages.append(assistant_message)
                    
                    for idx, tc in tool_calls_dict.items():
                        name = tc["function"]["name"]
                        args = tc["function"]["arguments"]
                        print(f"{Fore.YELLOW}  - {name}({args}){Style.RESET_ALL}")
                        
                        # Tool'u çalıştır
                        if name == "get_weather":
                            args_dict = json.loads(args)
                            result = get_weather(args_dict.get("location", ""))
                            print(f"{Fore.YELLOW}  - Sonuç: {result}{Style.RESET_ALL}")
                            
                            messages.append({
                                "role": "tool",
                                "tool_call_id": tc["id"],
                                "name": name,
                                "content": result
                            })
                    
                    # Tool sonucu mesaja eklendi, tekrar modelin cevabını almak için döngü devam eder
                    continue
                else:
                    if full_response:
                        messages.append({"role": "assistant", "content": full_response})
                    break # Tool call yoksa bu isteği sonlandır, yeni kullanıcı girdisi bekle
                
            except Exception as e:
                print(f"\n{Fore.RED}API Hatası Oluştu: {e}{Style.RESET_ALL}\n")
                break

if __name__ == "__main__":
    orion_chat_session()
