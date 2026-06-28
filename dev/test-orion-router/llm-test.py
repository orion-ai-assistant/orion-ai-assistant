import sys
from openai import OpenAI
from colorama import init, Fore, Style

init(autoreset=True)

# Orion Router'ın adresi ve Senin Sanal Anahtarın
client = OpenAI(
    base_url="http://localhost:20128/v1",
    api_key="sk-orion-HDugC8SbKPBv1pp7ysRP-sQqPwsS-tWVr37AkhhrZqM",
    default_headers={"x-orion-provider": "local"} 
)

def orion_chat_session():
    print(f"{Fore.CYAN}=== ORION AI SOHBET MODU (OPENAI SDK & DÜŞÜNME AKTİF) ==={Style.RESET_ALL}")
    print(f"{Fore.YELLOW}Çıkmak için 'exit' veya 'quit' yazabilirsiniz.\n{Style.RESET_ALL}")

    messages = [
        {"role": "system", "content": "Sen yetenekli, Türkçe konuşan Orion AI asistanısın."}
    ]

    while True:
        try:
            user_input = input(f"{Fore.GREEN}Siz: {Style.RESET_ALL}")
        except (KeyboardInterrupt, EOFError):
            break
            
        if user_input.lower() in ['exit', 'quit', 'çıkış']:
            print(f"\n{Fore.CYAN}Görüşmek üzere!{Style.RESET_ALL}")
            break

        if not user_input.strip():
            continue

        messages.append({"role": "user", "content": user_input})
        
        try:
            # İstek atıyoruz (Tıpkı ChatGPT'ye atar gibi)
            response = client.chat.completions.create(
                model="gemini-2.5-flash-lite",
                messages=messages,
                stream=True,
                extra_body={"thinking_level": "512"} # Router'a düşünme bütçesi tetikleyicisi gönderiyoruz
            )
            
            full_response = ""
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
                    
            print("\n")
            messages.append({"role": "assistant", "content": full_response})
            
        except Exception as e:
            # Hata olduğunda kütüphane 403 / 402 kodunu ve hatayı direkt gösterir
            print(f"\n{Fore.RED}API Hatası Oluştu: {e}{Style.RESET_ALL}\n")

if __name__ == "__main__":
    orion_chat_session()
