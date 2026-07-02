# Orion AI Assistant

Orion AI Assistant is a unified, cross-platform ecosystem designed to orchestrate local and cloud-based AI models seamlessly. It provides an intuitive installer and a modular architecture to handle LLMs, Embeddings, TTS (Text-To-Speech), and Vision models through a single, easy-to-use interface.

## 🚀 Features
- **Cross-Platform Support**: Run on Windows, macOS, or Linux.
- **Dual Installation Modes**: Choose between a lightweight, native (Local) setup or an isolated (Docker) deployment.
- **Automated Dependency Management**: Automatically provisions PostgreSQL and Redis via portable binaries (Windows) or native package managers (Homebrew/APT).
- **Interactive UI Installer**: A slick web-based installation wizard to configure models, API keys, and hardware acceleration directly from your browser.
- **Orion Router Integration**: Dynamically routes requests across multiple providers (OpenAI, Gemini, Local Llama.cpp) with load balancing and intelligent failover.

## 📦 Architecture
- **Orion Hub (API & Worker)**: The central brain managing tasks, configuration, and background processing.
- **Orion Router**: The high-performance API gateway handling rate limits, model fallback, and request proxying.
- **Services Ecosystem**: Modular integrations for external AI models, including `llama-cpp`, `orion-tts`, and more.

## ⚙️ Getting Started

### Prerequisites
- **Git**
- **Python 3.10+** (For Local Mode)
- **Docker & Docker Compose** (For Docker Mode)

### Installation

1. **Clone the repository:**
   ```bash
   git clone https://github.com/orion-ai-assistant/orion-ai-assistant.git
   cd orion-ai-assistant
   ```

2. **Launch the Installer:**
   You can start the visual web installer for your preferred environment:
   
   *For Local (Native) Installation:*
   ```bash
   python orion.py installer local
   ```
   
   *For Docker Installation:*
   ```bash
   python orion.py installer docker
   ```
   
   *The installer will automatically open in your default web browser.*

### CLI Commands
Orion provides a unified CLI tool (`orion.py`) to manage your environment:

- `python orion.py setup [local|docker]` : Prepares the databases, virtual environments, and fetches the Router without launching the GUI.
- `python orion.py start [local|docker]` : Starts all configured services.
- `python orion.py stop [local|docker]` : Stops all background processes and containers.

## 📚 Documentation
For more detailed documentation, API references, and architecture overviews, please refer to our internal wiki or the `docs/` folder (coming soon).

## 🤝 Contributing
Contributions are welcome! Feel free to open issues or submit pull requests.

## 📄 License
MIT License