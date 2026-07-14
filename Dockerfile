# Stage 1: Build the React frontend
FROM node:18-alpine AS frontend-builder
WORKDIR /app/frontend
COPY frontend/package*.json ./
RUN npm install
COPY frontend/ ./
RUN npm run build

# Stage 2: Build the FastAPI backend
FROM python:3.12
WORKDIR /app

# Copiar requerimientos e instalar dependencias de Python
COPY requirements.txt requirements-extractor.txt ./
RUN pip install --no-cache-dir -r requirements.txt
RUN pip install --no-cache-dir -r requirements-extractor.txt

# Copiar código y datos del backend
COPY app/ ./app
COPY data/ ./data
COPY extractor/ ./extractor

# Copiar el frontend compilado desde la etapa 1
COPY --from=frontend-builder /app/frontend/dist ./frontend/dist

# Asegurar que existan los directorios y dar permisos de escritura completos
# para el usuario no-root de Hugging Face (UID 1000)
RUN mkdir -p data/reportes data/soc cache/tifs && chmod -R 777 /app

# Puerto reservado de Hugging Face Spaces
EXPOSE 7860

# Iniciar la aplicación
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "7860"]
