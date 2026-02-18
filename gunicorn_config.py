"""
Gunicorn 設定檔
標記 worker process，讓 _startup 知道自己在 worker 裡
"""
import os


def post_fork(server, worker):
    """每個 worker process fork 後設定標記"""
    os.environ["GUNICORN_WORKER"] = "1"
