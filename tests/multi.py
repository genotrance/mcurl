import mcurl

mcurl.dprint = print

m = mcurl.MCurl()

c1 = mcurl.Curl('http://httpbin.org/get')
c1.set_debug()
c1.buffer()
m.add(c1)

data = "test8192".encode("utf-8")
c2 = mcurl.Curl('https://httpbin.org/post', 'POST')
c2.set_debug()
c2.buffer(data=data)
c2.set_headers({"Content-Length": len(data)})
m.add(c2)

ret1 = m.do(c1)
ret2 = m.do(c2)

if ret1:
    c1.get_response()
    c1.get_headers()
    c1.get_data()
    print(f"Response: {c1.get_response()}\n\n" +
          f"{c1.get_headers()}{c1.get_data()}")
else:
    print(f"Failed with error: {c1.errstr}")

if ret2:
    c2.get_response()
    c2.get_headers()
    c2.get_data()
    print(f"Response: {c2.get_response()}\n\n" +
          f"{c2.get_headers()}{c2.get_data()}")
else:
    print(f"Failed with error: {c2.errstr}")

m.close()
