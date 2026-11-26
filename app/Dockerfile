FROM python:3.12-slim

WORKDIR /srv

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . ./app

EXPOSE 8000
# 默认跑探测 API；模拟时源容器用 command 覆盖为 python -m app.mock_server
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
