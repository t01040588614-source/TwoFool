import re
import urllib.request

for path in ("/", "/dashboard"):
    html = urllib.request.urlopen(f"https://scmaglev.onrender.com{path}", timeout=45).read()
    title = re.search(rb"<title>(.*?)</title>", html, re.I).group(1)
    print(path, title.decode("utf-8"))
