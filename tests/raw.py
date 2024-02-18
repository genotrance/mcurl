from _libcurl_cffi import lib as libcurl
from _libcurl_cffi import ffi

url = "http://httpbin.org/get"
curl = ffi.new("char []", url.encode("utf-8"))

easy = libcurl.curl_easy_init()
libcurl.curl_easy_setopt(easy, libcurl.CURLOPT_URL, curl)
cerr = libcurl.curl_easy_perform(easy)