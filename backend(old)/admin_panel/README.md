# Orion AI Admin Panel

Modern, modular admin panel for managing AI configuration and monitoring active users.

## 🎯 Features

- **Dashboard**: System overview with real-time statistics
- **AI Configuration**: Control thinking mode, debug settings, models, and more
- **User Monitoring**: View active users and their connected devices
- **Model Management**: Enable/disable models and set defaults
- **Modern UI**: Clean, dark-themed responsive interface

## 📁 Project Structure

```
admin_panel/
├── app/
│   ├── main.py                 # FastAPI application entry point
│   ├── settings.py             # Configuration and environment variables
│   ├── requirements.txt        # Python dependencies
│   │
│   ├── models/                 # Data models (Pydantic)
│   │   ├── __init__.py
│   │   ├── config_model.py     # AI configuration models
│   │   └── user_activity.py    # User activity models
│   │
│   ├── services/               # Business logic layer
│   │   ├── __init__.py
│   │   ├── config_service.py   # Configuration management (CRUD)
│   │   ├── user_service.py     # User activity tracking
│   │   └── stats_service.py    # Statistics and monitoring
│   │
│   ├── routes/                 # API endpoints
│   │   ├── __init__.py
│   │   ├── config_routes.py    # /api/config/* endpoints
│   │   ├── user_routes.py      # /api/users/* endpoints
│   │   └── stats_routes.py     # /api/stats/* endpoints
│   │
│   ├── static/                 # Frontend assets
│   │   ├── dashboard.html      # Main admin interface
│   │   ├── styles.css          # Styling
│   │   └── app.js              # Frontend logic
│   │
│   └── data/                   # Runtime data (auto-created)
│       └── ai_config.json      # Persisted configuration
```

## 🚀 Getting Started

### Installation

1. **Navigate to admin_panel directory**:
   ```bash
   cd backend/admin_panel
   ```

2. **Install dependencies**:
   ```bash
   pip install -r app/requirements.txt
   ```

3. **Set environment variables** (optional):
   ```bash
   # Windows PowerShell
   $env:ADMIN_HOST = "0.0.0.0"
   $env:ADMIN_PORT = "3000"
   $env:API_BACKEND_URL = "http://localhost:8000"
   ```

### Running the Admin Panel

```bash
# From admin_panel directory
python -m app.main

# Or using uvicorn directly
uvicorn app.main:app --host 127.0.0.1 --port 3000 --reload
```

The admin panel will be available at:
- **Dashboard**: http://localhost:3000
- **API Docs**: http://localhost:3000/docs
- **Health Check**: http://localhost:3000/api/health

## 🔧 Configuration

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `ADMIN_HOST` | `127.0.0.1` | Host to bind the server |
| `ADMIN_PORT` | `3000` | Port to run the server |
| `ADMIN_TOKEN` | `dev-admin-token-change-in-production` | Admin authentication token |
| `ALLOWED_ORIGINS` | `*` | CORS allowed origins |
| `API_BACKEND_URL` | `http://localhost:8000` | Main API backend URL |

### Default Configuration

On first run, a default configuration is created with:
- Thinking mode: **Enabled**
- Debug mode: **Disabled**
- Stream responses: **Enabled**
- Models: GPT-4-turbo (default), GPT-3.5-turbo, Claude-3-sonnet
- Max conversation history: 20 messages
- Rate limit: 100 requests/user/hour

## 📡 API Endpoints

### Configuration
- `GET /api/config/` - Get current configuration
- `PUT /api/config/` - Update configuration (partial)
- `POST /api/config/reset` - Reset to defaults
- `POST /api/config/thinking/toggle` - Toggle thinking mode
- `POST /api/config/model/{model_name}/toggle` - Enable/disable model
- `POST /api/config/model/{model_name}/default` - Set default model

### Users
- `GET /api/users/` - List active users
- `GET /api/users/{user_id}` - Get user details
- `GET /api/users/{user_id}/devices` - Get user's devices

### Statistics
- `GET /api/stats/` - System statistics
- `GET /api/stats/health` - Health check

## 🏗️ Architecture Principles

### Open/Closed Principle
- **Open for extension**: Add new routes, services, or models without modifying existing code
- **Closed for modification**: Core functionality is stable and encapsulated

### Separation of Concerns
- **Models**: Data structures and validation
- **Services**: Business logic and data operations
- **Routes**: API endpoints and request handling
- **Static**: Frontend presentation layer

### Benefits
- ✅ Easy to test each layer independently
- ✅ Simple to add new features
- ✅ Clear responsibilities
- ✅ No circular dependencies
- ✅ Maintainable and scalable

## 🎨 Frontend Features

- **Real-time Updates**: Auto-refresh statistics every 10 seconds
- **Responsive Design**: Works on desktop and mobile
- **Toast Notifications**: User-friendly success/error messages
- **Tab Navigation**: Easy switching between sections
- **Toggle Switches**: Quick enable/disable controls

## 🔒 Security Notes

⚠️ **Important**: This is a development setup. For production:
- Change `ADMIN_TOKEN` to a secure value
- Implement proper authentication middleware
- Restrict `ALLOWED_ORIGINS` to specific domains
- Use HTTPS
- Add rate limiting
- Implement role-based access control

## 📝 Adding New Features

### Adding a New API Endpoint

1. **Create model** (if needed) in `models/`
2. **Create service** in `services/`
3. **Create route** in `routes/`
4. **Register route** in `main.py`

Example:
```python
# routes/new_feature_routes.py
from fastapi import APIRouter
router = APIRouter(prefix="/api/new-feature", tags=["New Feature"])

@router.get("/")
async def get_new_feature():
    return {"status": "ok"}

# main.py
from routes import new_feature_router
app.include_router(new_feature_router)
```

### Adding a New Frontend Tab

1. Add navigation button in `dashboard.html`
2. Add tab content section
3. Add loading function in `app.js`
4. Add navigation handler

## 🧪 Testing

```bash
# Manual testing
curl http://localhost:3000/api/health

# Get configuration
curl http://localhost:3000/api/config/

# Toggle thinking mode
curl -X POST "http://localhost:3000/api/config/thinking/toggle?enabled=true&admin_name=admin"
```

## 📊 Monitoring

The admin panel monitors:
- Active WebSocket connections (from main API)
- User sessions and devices
- Agent health status
- System configuration state

## 🤝 Integration with Main API

The admin panel connects to the main API backend to fetch:
- Active user statistics
- WebSocket connection counts
- Agent readiness status

Make sure the main API is running at the URL specified in `API_BACKEND_URL`.

## 📚 Dependencies

- **FastAPI**: Modern web framework
- **Pydantic**: Data validation
- **httpx**: Async HTTP client
- **uvicorn**: ASGI server

## 🐛 Troubleshooting

### Port Already in Use
```bash
# Change port in environment variable or settings.py
$env:ADMIN_PORT = "3001"
```

### Cannot Connect to Backend
- Verify main API is running
- Check `API_BACKEND_URL` setting
- Verify CORS settings on main API

### Static Files Not Loading
- Ensure `static/` directory exists
- Check file paths in `settings.py`
- Restart the server

## 📄 License

Part of the Orion AI Assistant project.
