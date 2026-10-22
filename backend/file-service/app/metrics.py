from prometheus_client import Counter

FILES = Counter("files_total", "Fayl amallari", ["purpose", "result"])
