FROM alpine:3.20
RUN apk add --no-cache docker-cli docker-cli-compose python3 py3-psycopg2
COPY deploy_watch.py /deploy_watch.py
CMD ["python3", "/deploy_watch.py"]
