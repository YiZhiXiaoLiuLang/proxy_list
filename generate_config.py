import json
import time
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed

SOURCE_URL = "https://cdn.jsdelivr.net/gh/proxifly/free-proxy-list@main/proxies/all/data.json"
OUTPUT_FILE = "xray-config.json"

# 限制导入的最大可用节点数量
MAX_PROXIES = 50
# 验活并发线程数与单节点探测超时（秒）
CHECK_WORKERS = 30
CHECK_TIMEOUT = 6

# 校验目标：Google 官方根路径（真实访问且证书合法时必返回 404）
PROBE_URL = "https://www.gstatic.com/"
PROBE_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}

def fetch_proxies():
    resp = requests.get(SOURCE_URL, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    return data if isinstance(data, list) else []

def check_single_proxy(item):
    """
    测试节点可用性：
    1. 走 HTTPS 握手（自动过滤无法打通 TLS CONNECT 隧道的假节点）
    2. 校验 SSL 证书（未关闭 verify，自动剔除拦截篡改/中间人伪造节点）
    3. 严格匹配 Google gstatic 根路径返回的 404 状态码
    """
    protocol = item.get("protocol")
    ip = item.get("ip")
    port = item.get("port")

    if protocol not in ("http", "socks4", "socks5"):
        return None

    proxy_url = f"{protocol}://{ip}:{port}"
    proxies = {
        "http": proxy_url,
        "https": proxy_url
    }

    start_time = time.time()
    try:
        r = requests.get(
            PROBE_URL,
            proxies=proxies,
            headers=PROBE_HEADERS,
            timeout=CHECK_TIMEOUT,
            verify=True  # 严格校验证书合法性
        )
        if r.status_code == 404:
            latency = round((time.time() - start_time) * 1000)
            item["latency"] = latency
            return item
    except Exception:
        pass
    return None

def filter_alive_proxies(proxies, max_count=MAX_PROXIES):
    """并发检测节点真实性，并按延迟升序截取最优节点"""
    print(f"正在验活 {len(proxies)} 个节点（目标: {PROBE_URL} -> 404，超时: {CHECK_TIMEOUT}s）...")
    alive = []

    with ThreadPoolExecutor(max_workers=CHECK_WORKERS) as executor:
        futures = [executor.submit(check_single_proxy, item) for item in proxies]
        for future in as_completed(futures):
            res = future.result()
            if res:
                alive.append(res)
                print(f"[ALIVE] {res['protocol']}://{res['ip']}:{res['port']} - {res['latency']}ms")

    alive.sort(key=lambda x: x["latency"])
    selected = alive[:max_count]
    print(f"验活完成：真实可用节点 {len(alive)} 个，提取前 {len(selected)} 个。")
    return selected

def build_xray_config(proxies):
    inbounds = [
        {
            "port": 1080,
            "protocol": "socks",
            "tag": "socks-in",
            "settings": {"udp": True}
        },
        {
            "port": 1081,
            "protocol": "http",
            "tag": "http-in"
        }
    ]

    outbounds = []
    for idx, item in enumerate(proxies):
        protocol = item.get("protocol")
        ip = item.get("ip")
        port = item.get("port")

        if protocol == "http":
            outbound = {
                "tag": f"proxy-{idx}",
                "protocol": "http",
                "settings": {
                    "servers": [{"address": ip, "port": port}]
                }
            }
        elif protocol in ("socks5", "socks4"):
            version = 4 if protocol == "socks4" else 5
            outbound = {
                "tag": f"proxy-{idx}",
                "protocol": "socks",
                "settings": {
                    "servers": [
                        {
                            "address": ip,
                            "port": port,
                            "users": []
                        }
                    ],
                    "version": version
                },
                "streamSettings": {
                    "sockopt": {
                        "tcpFastOpen": False
                    }
                }
            }
        else:
            continue

        outbounds.append(outbound)

    # 兜底直连
    outbounds.append({
        "tag": "direct",
        "protocol": "freedom"
    })

    observatory = {
        "subjectSelector": ["proxy-"],
        "probeUrl": "https://www.google.com/generate_204",
        "probeInterval": "3m",
        "enableConcurrency": True
    }

    balancers = [
        {
            "tag": "proxy-balancer",
            "selector": ["proxy-"],
            "strategy": {
                "type": "leastPing"
            },
            "fallbackTag": "direct"
        }
    ]

    routing = {
        "rules": [
            {
                "inboundTag": ["socks-in", "http-in"],
                "balancerTag": "proxy-balancer"
            }
        ],
        "balancers": balancers
    }

    # 包含顶层 log 配置
    return {
        "log": {
            "loglevel": "warning"
        },
        "inbounds": inbounds,
        "outbounds": outbounds,
        "observatory": observatory,
        "routing": routing
    }

def main():
    try:
        raw_proxies = fetch_proxies()
        if not raw_proxies:
            print("No proxies fetched, exiting.")
            return

        alive_proxies = filter_alive_proxies(raw_proxies, MAX_PROXIES)
        if not alive_proxies:
            print("No valid alive proxies found, exiting.")
            return

        config = build_xray_config(alive_proxies)
        with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
            json.dump(config, f, indent=2)

        print(f"Success: Config written to {OUTPUT_FILE} with {len(alive_proxies)} verified proxies.")
    except Exception as e:
        print(f"Error: {e}")
        exit(1)

if __name__ == "__main__":
    main()
