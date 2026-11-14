FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /app
COPY requirements.txt .
RUN python -m pip install --no-cache-dir -r requirements.txt && \
    groupadd -r foodbank && useradd -r -g foodbank -d /app foodbank
COPY . .
# The data folder must be writable by the non-root user. This runs after COPY,
# because COPY would otherwise recreate /app/data owned by root.
RUN mkdir -p /app/data && chown -R foodbank:foodbank /app/data
USER foodbank
EXPOSE 5000
CMD ["python", "server.py", "--host", "0.0.0.0", "--port", "5000"]
