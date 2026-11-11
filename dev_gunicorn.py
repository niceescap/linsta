"""Explicit local-only config, not production Gunicorn discovery."""
bind = '127.0.0.1:17010'
workers = 1
threads = 1
worker_class = 'sync'
daemon = False
reload = False
preload_app = False
accesslog = None
errorlog = '-'
timeout = 30
graceful_timeout = 15
worker_tmp_dir = '/home/jean/digger-dev/instance/dev'

# Keep Gunicorn from creating a shared ~/.gunicorn control endpoint.
control_socket_disable = True
