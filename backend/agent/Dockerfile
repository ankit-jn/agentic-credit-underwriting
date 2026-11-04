# AgentCore Runtime requires linux/arm64 images
FROM --platform=linux/arm64 python:3.12-slim
WORKDIR /app
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app
RUN useradd -m agent && chown -R agent /app
USER agent
EXPOSE 8080
CMD ["opentelemetry-instrument", "python", "-m", "app.main"]
