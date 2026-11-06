ARG PYTHON_IMAGE=python:3.12-slim
FROM ${PYTHON_IMAGE}
RUN pip install --no-cache-dir "psycopg[binary]==3.2.3"
COPY writer.py console.py /app/
USER 65534
ENV PYTHONUNBUFFERED=1
CMD ["python", "/app/writer.py"]
