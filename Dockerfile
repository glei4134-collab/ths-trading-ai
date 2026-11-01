FROM python:3.10-slim

WORKDIR /app

# 创建非 root 用户
RUN groupadd -r app && useradd -r -g app -d /app -s /bin/bash app

# 安装依赖（优先使用 lock 文件保证确定性构建）
COPY requirements.txt requirements.lock* .
RUN if [ -f requirements.lock ]; then \
        pip install --no-cache-dir -r requirements.lock; \
    else \
        pip install --no-cache-dir -r requirements.txt; \
    fi

# 复制代码（以 app 用户所有）
COPY --chown=app:app . .

# 数据目录
RUN mkdir -p /app/data && chown -R app:app /app

# 暴露端口
EXPOSE 5000

# 确保 gunicorn 在 PATH 中
ENV PATH="/usr/local/bin:${PATH}"

# 非 root 运行
USER app

# 启动生产级 WSGI（与生产入口一致）
CMD ["gunicorn", "-c", "deploy/gunicorn_conf.py", "api:app"]
