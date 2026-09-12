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

# 探测目标：访问 Google gstatic 根目录固定返回 404，能确实验证节点是否具备访问外网能力且无劫持
PROBE_URL = "https://www.gstatic.com/"

def fetch_proxies():
    resp = requests.get(SOURCE_URL, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    return data if isinstance(data, list) else []

def check_single_proxy(item):
    """
    测试单个节点。通过代理访问 https://www.gstatic.com/，
    只有状态码严格为 404 才判定为真实有效的直连代理节点。
    注意：测试 SOCKS 节点需要依赖：pip install "requests[socks]"
    """
    protocol = item.get("protocol")
    ip = item.get("ip")
    port = item.get("port")

    if protocol not in ("http", "socks4", "socks5"):
        return None

    # 对 socks5 推荐使用 socks5h 协议头，让 DNS 解析交由远端代理完成，避免本地 DNS 污染
    proto_prefix = "socks5h" if protocol == "socks5" else protocol
    proxy_url = f"{proto_prefix}://{ip}:{port}"
    proxies = {
        "http": proxy_url,
        "https": proxy_url
    }

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }

    start_time = time.time()
    try:
        # verify=True 校验 Google 的真实 SSL 证书，防止中间人伪造
        r = requests.get(
            PROBE_URL,
            proxies=proxies,
            headers=headers,
            timeout=CHECK_TIMEOUT,
            verify=True
        )
        if r.status_code == 404:
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
                print(f"[VALID] {res['protocol']}://{res['ip']}:{res['port']} - {res['latency']}ms")

    # 按探测延迟升序排序
    alive.sort(key=lambda x: x["latency"])
    selected = alive[:max_count]
    print(f"验活完成：可用真实节点 {len(alive)} 个，写入前 {len(selected)} 个最优节点。")
    return selected

def build_xray_config(proxies):
    # 顶层日志配置
    log = {
        "loglevel": "warning"
    }

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
        "log": log,
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
            print("No valid proxies passed gstatic 404 check, exiting.")
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
