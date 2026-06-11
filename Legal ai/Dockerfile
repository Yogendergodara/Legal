FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY mcp/ mcp/
COPY db/ db/
COPY scripts/ scripts/
COPY crawler/ crawler/

EXPOSE 8001

CMD ["uvicorn", "mcp.retrieval_server.main:app", "--host", "0.0.0.0", "--port", "8001"]
