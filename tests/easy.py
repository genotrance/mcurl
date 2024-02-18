from mcurl import Curl

c = Curl('http://httpbin.org/get')
c.buffer()
ret = c.perform()
if ret == 0:
    ret, resp = c.get_response()
    headers = c.get_headers()
    data = c.get_data()
    print(f"Response: {resp}\n\n{headers}{data}")