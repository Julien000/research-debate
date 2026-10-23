import os

bind = os.environ.get("RESEARCH_DEBATE_BIND", "127.0.0.1:8080")
workers = 1
worker_class = "gthread"
threads = 4
timeout = 60
accesslog = "-"
errorlog = "-"
