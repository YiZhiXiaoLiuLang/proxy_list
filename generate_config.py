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
CHECK_TIMEOUT = 5
# 探测目标（204 状态码，开销最小）
PROBE_URL = "http://cp.cloudflare.com/generate_204"

def fetch_proxies():
    resp = requests.get(SOURCE_URL, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    return data if isinstance(data, list) else []

def check_single_proxy(item):
    """
    测试单个节点的可用性与延迟。
    注意：测试 SOCKS 节点需要安装支持库：pip install "requests[socks]"
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
        r = requests.get(PROBE_URL, proxies=proxies, timeout=CHECK_TIMEOUT)
        if r.status_code in (200, 204):
            latency = round((time.time() - start_time) * 1000)
            item["latency"] = latency
            return item
    except Exception:
        pass
    return None

def filter_alive_proxies(proxies, max_count=MAX_PROXIES):
    """并发检测所有节点，并按延迟升序截取最优节点"""
    print(f"正在验活 {len(proxies)} 个候选节点（超时: {CHECK_TIMEOUT}s，并发: {CHECK_WORKERS}）...")
    alive = []

    with ThreadPoolExecutor(max_workers=CHECK_WORKERS) as executor:
        futures = [executor.submit(check_single_proxy, item) for item in proxies]
        for future in as_completed(futures):
            res = future.result()
            if res:
                alive.append(res)
                print(f"[ALIVE] {res['protocol']}://{res['ip']}:{res['port']} - {res['latency']}ms")

    # 按探测延迟升序排序
    alive.sort(key=lambda x: x["latency"])
    selected = alive[:max_count]
    print(f"验活完成：可用节点 {len(alive)} 个，选出最优前 {len(selected)} 个。")
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

    return {
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
