# Data Storage Architecture

This document describes the responsibilities and schemas of the data storage layers used by the Orion Hub service. Orion Hub utilizes both **PostgreSQL** (for persistent relational data) and **Redis** (for fast, transient in-memory operations and task queuing).

---

## 1. PostgreSQL (Persistent Relational Storage)

PostgreSQL is the source of truth for critical, persistent data that must survive system restarts, container updates, or hardware failures.

### Tables

#### `orion_users`
Stores registered dashboard users and their authentication credentials.
- `id` (text, Primary Key): The username of the user.
- `password_hash` (text): Secure hash of the user's password.
- `created_at` (timestamptz): Timestamp of account creation.
- `is_active` (boolean): Active state of the user account.

#### `orion_settings`
Stores persistent configuration overrides for the system and individual users.
- `user_id` (text, Primary Key part): The user to whom the setting applies (or `global` for system-wide defaults).
- `key` (text, Primary Key part): The configuration setting key name (e.g., model overrides, temperatures).
- `value` (text): The value of the configuration setting.
- `updated_at` (timestamptz): Timestamp of the last update.

---

## 2. Redis (In-Memory, Cache & Queue)

Redis is utilized for high-performance, real-time messaging, active job tracking, and caching. The data stored in Redis is designed to be transient or easily reconstructible, although a Docker volume is used to persist snapshots to disk.

### Key Patterns & Data Types

#### Chat History
- **Key Pattern:** `chat:history:<chat_id>`
- **Type:** List
- **Description:** A list of JSON-serialized messages within a specific chat room. Used to feed context back to the LLM during interactions.

#### Chat Metadata
- **Key Pattern:** `chat:meta:<chat_id>`
- **Type:** Hash
- **Description:** Metadata about a specific chat, such as the owner's `user_id`, creation timestamp, and update timestamp.

#### Active Job States
- **Key Pattern:** `chat:state:<chat_id>`
- **Type:** Hash
- **Description:** Real-time state of the job associated with the chat. Contains fields like `status` (queued, thinking, streaming, completed, failed), `partial_thinking` (accumulated thinking tokens), `partial_text` (accumulated output text), and final results or errors. Typically set with a TTL.

#### User Chat Indexes
- **Key Pattern:** `chat:user:index:<user_id>`
- **Type:** Sorted Set (ZSET)
- **Description:** Maintains an ordered list of `chat_id`s belonging to a user, sorted by the last update timestamp. Used to render the chat list sidebar.

#### Task Queue
- **Key Pattern:** `jobs` (Stream Name)
- **Type:** Stream (with Consumer Groups)
- **Description:** A message broker stream where incoming requests are queued. The Hub worker processes read from this stream to execute background LLM inference tasks.

#### Stop Signals
- **Key Pattern:** `chat:stop:<chat_id>`
- **Type:** String (Value: `"1"`)
- **Description:** A short-lived key created when a user requests to cancel/stop an ongoing text generation. The worker checks for this key to interrupt execution.

#### Settings Cache
- **Key Pattern:** `settings:user:<user_id>`
- **Type:** Hash
- **Description:** Caches the merged runtime configuration (global settings + user overrides) from PostgreSQL to avoid hitting the database on every token generation request. Set with a TTL.

#### Real-time Events (Pub/Sub)
- **Key Pattern:** `room:user:<user_id>`
- **Type:** Channel
- **Description:** Used to broadcast token generation streams and system notifications in real-time to the active user's WebSocket connection.
