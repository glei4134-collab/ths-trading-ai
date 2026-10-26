FROM python:3.10-slim

WORKDIR /app

# 安装依赖
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 复制代码
COPY . .

# 数据目录
RUN mkdir -p /app/data

# 暴露端口
EXPOSE 5000

# 启动
CMD ["python", "api.py"]
