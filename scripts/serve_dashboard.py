import http.server, socketserver, os, signal
signal.signal(signal.SIGHUP, signal.SIG_IGN)
os.chdir('/root/atlas/dashboard/templates')
h = http.server.SimpleHTTPRequestHandler
s = socketserver.TCPServer(('127.0.0.1', 8899), h)
print(f'Atlas dashboard serving on :8899 pid={os.getpid()}', flush=True)
s.serve_forever()
