import sys
import time

import mcurl

def dprint(msg):
    "Print message to stdout and debug file if open"
    offset = 0
    tree = ""
    while True:
        try:
            name = sys._getframe(offset).f_code.co_name
            offset += 1
            if name != "print":
                tree = "/" + name + tree
            if offset > 3:
                break
        except ValueError:
            break
    sys.stdout.write(str(int(time.time())) + ": " + tree + ": " + msg + "\n")

def query(url, method="GET", data = None, quit=True, check=False, insecure=False, multi=None, encoding="utf-8"):
    ec = mcurl.Curl(url, method)
    if url.startswith("https"):
        ec.set_insecure(insecure)
    if data is not None:
        ec.buffer(data.encode("utf-8"))
        ec.set_headers({"Content-Length": len(data)})
    else:
        ec.buffer()
    ec.set_useragent("mcurl tester")
    if multi is None:
        print(f"\nTesting {method} {url}")
        ret = ec.perform()
    else:
        print(f"\nTesting {method} {url} multi")
        ret = 0 if multi.do(ec) else 1
    if ret != 0:
        print(f"Failed with error {ret}\n{ec.errstr}")
        sys.exit(1)
    else:
        ret_data = ec.get_data(encoding=encoding)
        print(f"\n{ec.get_headers()}Response length: {len(ret_data)}")
        if check:
            # Tests against httpbin
            if url not in ret_data:
                print(f"Failed: response does not contain {url}:\n{ret_data}")
                sys.exit(2)
            if data is not None and data not in ret_data:
                print(f"Failed: response does not match {data}:\n{ret_data}")
                sys.exit(3)

    if quit:
        sys.exit()

def queryall(testurl):
    import uuid

    multi = mcurl.MCurl()

    insecure = False
    if testurl == "all":
        url = "://httpbin.org/"
    elif testurl.startswith("all:"):
        url = f"://{testurl[4:]}/"
        insecure = True
    else:
        query(testurl, quit=True)

    # HTTP verb tests
    for method in ["GET", "POST", "PUT", "DELETE", "PATCH"]:
        for protocol in ["http", "https"]:
            testurl = protocol + url + method.lower()
            data = str(uuid.uuid4()) if method in ["POST", "PUT", "PATCH"] else None
            query(testurl, method, data, quit=False, check=True, insecure=insecure)
            query(testurl, method, data, quit=False, check=True, insecure=insecure, multi=multi)

    # Binary download tests
    for protocol in ["http", "https"]:
        testurl = protocol + url + "image/jpeg"
        query(testurl, quit=False, insecure=insecure, encoding=None)
        query(testurl, quit=False, insecure=insecure, multi=multi, encoding=None)

    sys.exit()

def check_deps():
    from _libcurl_cffi import lib as libcurl

    features = [
        "CURL_VERSION_SSL",
        "CURL_VERSION_SPNEGO",
        "CURL_VERSION_KERBEROS5",
        "CURL_VERSION_NTLM"
    ]

    if sys.platform == "win32":
        features.append("CURL_VERSION_SSPI")
    else:
        features.append("CURL_VERSION_GSSAPI")

    vinfo = libcurl.curl_version_info(libcurl.CURLVERSION_LAST-1)
    for feature in features:
        bit = getattr(libcurl, feature)
        avail = True if (bit & vinfo.features) > 0 else False
        if not avail:
            print(f"Error: {feature} not available in libcurl")
            sys.exit(1)

    print("All dependencies available")

if __name__ == "__main__":
    mcurl.dprint = dprint
    check_deps()
    if len(sys.argv) == 1:
        queryall("all")
    else:
        queryall(sys.argv[1])