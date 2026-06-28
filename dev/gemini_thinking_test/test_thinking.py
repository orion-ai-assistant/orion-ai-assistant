import os
import sys
import json
import asyncio
import inspect
from google import genai
from google.genai import types

# Load API key
api_key = "AIzaSyAuocOUL7x8a7fUsTAVq1DpHHC8sGXYuLA"
os.environ["GEMINI_API_KEY"] = api_key
if not api_key:
    print("WARNING: GEMINI_API_KEY is empty.")

# Redirect all print outputs to both terminal and a file
class Logger(object):
    def __init__(self, filename="test_results.txt"):
        self.terminal = sys.stdout
        self.log = open(filename, "w", encoding="utf-8")

    def write(self, message):
        self.terminal.write(message)
        self.log.write(message)
        self.log.flush()

    def flush(self):
        self.terminal.flush()
        self.log.flush()

sys.stdout = Logger()

def print_sdk_info():
    print("=" * 60)
    print("GOOGLE-GENAI SDK DIAGNOSTICS")
    print("=" * 60)
    try:
        print(f"SDK Module Path: {genai.__file__}")
    except Exception as e:
        print(f"Could not get file path: {e}")
        
    # Inspect ThinkingConfig
    try:
        sig = inspect.signature(types.ThinkingConfig.__init__)
        print(f"types.ThinkingConfig constructor signature:\n  {sig}")
    except Exception as e:
        print(f"Error inspecting types.ThinkingConfig: {e}")

    try:
        sig_gen = inspect.signature(types.GenerateContentConfig.__init__)
        params = list(sig_gen.parameters.keys())
        print(f"types.GenerateContentConfig parameters containing 'think':")
        think_params = [p for p in params if 'think' in p.lower()]
        print(f"  {think_params}")
    except Exception as e:
        print(f"Error inspecting types.GenerateContentConfig: {e}")
    print("=" * 60 + "\n")

async def test_thinking(model_name: str, config_name: str, config: types.GenerateContentConfig, prompt: str):
    print("=" * 60)
    print(f"TESTING MODEL: {model_name}")
    print(f"CONFIGURATION: {config_name}")
    print(f"PROMPT: {prompt}")
    print("=" * 60)
    
    if not os.getenv("GEMINI_API_KEY"):
        print("Skipping test: GEMINI_API_KEY is empty.")
        return
        
    try:
        client = genai.Client()
    except Exception as e:
        print(f"Error initializing genai.Client: {e}")
        return

    try:
        config_dict = config.model_dump()
        print(f"Request Configuration:\n{json.dumps(config_dict, indent=2)}")
    except Exception:
        try:
            print(f"Request Configuration: {config}")
        except Exception as e:
            print(f"Error converting config to string: {e}")

    print("\n--- Starting Stream ---")
    chunk_count = 0
    try:
        response_stream = await client.aio.models.generate_content_stream(
            model=model_name,
            contents=prompt,
            config=config
        )
        
        async for chunk in response_stream:
            chunk_count += 1
            print(f"\n[Chunk #{chunk_count}]")
            
            # Print raw chunk representation
            try:
                chunk_json = chunk.model_dump_json(indent=2)
                print("RAW JSON:")
                print(chunk_json)
            except Exception as e:
                try:
                    chunk_dict = chunk.model_dump()
                    print("RAW DICT:")
                    print(json.dumps(chunk_dict, indent=2, default=str))
                except Exception as e2:
                    print(f"Could not print raw JSON: {e}, {e2}")
                    print(str(chunk))
            
            # Extract and print content vs thought
            if getattr(chunk, 'candidates', None):
                candidate = chunk.candidates[0]
                if getattr(candidate, 'content', None) and getattr(candidate.content, 'parts', None):
                    for idx, part in enumerate(candidate.content.parts):
                        is_thought = getattr(part, 'thought', False)
                        text = getattr(part, 'text', '')
                        
                        print(f"  -> Part #{idx + 1}:")
                        print(f"     * thought: {is_thought}")
                        print(f"     * text: {repr(text)}")

    except Exception as e:
        print(f"\nAPI Error during execution: {e}")
    print("\n--- End Stream ---")
    print(f"Total Chunks Received: {chunk_count}")
    print("=" * 60 + "\n")

async def main():
    print_sdk_info()
    
    prompt = "En sevdiğin renk ne?"
    
    # Test Config 1: Default config
    config_default = types.GenerateContentConfig()
    
    # Test Config 2: ThinkingConfig with include_thoughts=True and budget
    try:
        config_thinking_on = types.GenerateContentConfig(
            thinking_config=types.ThinkingConfig(
                include_thoughts=True,
                thinking_budget=1024
            )
        )
    except Exception as e:
        print(f"Trying fallback for thinking config ON: {e}")
        try:
            config_thinking_on = types.GenerateContentConfig(
                thinking_config=types.ThinkingConfig(include_thoughts=True)
            )
        except Exception as e2:
            print(f"Failed fallback: {e2}")
            config_thinking_on = types.GenerateContentConfig()
            
    # Test Config 3: ThinkingConfig with thinking_budget = 0
    try:
        config_thinking_off = types.GenerateContentConfig(
            thinking_config=types.ThinkingConfig(thinking_budget=0)
        )
    except Exception as e:
        print(f"Error creating thinking config OFF: {e}")
        config_thinking_off = None
    
    models_to_test = [
        "gemini-2.5-flash-lite",
    ]
    
    if len(sys.argv) > 1:
        models_to_test = [sys.argv[1]]
        print(f"Overriding model list with user provided model: {models_to_test[0]}")

    for model in models_to_test:
        # 1. Test with default config
        await test_thinking(model, "Default (No Thinking Config)", config_default, prompt)
        
        # 2. Test with thinking config ON
        await test_thinking(model, "Thinking Config ON (include_thoughts=True, budget=1024)", config_thinking_on, prompt)
        
        # 3. Test with thinking config OFF
        if config_thinking_off:
            await test_thinking(model, "Thinking Config OFF (thinking_budget=0)", config_thinking_off, prompt)

if __name__ == "__main__":
    asyncio.run(main())
