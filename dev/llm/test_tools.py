import os
import json
import time
import subprocess
import urllib.request
import urllib.parse
import re
import webbrowser
from colorama import init, Fore, Style
from openai import OpenAI

init(autoreset=True)

# =========================================================
# CONFIG
# =========================================================

# --- Llama.cpp Config (Yerel) ---
# OPENROUTER_API_KEY = "sk-no-key-needed"
# MODEL_NAME = "local-model"
# client = OpenAI(
#     base_url="http://localhost:8200/v1",
#     api_key=OPENROUTER_API_KEY,
# )

# --- OpenRouter Config (Bulut) ---
OPENROUTER_API_KEY = "YOUR_OPENROUTER_API_KEY"
MODEL_NAME = "openai/gpt-oss-120b"
client = OpenAI(
    base_url="https://openrouter.ai/api/v1",
    api_key=OPENROUTER_API_KEY,
)

running_processes = {}

# =========================================================
# TOOLS
# =========================================================

def execute_command(command, background=False, timeout=10):
    try:
        # PowerShell çıktı kodlamasını UTF-8'e zorla
        ps_command = f"$OutputEncoding = [Console]::OutputEncoding = [System.Text.Encoding]::UTF8; {command}"

        # Browser açma optimizasyonu
        if command.startswith("http://") or command.startswith("https://"):
            os.startfile(command)
            return f"Tarayıcıda açıldı: {command}"

        if background:
            process = subprocess.Popen(
                [
                    "powershell",
                    "-NoProfile",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-Command",
                    ps_command
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                stdin=subprocess.DEVNULL,
                text=True,
                encoding='utf-8',
                errors='replace',
                creationflags=subprocess.CREATE_NO_WINDOW
            )

            running_processes[process.pid] = process

            return f"Komut arka planda başlatıldı. PID: {process.pid}"

        else:
            result = subprocess.run(
                [
                    "powershell",
                    "-NoProfile",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-Command",
                    ps_command
                ],
                capture_output=True,
                text=True,
                encoding='utf-8',
                errors='replace',
                timeout=timeout
            )

            output = (result.stdout or "").strip()

            if result.stderr:
                output += f"\nHATA:\n{result.stderr.strip()}"

            return output if output else "Komut çalıştı fakat çıktı üretmedi."

    except subprocess.TimeoutExpired:
        return "Komut zaman aşımına uğradı."

    except Exception as e:
        return f"Hata oluştu: {str(e)}"


def write_file(filepath, content):
    try:
        directory = os.path.dirname(filepath)

        if directory:
            os.makedirs(directory, exist_ok=True)

        with open(filepath, "w", encoding="utf-8") as f:
            f.write(content)

        return f"Dosya kaydedildi: {filepath}"

    except Exception as e:
        return f"Dosya yazma hatası: {e}"


def read_file(filepath):
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            return f.read()

    except Exception as e:
        return f"Dosya okuma hatası: {e}"


def play_youtube_song(song_name: str):
    try:
        query_string = urllib.parse.urlencode({"search_query": song_name})
        url = f"https://www.youtube.com/results?{query_string}"
        
        # Sayfa kaynağını çek
        headers = {'User-Agent': 'Mozilla/5.0'}
        req = urllib.request.Request(url, headers=headers)
        html_content = urllib.request.urlopen(req).read().decode()
        
        # İlk video ID'sini bul
        video_ids = re.findall(r'"videoId":"([a-zA-Z0-9_-]{11})"', html_content)
        
        if video_ids:
            first_video_url = f"https://www.youtube.com/watch?v={video_ids[0]}"
            webbrowser.open(first_video_url)
            return f"Şarkı başarıyla açıldı: {first_video_url}"
        else:
            return "Şarkı bulunamadı."
    except Exception as e:
        return f"YouTube hatası: {str(e)}"


# =========================================================
# TOOL DEFINITIONS
# =========================================================

tools = [
    {
        "type": "function",
        "function": {
            "name": "execute_command",
            "description": "PowerShell komutu çalıştırır.",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string"
                    },
                    "background": {
                        "type": "boolean"
                    },
                    "timeout": {
                        "type": "integer"
                    }
                },
                "required": ["command"]
            }
        }
    },

    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "Dosya oluşturur veya yazar.",
            "parameters": {
                "type": "object",
                "properties": {
                    "filepath": {
                        "type": "string"
                    },
                    "content": {
                        "type": "string"
                    }
                },
                "required": ["filepath", "content"]
            }
        }
    },

    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Dosya okur.",
            "parameters": {
                "type": "object",
                "properties": {
                    "filepath": {
                        "type": "string"
                    }
                },
                "required": ["filepath"]
            }
        }
    },

    {
        "type": "function",
        "function": {
            "name": "play_youtube_song",
            "description": "YouTube'da belirtilen şarkıyı veya videoyu arar ve ilk sonucu tarayıcıda açar.",
            "parameters": {
                "type": "object",
                "properties": {
                    "song_name": {
                        "type": "string",
                        "description": "Aranacak şarkı veya video adı."
                    }
                },
                "required": ["song_name"]
            }
        }
    }
]


# =========================================================
# MAIN
# =========================================================

