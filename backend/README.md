# RepoMentor Backend

## Running the Application

### 1. Configure Environment
Copy the example environment file:
```bash
cp .env.example .env
```

### 2. Start the Server
Run Uvicorn with auto-reload:
```bash
uvicorn app.main:app --reload --port 8000
```

### 3. Check Health
Verify the service is running:
- Browser: http://localhost:8000/health
- curl:
  ```bash
  curl http://localhost:8000/health
  ```
Expected response:
```json
{"status":"ok"}
```

### 4. Run Tests
```bash
pytest
```