def run_orion():

    print(
        f"{Fore.CYAN}=== ORION AI ==={Style.RESET_ALL}\n"
    )

    messages = [
    
    ]

    while True:

        user_input = input(
            f"{Fore.GREEN}Siz: {Style.RESET_ALL}"
        )

        if user_input.lower() in ["exit", "quit"]:
            break

        messages.append({
            "role": "user",
            "content": user_input
        })

        while True:
            # Sağlayıcıya göre özel gövde ayarları (Reasoning/Thinking)
            extra_body = (
                {
                    "reasoning": {"effort": "medium"},
                } 
                if "openrouter.ai" in str(client.base_url) 
                else {"chat_template_kwargs": {"enable_thinking": True}}
            )

            # Geçmiş uzadıkça küçük modellerin sistemi unutmasını engellemek için
            # son kullanıcı mesajının arkasına gizli bir hatırlatıcı ekliyoruz.
            payload_messages = list(messages)
            if payload_messages[-1]["role"] == "user":
                payload_messages[-1] = {
                    "role": "user",
                    "content": payload_messages[-1]["content"]
                }

            api_kwargs = {
                "model": MODEL_NAME,
                "messages": payload_messages,
                "tools": tools,
                "tool_choice": "auto",
                "temperature": 1,
                "extra_body": extra_body,
                "stream": True
            }

            # --- GÖNDERİLEN PAYLOAD'U JSON OLARAK BAS (HATA AYIKLAMA) ---
            print(f"\n{Fore.LIGHTBLACK_EX}>>> GÖNDERİLEN VERİ (PAYLOAD) <<<")
            print(json.dumps(payload_messages, indent=2, ensure_ascii=False))
            print(f">>> --------------------------- <<<{Style.RESET_ALL}\n")

            try:
                full_content = ""
                full_reasoning = ""
                tool_calls_buffer = {}
                
                response = client.chat.completions.create(**api_kwargs)
                
                first_content = True
                first_reasoning = True
                
                for chunk in response:
                    delta = chunk.choices[0].delta
                    
                    # Reasoning Content (Düşünce)
                    reasoning = (
                        getattr(delta, "reasoning_content", None) or 
                        getattr(delta, "reasoning", None) or 
                        (delta.model_extra or {}).get("reasoning_content") or 
                        (delta.model_extra or {}).get("reasoning") or 
                        ""
                    )
                    if reasoning:
                        if first_reasoning:
                            print(f"\n{Fore.YELLOW}{Style.DIM}💭 Düşünce: ", end="", flush=True)
                            first_reasoning = False
                        print(reasoning, end="", flush=True)
                        full_reasoning += reasoning
                    
                    # Content (Normal Cevap)
                    content = delta.content
                    if content:
                        if first_content:
                            if not first_reasoning: print("\n")
                            print(f"{Fore.CYAN}Orion: {Fore.WHITE}", end="", flush=True)
                            first_content = False
                        print(content, end="", flush=True)
                        full_content += content
                        
                    # Tool Calls (Araç Çağrıları)
                    if delta.tool_calls:
                        for tc_delta in delta.tool_calls:
                            idx = tc_delta.index
                            if idx not in tool_calls_buffer:
                                tool_calls_buffer[idx] = {
                                    "id": tc_delta.id,
                                    "name": "",
                                    "arguments": ""
                                }
                            
                            if tc_delta.id:
                                tool_calls_buffer[idx]["id"] = tc_delta.id
                            if tc_delta.function and tc_delta.function.name:
                                tool_calls_buffer[idx]["name"] = tc_delta.function.name
                            if tc_delta.function and tc_delta.function.arguments:
                                tool_calls_buffer[idx]["arguments"] += tc_delta.function.arguments

                print("\n") # Satır sonu

            except Exception as e:
                print(f"{Fore.RED}API Hatası: {e}")
                break

            # =====================================================
            # TOOL EXECUTION
            # =====================================================
            if tool_calls_buffer:
                tool_calls_list = []
                # Sözlüğü listeye çevir ve ID/Name bilgilerini toparla
                for idx in sorted(tool_calls_buffer.keys()):
                    tc = tool_calls_buffer[idx]
                    tool_calls_list.append({
                        "id": tc["id"],
                        "type": "function",
                        "function": {
                            "name": tc["name"],
                            "arguments": tc["arguments"]
                        }
                    })

                messages.append({
                    "role": "assistant",
                    "content": full_content or "",
                    "tool_calls": tool_calls_list
                })

                for tc in tool_calls_list:
                    func_name = tc["function"]["name"]
                    arguments_str = tc["function"]["arguments"]
                    try:
                        args = json.loads(arguments_str)
                    except:
                        args = {}

                    print(f"{Fore.MAGENTA}🛠️ Araç: {func_name}")
                    print(f"{Fore.CYAN}📦 Args: {args}")

                    # TOOL EXECUTION
                    if func_name == "execute_command":
                        result = execute_command(**args)
                    elif func_name == "write_file":
                        result = write_file(**args)
                    elif func_name == "read_file":
                        result = read_file(**args)
                    elif func_name == "play_youtube_song":
                        result = play_youtube_song(**args)
                    else:
                        result = "Bilinmeyen araç."

                    print(
                        f"{Fore.BLUE}"
                        f"\n--- TOOL OUTPUT ---\n"
                        f"{result}\n"
                        f"-------------------\n"
                    )

                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc["id"],
                        "name": func_name,
                        "content": str(result)
                    })
                
                continue # Araç sonuçlarını LLM'e geri gönder

            # Eğer tool_call yoksa (normal cevap bittiyse)
            messages.append({
                "role": "assistant",
                "content": full_content or ""
            })
            
            break 


if __name__ == "__main__":
    run_orion()